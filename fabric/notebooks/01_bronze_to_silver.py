# Fabric Notebook: 01 - Bronze to Silver
# =============================================================================
# Edge IQ medallion pipeline, stage 1 of 2.
#
# BRONZE = raw, append-only, exactly as it arrived from the eventstream.
# SILVER = cleansed, deduplicated, typed, quality-flagged. One row per
#          device per tag per event-time, with known-bad data MARKED rather
#          than deleted.
#
# WHY WE MARK RATHER THAN DELETE: a flatlined analyser and a healthy analyser
# look identical in a mean. The single most important thing this pipeline does
# is preserve the distinction between "we measured this" and "we did not know".
#
# HOW TO RUN: attach to EdgeIQ_Lakehouse and run all. Scheduled hourly by the
# EdgeIQ_Medallion pipeline. Parameterised for the Fabric pipeline activity.
# =============================================================================

# PARAMETERS CELL -- tag this cell as "Parameters" in the Fabric notebook UI
lakehouse_name = "EdgeIQ_Lakehouse"
lookback_hours = 72          # reprocess window; wide enough to absorb edge replay
flatline_std_threshold = 0.005
stale_multiplier = 3         # missed sampling intervals before a tag is stale
min_completeness_pct = 90.0

# -----------------------------------------------------------------------------
from pyspark.sql import functions as F
from pyspark.sql.window import Window
from delta.tables import DeltaTable

spark.conf.set("spark.sql.session.timeZone", "UTC")
spark.conf.set("spark.databricks.delta.schema.autoMerge.enabled", "true")

cutoff = F.expr(f"current_timestamp() - INTERVAL {lookback_hours} HOURS")

# =============================================================================
# 1. TELEMETRY: bronze -> silver
# =============================================================================
bronze = (
    spark.read.format("delta").table("bronze_telemetry")
    .filter(F.col("eventTimeUtc") >= cutoff)
)

# --- Flatten the measurement map into columns -------------------------------
TAGS = [
    "vibration_mm_s", "bearing_temp_c", "motor_current_a", "power_kw",
    "flow_m3_h", "pressure_bar", "suction_pressure_bar", "runHours",
]

flat = bronze.select(
    F.col("eventTimeUtc").cast("timestamp").alias("timestamp"),
    F.col("enqueuedUtc").cast("timestamp").alias("ingestedUtc"),
    F.col("deviceId"), F.col("assetId"), F.col("siteId"),
    F.coalesce(F.col("isReplay"), F.lit(False)).alias("isReplay"),
    F.coalesce(F.col("schemaVersion"), F.lit("1.0")).alias("schemaVersion"),
    *[F.col(f"measurements.{t}").cast("double").alias(t) for t in TAGS],
)

# --- Deduplicate -------------------------------------------------------------
# Edge store-and-forward replays on reconnect, so the same event can arrive
# more than once. Keep the LATEST ingested copy of each (device, timestamp):
# a corrected re-send supersedes the original.
dedup_window = Window.partitionBy("deviceId", "timestamp").orderBy(F.col("ingestedUtc").desc())
deduped = (
    flat.withColumn("_rn", F.row_number().over(dedup_window))
        .filter(F.col("_rn") == 1)
        .drop("_rn")
)

# --- Physical plausibility bounds -------------------------------------------
# Out-of-range values are nulled and flagged, never clamped. Clamping invents
# data; nulling preserves the truth that the reading was unusable.
BOUNDS = {
    "vibration_mm_s":       (0.0, 60.0),
    "bearing_temp_c":       (-20.0, 200.0),
    "motor_current_a":      (0.0, 2000.0),
    "power_kw":             (0.0, 5000.0),
    "flow_m3_h":            (0.0, 20000.0),
    "pressure_bar":         (-1.0, 40.0),
    "suction_pressure_bar": (-1.0, 40.0),
}

bounded = deduped
out_of_range = F.lit(False)
for tag, (lo, hi) in BOUNDS.items():
    bad = F.col(tag).isNotNull() & (~F.col(tag).between(lo, hi))
    out_of_range = out_of_range | bad
    bounded = bounded.withColumn(tag, F.when(bad, F.lit(None).cast("double")).otherwise(F.col(tag)))
bounded = bounded.withColumn("hadOutOfRange", out_of_range)

