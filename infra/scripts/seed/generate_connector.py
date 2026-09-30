#!/usr/bin/env python3
"""
Generate the deployment-ready Copilot Studio connector and agent manifest.

``copilot-studio/connectors/edgeiq-openapi.yaml`` is the authored source and
deliberately contains ``<PLACEHOLDER>`` tokens so it is safe to commit. This
script substitutes the real values from the azd environment (or explicit
flags) and writes the result to ``copilot-studio/generated/``, which is
gitignored.

The same substitution is applied to the declarative agent so the M365 sources
(SharePoint sites, Teams channels, Graph connectors) point at the customer's
actual tenant.

Usage
-----
    python generate_connector.py                     # uses env vars
    python generate_connector.py --api-host edgeiq-api.x.azurecontainerapps.io \
                                 --tenant-id <guid> --api-client-id <guid>
    python generate_connector.py --report            # list unresolved tokens
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CS_DIR = REPO_ROOT / "copilot-studio"
OUT_DIR = CS_DIR / "generated"

SOURCES = [
    (CS_DIR / "connectors" / "edgeiq-openapi.yaml", OUT_DIR / "edgeiq-connector.yaml"),
    (CS_DIR / "connectors" / "apiProperties.json", OUT_DIR / "apiProperties.json"),
    (CS_DIR / "declarative-agent.json", OUT_DIR / "declarative-agent.json"),
]

TOKEN_RE = re.compile(r"<([A-Z0-9_]+)>")


def build_substitutions(args: argparse.Namespace) -> dict[str, str]:
    """Map placeholder token -> value, from flags first then the environment.

    Every value is optional. Unresolved tokens are left in place and reported,
    so a partially-configured environment still produces a usable file that
    clearly shows what is still missing.
    """
    def pick(flag: str | None, *env_keys: str) -> str:
        if flag:
            return flag
        for key in env_keys:
            value = os.getenv(key, "").strip()
            if value:
                return value
        return ""

    api_host = pick(args.api_host, "API_APP_URL", "SERVICE_API_URI", "EDGEIQ_API_HOST")
    api_host = api_host.replace("https://", "").replace("http://", "").rstrip("/")

    console = pick(args.console_url, "WEB_APP_URL", "SERVICE_WEB_URI")

    return {k: v for k, v in {
        "API_HOST": api_host,
        "TENANT_ID": pick(args.tenant_id, "AZURE_TENANT_ID", "M365_TENANT_ID"),
        "API_CLIENT_ID": pick(args.api_client_id, "AZURE_CLIENT_ID", "M365_CLIENT_ID"),
        "TENANT": pick(args.tenant_name, "M365_TENANT_NAME"),
        "ORGANISATION_NAME": pick(args.organisation, "EDGEIQ_ORGANISATION"),
        "CONSOLE_URL": console,
        "GRAPH_CONNECTOR_ID_SOPS": pick(None, "M365_GRAPH_CONNECTOR_ID"),
        "GRAPH_CONNECTOR_ID_ASSET_DOCS": pick(None, "M365_GRAPH_CONNECTOR_ASSETS_ID"),
        "CONTROL_ROOM_TEAM_ID": pick(None, "M365_CONTROL_ROOM_TEAM_ID"),
        "MAINTENANCE_TEAM_ID": pick(None, "M365_MAINTENANCE_TEAM_ID"),
    }.items() if v}


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the Copilot Studio package.")
    ap.add_argument("--api-host", default="")
    ap.add_argument("--tenant-id", default="")
    ap.add_argument("--api-client-id", default="")
    ap.add_argument("--tenant-name", default="")
    ap.add_argument("--organisation", default="")
    ap.add_argument("--console-url", default="")
    ap.add_argument("--report", action="store_true",
                    help="Only report which tokens would remain unresolved.")
    args = ap.parse_args()

    subs = build_substitutions(args)

    print("Edge IQ - Copilot Studio package")
    if subs:
        for key, value in sorted(subs.items()):
            shown = value if len(value) <= 52 else value[:49] + "..."
            print(f"  {key:<32} {shown}")
    else:
        print("  no values resolved - output will keep every placeholder")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    unresolved: set[str] = set()
    written = 0

    for src, dst in SOURCES:
        if not src.exists():
            print(f"  SKIP  {src.relative_to(REPO_ROOT)} (not found)")
            continue
        text = src.read_text(encoding="utf-8")
        for token, value in subs.items():
            text = text.replace(f"<{token}>", value)
        unresolved.update(TOKEN_RE.findall(text))

        if not args.report:
            dst.write_text(text, encoding="utf-8")
            written += 1
            print(f"  wrote {dst.relative_to(REPO_ROOT)}")

    if unresolved:
        print("\n  Unresolved placeholders (set these before importing):")
        for token in sorted(unresolved):
            print(f"    <{token}>")
        print("\n  These are M365/tenant values that cannot be derived from the "
              "Azure deployment.\n  See docs/work-iq-setup.md for where to find each one.")
    else:
        print("\n  All placeholders resolved.")

    if not args.report:
        print(f"\n{written} file(s) written to {OUT_DIR.relative_to(REPO_ROOT)}/")
        print("Import edgeiq-connector.yaml in the Power Platform admin centre, "
              "then upload declarative-agent.json to Copilot Studio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
