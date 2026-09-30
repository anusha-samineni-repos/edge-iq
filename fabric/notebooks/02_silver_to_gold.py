# Fabric Notebook: 02 - Silver to Gold
# =============================================================================
# Edge IQ medallion pipeline, stage 2 of 2.
#
# GOLD = the business-semantic layer. These tables are the physical backing of
# the Fabric IQ ontology in fabric/ontology/water_utility_ontology.yaml, and
# the tables the Fabric Data Agent generates SQL against. The column names
# here MUST match the ontology attribute names exactly - that mapping is the
# whole contract between the data platform and the agent layer.
#
# Every gold table carries data-quality provenance. An agent must always be
# able to answer "how complete was the data you based that on?".
# =============================================================================

# PARAMETERS CELL
lakehouse_name = "EdgeIQ_Lakehouse"
lookback_hours = 72
mnf_baseline_days = 90
min_completeness_pct = 90.0

# -----------------------------------------------------------------------------
from pyspark.sql import functions as F
from pyspark.sql.window import Window
from delta.tables import DeltaTable

spark.conf.set("spark.sql.session.timeZone", "UTC")
spark.conf.set("spark.databricks.delta.schema.autoMerge.enabled", "true")

cutoff = F.expr(f"current_timestamp() - INTERVAL {lookback_hours} HOURS")


def upsert(df, table, keys):
    """Idempotent merge so the notebook can be re-run over any window safely."""
    if spark.catalog.tableExists(table):
        cond = " AND ".join(f"t.{k} = s.{k}" for k in keys)
        (DeltaTable.forName(spark, table).alias("t")
         .merge(df.alias("s"), cond)
         .whenMatchedUpdateAll().whenNotMatchedInsertAll().execute())
    else:
        df.write.format("delta").mode("overwrite").saveAsTable(table)
    print(f"{table}: {df.count():,} rows")


# =============================================================================
# 1. DIMENSIONS  (gold_site, gold_asset, gold_edge_device)
# =============================================================================
# These come from the asset master / CMMS, loaded as reference files or shortcut
# into OneLake. In the demo they are seeded from data/customdata/*.csv.
gold_site = (
    spark.read.format("delta").table("ref_site")
    .select("siteId", "siteName", "siteType", "region",
            F.col("populationServed").cast("int").alias("populationServed"),
            F.col("capacityMld").cast("double").alias("capacityMld"),
            "criticality")
    .dropDuplicates(["siteId"])
)
upsert(gold_site, "gold_site", ["siteId"])

gold_asset = (
    spark.read.format("delta").table("ref_asset")
    .select("assetId", "siteId", "assetClass", "manufacturer", "model",
            F.col("ratedPowerKw").cast("double").alias("ratedPowerKw"),
            F.to_date("installDate").alias("installDate"),
            "criticality", "dutyStandby")
    .dropDuplicates(["assetId"])
    # Resolve the ISO 10816-3 evaluation band once, here, so no agent or tool
    # ever has to guess it from rated power.
    .withColumn(
        "isoVibrationBand",
        F.when(F.col("ratedPowerKw") <= 15, F.lit("small"))
         .when(F.col("ratedPowerKw") <= 75, F.lit("medium"))
         .otherwise(F.lit("large")),
    )
)
upsert(gold_asset, "gold_asset", ["assetId"])

gold_edge_device = (
    spark.read.format("delta").table("ref_device")
    .select("deviceId", "siteId", "assetId", "deviceType", "status",
            "runtimeVersion",
            F.col("lastHeartbeatUtc").cast("timestamp").alias("lastHeartbeatUtc"),
            F.col("certExpiryUtc").cast("timestamp").alias("certExpiryUtc"),
            "deploymentId")
    .dropDuplicates(["deviceId"])
    .withColumn("certDaysRemaining", F.datediff("certExpiryUtc", F.current_date()))
    .withColumn(
        "certRisk",
        F.when(F.col("certDaysRemaining") < 0, F.lit("expired"))
         .when(F.col("certDaysRemaining") <= 7, F.lit("critical"))
         .when(F.col("certDaysRemaining") <= 30, F.lit("warning"))
         .otherwise(F.lit("ok")),
    )
    .withColumn(
        "minutesSinceHeartbeat",
        (F.unix_timestamp(F.current_timestamp()) - F.unix_timestamp("lastHeartbeatUtc")) / 60,
    )
)
upsert(gold_edge_device, "gold_edge_device", ["deviceId"])