# --- Sensor validity: flatline detection ------------------------------------
# A rolling standard deviation collapsing toward zero means the signal is
# frozen. This is the check that stops Edge IQ reporting a dead chlorine
# analyser as "perfectly compliant".
vib_window = (
    Window.partitionBy("deviceId")
          .orderBy(F.col("timestamp").cast("long"))
          .rangeBetween(-86400, 0)          # trailing 24 hours
)
validated = (
    bounded
    .withColumn("vibration_std_24h", F.stddev("vibration_mm_s").over(vib_window))
    .withColumn("sampleCount_24h", F.count("vibration_mm_s").over(vib_window))
    .withColumn(
        "isFlatline",
        (F.col("sampleCount_24h") >= 12)
        & F.col("vibration_std_24h").isNotNull()
        & (F.col("vibration_std_24h") < flatline_std_threshold),
    )
)

# --- Quality verdict ---------------------------------------------------------
silver_telemetry = (
    validated
    .withColumn(
        "qualityFlag",
        F.when(F.col("isFlatline"), F.lit("suspect_flatline"))
         .when(F.col("hadOutOfRange"), F.lit("suspect_range"))
         .when(F.col("isReplay"), F.lit("replayed"))
         .otherwise(F.lit("good")),
    )
    .withColumn("isTrustworthy", F.col("qualityFlag").isin("good", "replayed"))
    .withColumn("ingestDate", F.to_date("timestamp"))
    .withColumn("silverProcessedUtc", F.current_timestamp())
    .drop("vibration_std_24h", "sampleCount_24h", "hadOutOfRange")
)

# --- Idempotent merge --------------------------------------------------------
# MERGE not append, so re-running the notebook over the same window is safe.
if spark.catalog.tableExists("silver_telemetry"):
    (
        DeltaTable.forName(spark, "silver_telemetry").alias("t")
        .merge(silver_telemetry.alias("s"), "t.deviceId = s.deviceId AND t.timestamp = s.timestamp")
        .whenMatchedUpdateAll()
        .whenNotMatchedInsertAll()
        .execute()
    )
else:
    (
        silver_telemetry.write.format("delta")
        .partitionBy("ingestDate")
        .mode("overwrite")
        .saveAsTable("silver_telemetry")
    )

print(f"silver_telemetry: merged {silver_telemetry.count():,} rows")

# =============================================================================
# 2. WATER QUALITY: bronze -> silver
# =============================================================================
WQ_TAGS = ["chlorine_mg_l", "turbidity_ntu", "ph", "conductivity_us_cm", "temperature_c"]
WQ_BOUNDS = {
    "chlorine_mg_l":      (0.0, 10.0),
    "turbidity_ntu":      (0.0, 1000.0),
    "ph":                 (0.0, 14.0),
    "conductivity_us_cm": (0.0, 10000.0),
    "temperature_c":      (-5.0, 60.0),
}

wq = (
    spark.read.format("delta").table("bronze_water_quality")
    .filter(F.col("eventTimeUtc") >= cutoff)
    .select(
        F.col("eventTimeUtc").cast("timestamp").alias("timestamp"),
        F.col("enqueuedUtc").cast("timestamp").alias("ingestedUtc"),
        "deviceId", "siteId",
        *[F.col(f"measurements.{t}").cast("double").alias(t) for t in WQ_TAGS],
    )
)

wq_dedup = (
    wq.withColumn("_rn", F.row_number().over(
        Window.partitionBy("deviceId", "timestamp").orderBy(F.col("ingestedUtc").desc())))
      .filter(F.col("_rn") == 1).drop("_rn")
)

for tag, (lo, hi) in WQ_BOUNDS.items():
    wq_dedup = wq_dedup.withColumn(
        tag, F.when(F.col(tag).between(lo, hi), F.col(tag)).otherwise(F.lit(None).cast("double"))
    )

# Chlorine flatline is the highest-consequence sensor fault in the whole
# solution - it silently defeats compliance monitoring. Detect it explicitly.
cl2_window = (
    Window.partitionBy("deviceId").orderBy(F.col("timestamp").cast("long")).rangeBetween(-86400, 0)
)
silver_water_quality = (
    wq_dedup
    .withColumn("cl2_std_24h", F.stddev("chlorine_mg_l").over(cl2_window))
    .withColumn("cl2_n_24h", F.count("chlorine_mg_l").over(cl2_window))
    .withColumn(
        "chlorineSensorFlatline",
        (F.col("cl2_n_24h") >= 12)
        & F.col("cl2_std_24h").isNotNull()
        & (F.col("cl2_std_24h") < flatline_std_threshold),
    )
    .withColumn(
        "qualityFlag",
        F.when(F.col("chlorineSensorFlatline"), F.lit("suspect_flatline")).otherwise(F.lit("good")),
    )
    .withColumn("isTrustworthy", F.col("qualityFlag") == "good")
    .withColumn("ingestDate", F.to_date("timestamp"))
    .withColumn("silverProcessedUtc", F.current_timestamp())
    .drop("cl2_std_24h", "cl2_n_24h")
)