# =============================================================================
# 2. gold_telemetry_hourly
# =============================================================================
# Hourly aggregate with mean/min/max/stddev per tag. Agents trend against this
# rather than scanning raw events - it is ~60x smaller and answers every
# question the specialists actually ask.
#
# CRITICAL: aggregate ONLY trustworthy rows, but carry the sample counts so the
# consumer can see how much was excluded. Averaging across a flatlined sensor
# would launder a fault into a healthy-looking number.
silver = spark.read.format("delta").table("silver_telemetry").filter(F.col("timestamp") >= cutoff)

TAGS = ["vibration_mm_s", "bearing_temp_c", "motor_current_a", "power_kw",
        "flow_m3_h", "pressure_bar", "suction_pressure_bar"]

aggs = []
for t in TAGS:
    trusted = F.when(F.col("isTrustworthy"), F.col(t))
    aggs += [
        F.round(F.avg(trusted), 3).alias(t),
        F.round(F.min(trusted), 3).alias(f"{t}_min"),
        F.round(F.max(trusted), 3).alias(f"{t}_max"),
        F.round(F.stddev(trusted), 4).alias(f"{t}_std"),
    ]

gold_telemetry_hourly = (
    silver
    .groupBy(F.date_trunc("hour", "timestamp").alias("timestamp"),
             "deviceId", "assetId", "siteId")
    .agg(
        *aggs,
        F.count("*").alias("sampleCount"),
        F.sum(F.col("isTrustworthy").cast("int")).alias("trustworthyCount"),
        F.max(F.col("runHours")).alias("runHours"),
        F.collect_set(F.when(~F.col("isTrustworthy"), F.col("qualityFlag"))).alias("_flags"),
    )
    .withColumn("qualityFlags", F.array_except(F.col("_flags"), F.array(F.lit(None))))
    .withColumn(
        "completenessPct",
        F.least(F.lit(100.0), F.round(F.col("trustworthyCount") / 60.0 * 100, 1)),
    )
    .withColumn("isTrustworthy", F.col("completenessPct") >= min_completeness_pct)
    .withColumn("ingestDate", F.to_date("timestamp"))
    .drop("_flags")
)
upsert(gold_telemetry_hourly, "gold_telemetry_hourly", ["deviceId", "timestamp"])


# =============================================================================
# 3. gold_water_quality
# =============================================================================
wq_silver = (
    spark.read.format("delta").table("silver_water_quality").filter(F.col("timestamp") >= cutoff)
)
WQ = ["chlorine_mg_l", "turbidity_ntu", "ph", "conductivity_us_cm", "temperature_c"]

wq_aggs = []
for t in WQ:
    trusted = F.when(F.col("isTrustworthy"), F.col(t))
    wq_aggs += [
        F.round(F.avg(trusted), 4).alias(t),
        F.round(F.min(trusted), 4).alias(f"{t}_min"),
        F.round(F.max(trusted), 4).alias(f"{t}_max"),
    ]

gold_water_quality = (
    wq_silver
    .groupBy(F.date_trunc("hour", "timestamp").alias("timestamp"), "deviceId", "siteId")
    .agg(
        *wq_aggs,
        F.count("*").alias("sampleCount"),
        F.sum(F.col("isTrustworthy").cast("int")).alias("trustworthyCount"),
        F.max(F.col("chlorineSensorFlatline").cast("int")).alias("_flat"),
    )
    .withColumn("chlorineSensorSuspect", F.col("_flat") == 1)
    # If the analyser is suspect, the chlorine value is NOT evidence of
    # compliance. Null it and say so, rather than publishing a comforting number.
    .withColumn("chlorine_mg_l",
                F.when(F.col("chlorineSensorSuspect"), F.lit(None).cast("double"))
                 .otherwise(F.col("chlorine_mg_l")))
    .withColumn("isTrustworthy", ~F.col("chlorineSensorSuspect"))
    .withColumn("ingestDate", F.to_date("timestamp"))
    .drop("_flat")
)
upsert(gold_water_quality, "gold_water_quality", ["deviceId", "timestamp"])


# =============================================================================
# 4. gold_dma_flow + gold_dma_mnf  (UC3 leak detection)
# =============================================================================
dma_silver = spark.read.format("delta").table("silver_dma_flow").filter(F.col("timestamp") >= cutoff)

gold_dma_flow = (
    dma_silver
    .groupBy(F.date_trunc("hour", "timestamp").alias("timestamp"), "dmaId", "isNightWindow")
    .agg(
        F.round(F.avg("inflow_m3_h"), 3).alias("inflow_m3_h"),
        F.round(F.avg("pressure_bar"), 3).alias("pressure_bar"),
        F.count("*").alias("sampleCount"),
    )
    .withColumn("ingestDate", F.to_date("timestamp"))
)
upsert(gold_dma_flow, "gold_dma_flow", ["dmaId", "timestamp"])

# Minimum night flow, its rolling baseline, and the excess above baseline.
# This single table is what turns "flow data" into "we have a leak".
full_dma = spark.read.format("delta").table("gold_dma_flow")
nightly = (
    full_dma.filter(F.col("isNightWindow"))
    .groupBy("dmaId", F.to_date("timestamp").alias("date"))
    .agg(F.round(F.avg("inflow_m3_h"), 3).alias("mnf_m3_h"),
         F.round(F.avg("pressure_bar"), 3).alias("nightPressure_bar"))
)

baseline_window = (
    Window.partitionBy("dmaId").orderBy(F.col("date").cast("long"))
          .rangeBetween(-mnf_baseline_days * 86400, -7 * 86400)  # exclude the last week
)

gold_dma_mnf = (
    nightly
    .withColumn("baselineMnf_m3_h", F.round(F.avg("mnf_m3_h").over(baseline_window), 3))
    .withColumn("excessMnf_m3_h",
                F.round(F.col("mnf_m3_h") - F.col("baselineMnf_m3_h"), 3))
    .withColumn(
        "leakIndication",
        F.when(F.col("baselineMnf_m3_h").isNull(), F.lit("insufficient_baseline"))
         .when(F.col("excessMnf_m3_h") > 5.0, F.lit("significant"))
         .when(F.col("excessMnf_m3_h") > 2.0, F.lit("confirmed"))
         .otherwise(F.lit("normal")),
    )
    # Annualised so the finding arrives with a number attached and can be
    # prioritised against other capital and operational work.
    .withColumn("annualLossM3",
                F.round(F.greatest(F.col("excessMnf_m3_h"), F.lit(0.0)) * 8760, 0))
)
upsert(gold_dma_mnf, "gold_dma_mnf", ["dmaId", "date"])


# =============================================================================
# 5. gold_energy  (UC4)
# =============================================================================
# Specific energy consumption - the KPI that normalises out duty variation.
PEAK_RATE, OFFPEAK_RATE, CARBON_KG_KWH = 0.284, 0.112, 0.371

gold_energy = (
    spark.read.format("delta").table("gold_telemetry_hourly")
    .filter(F.col("power_kw").isNotNull() & (F.col("flow_m3_h") > 0))
    .withColumn("hour", F.hour("timestamp"))
    .withColumn("dayOfWeek", F.dayofweek("timestamp"))
    .withColumn(
        "tariffPeriod",
        F.when(
            (F.col("dayOfWeek").between(2, 6))
            & (F.col("hour").between(7, 10) | F.col("hour").between(17, 20)),
            F.lit("peak"),
        ).otherwise(F.lit("offpeak")),
    )
    .withColumn("energyKwh", F.col("power_kw"))          # 1-hour buckets
    .withColumn("volumeM3", F.col("flow_m3_h"))
    .withColumn("specificEnergyKwhM3", F.round(F.col("energyKwh") / F.col("volumeM3"), 5))
    .withColumn("tariffRate",
                F.when(F.col("tariffPeriod") == "peak", F.lit(PEAK_RATE)).otherwise(F.lit(OFFPEAK_RATE)))
    .withColumn("costUsd", F.round(F.col("energyKwh") * F.col("tariffRate"), 4))
    .withColumn("carbonKgCo2e", F.round(F.col("energyKwh") * F.lit(CARBON_KG_KWH), 4))
    .select("timestamp", "deviceId", "assetId", "siteId", "tariffPeriod",
            "energyKwh", "volumeM3", "specificEnergyKwhM3", "tariffRate",
            "costUsd", "carbonKgCo2e", "ingestDate")
)
upsert(gold_energy, "gold_energy", ["deviceId", "timestamp"])


# =============================================================================
# 6. gold_asset_health  (UC1 - the ISO 10816-3 scorecard)
# =============================================================================
# One row per asset with the current vibration severity, its trend, and the
# projected date it crosses the next zone boundary. This is what makes the
# asset-health agent able to say "act within N days" instead of "vibration is high".
recent = (
    spark.read.format("delta").table("gold_telemetry_hourly")
    .filter(F.col("timestamp") >= F.expr("current_timestamp() - INTERVAL 14 DAYS"))
    .filter(F.col("vibration_mm_s").isNotNull() & F.col("isTrustworthy"))
)