if spark.catalog.tableExists("silver_water_quality"):
    (
        DeltaTable.forName(spark, "silver_water_quality").alias("t")
        .merge(silver_water_quality.alias("s"),
               "t.deviceId = s.deviceId AND t.timestamp = s.timestamp")
        .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()
    )
else:
    (silver_water_quality.write.format("delta").partitionBy("ingestDate")
     .mode("overwrite").saveAsTable("silver_water_quality"))

print(f"silver_water_quality: merged {silver_water_quality.count():,} rows")

# =============================================================================
# 3. DMA FLOW: bronze -> silver (with the night-window flag)
# =============================================================================
dma = (
    spark.read.format("delta").table("bronze_dma_flow")
    .filter(F.col("eventTimeUtc") >= cutoff)
    .select(
        F.col("eventTimeUtc").cast("timestamp").alias("timestamp"),
        F.col("enqueuedUtc").cast("timestamp").alias("ingestedUtc"),
        F.coalesce(F.col("body.dmaId"), F.col("siteId")).alias("dmaId"),
        F.col("measurements.flow_m3_h").cast("double").alias("inflow_m3_h"),
        F.col("measurements.pressure_bar").cast("double").alias("pressure_bar"),
    )
)

silver_dma_flow = (
    dma.withColumn("_rn", F.row_number().over(
        Window.partitionBy("dmaId", "timestamp").orderBy(F.col("ingestedUtc").desc())))
       .filter(F.col("_rn") == 1).drop("_rn")
       # 02:00-04:00 is the minimum night flow assessment window
       .withColumn("isNightWindow", F.hour("timestamp").between(2, 3))
       .withColumn("ingestDate", F.to_date("timestamp"))
       .withColumn("silverProcessedUtc", F.current_timestamp())
)

if spark.catalog.tableExists("silver_dma_flow"):
    (
        DeltaTable.forName(spark, "silver_dma_flow").alias("t")
        .merge(silver_dma_flow.alias("s"), "t.dmaId = s.dmaId AND t.timestamp = s.timestamp")
        .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()
    )
else:
    (silver_dma_flow.write.format("delta").partitionBy("ingestDate")
     .mode("overwrite").saveAsTable("silver_dma_flow"))

print(f"silver_dma_flow: merged {silver_dma_flow.count():,} rows")

# =============================================================================
# 4. DATA COMPLETENESS LEDGER
# =============================================================================
# The guard metric. Every agent conclusion is qualified by the completeness of
# the window it was drawn from - so completeness has to be a first-class,
# queryable fact, not something recomputed ad hoc.
completeness = (
    silver_telemetry
    .groupBy("deviceId", "assetId", "siteId", F.date_trunc("hour", "timestamp").alias("hour"))
    .agg(
        F.count("*").alias("receivedSamples"),
        F.sum(F.col("isTrustworthy").cast("int")).alias("trustworthySamples"),
    )
    .withColumn("expectedSamples", F.lit(60))   # 60s sampling -> 60 per hour
    .withColumn(
        "completenessPct",
        F.least(F.lit(100.0), F.round(F.col("receivedSamples") / F.col("expectedSamples") * 100, 1)),
    )
    .withColumn(
        "verdict",
        F.when(F.col("completenessPct") >= min_completeness_pct, F.lit("sufficient"))
         .when(F.col("completenessPct") > 0, F.lit("degraded"))
         .otherwise(F.lit("no_data")),
    )
    .withColumn("ingestDate", F.to_date("hour"))
)

if spark.catalog.tableExists("silver_data_completeness"):
    (
        DeltaTable.forName(spark, "silver_data_completeness").alias("t")
        .merge(completeness.alias("s"), "t.deviceId = s.deviceId AND t.hour = s.hour")
        .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute()
    )
else:
    (completeness.write.format("delta").partitionBy("ingestDate")
     .mode("overwrite").saveAsTable("silver_data_completeness"))

print(f"silver_data_completeness: merged {completeness.count():,} rows")

# =============================================================================
# 5. OPTIMISE
# =============================================================================
for table in ["silver_telemetry", "silver_water_quality",
              "silver_dma_flow", "silver_data_completeness"]:
    spark.sql(f"OPTIMIZE {table}")
    spark.sql(f"VACUUM {table} RETAIN 168 HOURS")

print("Bronze -> Silver complete.")