trend_window = Window.partitionBy("assetId").orderBy(F.col("timestamp").cast("long"))
slope = (
    recent
    .withColumn("t_days", F.col("timestamp").cast("long") / 86400.0)
    .groupBy("assetId", "deviceId", "siteId")
    .agg(
        F.round(F.avg("vibration_mm_s"), 3).alias("meanVibration"),
        F.round(F.max("vibration_mm_s"), 3).alias("peakVibration"),
        F.round(F.avg("bearing_temp_c"), 2).alias("meanBearingTemp"),
        F.count("*").alias("hoursObserved"),
        # least-squares slope in mm/s per day
        F.round(
            (F.count("*") * F.sum(F.col("t_days") * F.col("vibration_mm_s"))
             - F.sum("t_days") * F.sum("vibration_mm_s"))
            / F.nullif(
                F.count("*") * F.sum(F.col("t_days") * F.col("t_days"))
                - F.sum("t_days") * F.sum("t_days"), F.lit(0)),
            4,
        ).alias("vibrationSlopePerDay"),
    )
)

ZONES = {  # ISO 10816-3 rigid mounting, (A/B, B/C, C/D)
    "small":  (1.4, 2.8, 4.5),
    "medium": (2.3, 4.5, 7.1),
    "large":  (3.5, 7.1, 11.0),
}

banded = slope.join(gold_asset.select("assetId", "ratedPowerKw", "isoVibrationBand",
                                      "assetClass", "criticality"), "assetId", "left")

zone_expr = F.lit("unknown")
next_bound = F.lit(None).cast("double")
for band, (ab, bc, cd) in ZONES.items():
    m = F.col("isoVibrationBand") == band
    zone_expr = F.when(
        m,
        F.when(F.col("peakVibration") >= cd, F.lit("D"))
         .when(F.col("peakVibration") >= bc, F.lit("C"))
         .when(F.col("peakVibration") >= ab, F.lit("B"))
         .otherwise(F.lit("A")),
    ).otherwise(zone_expr)
    next_bound = F.when(
        m,
        F.when(F.col("peakVibration") >= bc, F.lit(cd))
         .when(F.col("peakVibration") >= ab, F.lit(bc))
         .otherwise(F.lit(ab)),
    ).otherwise(next_bound)

gold_asset_health = (
    banded
    .withColumn("isoZone", zone_expr)
    .withColumn("nextZoneBoundary", next_bound)
    .withColumn(
        "daysToNextZone",
        F.when(
            F.col("vibrationSlopePerDay") > 0.001,
            F.round((F.col("nextZoneBoundary") - F.col("peakVibration"))
                    / F.col("vibrationSlopePerDay"), 0),
        ),
    )
    .withColumn(
        "healthVerdict",
        F.when(F.col("isoZone") == "D", F.lit("act_now"))
         .when((F.col("isoZone") == "C") & (F.col("vibrationSlopePerDay") > 0.05),
               F.lit("act_within_7_days"))
         .when(F.col("isoZone") == "C", F.lit("plan_within_30_days"))
         .when((F.col("isoZone") == "B") & (F.col("vibrationSlopePerDay") > 0.05),
               F.lit("monitor_daily"))
         .otherwise(F.lit("healthy")),
    )
    .withColumn("assessedUtc", F.current_timestamp())
)
upsert(gold_asset_health, "gold_asset_health", ["assetId"])


# =============================================================================
# 7. REFERENCE PASSTHROUGH
# =============================================================================
# Reference tables the ontology declares. Seeded from data/customdata and
# refreshed from the CMMS / regulatory register in production.
for src, dst, keys in [
    ("ref_failure_mode",     "gold_failure_mode",     ["assetClass", "failureMode"]),
    ("ref_regulatory_limit", "gold_regulatory_limit", ["regime", "parameter"]),
    ("ref_work_order",       "gold_work_order",       ["workOrderId"]),
    ("ref_alarm",            "gold_alarm",            ["alarmId"]),
    ("ref_spare_part",       "gold_spare_part",       ["partNumber"]),
]:
    if spark.catalog.tableExists(src):
        upsert(spark.read.format("delta").table(src), dst, keys)
    else:
        print(f"skip {dst}: source {src} not present")


# =============================================================================
# 8. OPTIMISE + VERIFY
# =============================================================================
GOLD = ["gold_site", "gold_asset", "gold_edge_device", "gold_telemetry_hourly",
        "gold_water_quality", "gold_dma_flow", "gold_dma_mnf", "gold_energy",
        "gold_asset_health"]

for t in GOLD:
    if spark.catalog.tableExists(t):
        spark.sql(f"OPTIMIZE {t}")

print("\n=== GOLD LAYER SUMMARY ===")
for t in GOLD:
    if spark.catalog.tableExists(t):
        print(f"  {t:<28} {spark.table(t).count():>10,} rows")

print("\nSilver -> Gold complete. These tables back the Fabric IQ ontology.")
