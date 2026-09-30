"""
Verify the container image layout resolves the same paths the app expects.

The API and MCP modules both derive the repo root by walking up from their own
file. That makes the directory depth inside the image load-bearing: flatten
src/api/python to /app and foundry_iq.py resolves above the filesystem root,
losing the knowledge corpus silently - the app still starts, it just answers
worse. This test catches that before a deploy does.

It parses the two Dockerfiles, replays their COPY instructions into a temp
directory, and asserts that every grounding asset lands where the code looks
for it.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

COPY_RE = re.compile(r"^COPY\s+(?!--)(\S+)\s+(\S+)\s*$", re.MULTILINE)
ENV_RE = re.compile(r"^ENV\s+(.+?)(?=^(?:[A-Z]{2,}|\s*$))", re.MULTILINE | re.DOTALL)

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if ok:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        failures.append(label)


def stage(dockerfile: Path, root: Path) -> None:
    """Replay the Dockerfile's COPY instructions into `root`."""
    text = dockerfile.read_text(encoding="utf-8")
    for src, dst in COPY_RE.findall(text):
        source = REPO / src
        if not source.exists():
            failures.append(f"{dockerfile.name}: COPY source missing: {src}")
            continue
        target = root / dst.lstrip("/")
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def env_of(dockerfile: Path) -> dict[str, str]:
    """Extract ENV key=value pairs (handles backslash continuations)."""
    text = dockerfile.read_text(encoding="utf-8")
    joined = text.replace("\\\n", " ")
    env: dict[str, str] = {}
    for line in joined.splitlines():
        line = line.strip()
        if not line.startswith("ENV "):
            continue
        for pair in re.findall(r"([A-Z0-9_]+)=(\S+)", line[4:]):
            env[pair[0]] = pair[1]
    return env


# --------------------------------------------------------------------------- #

def test_api_image() -> None:
    print("\nApiApp.Dockerfile")
    dockerfile = REPO / "ApiApp.Dockerfile"
    env = env_of(dockerfile)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stage(dockerfile, root)

        pythonpath = env.get("PYTHONPATH", "")
        check("PYTHONPATH is set", bool(pythonpath))

        foundry = root / pythonpath.lstrip("/") / "iq" / "foundry_iq.py"
        check("foundry_iq.py on PYTHONPATH", foundry.exists(), str(foundry))

        fabric = root / pythonpath.lstrip("/") / "iq" / "fabric_iq.py"
        check("fabric_iq.py on PYTHONPATH", fabric.exists())

        app_py = root / pythonpath.lstrip("/") / "app.py"
        check("app.py importable as 'app'", app_py.exists())

        # foundry_iq.py: _REPO_ROOT = parents[4] -> documents/knowledge-base
        if foundry.exists():
            derived = foundry.resolve().parents[4]
            corpus = derived / "documents" / "knowledge-base"
            check("foundry_iq resolves the knowledge corpus",
                  corpus.is_dir() and any(corpus.glob("*.json")),
                  f"looked in {corpus}")

        # fabric_iq.py: _REPO_ROOT = parents[4] -> ontology + demo data
        if fabric.exists():
            derived = fabric.resolve().parents[4]
            ontology = derived / env.get(
                "FABRIC_ONTOLOGY_PATH",
                "fabric/ontology/water_utility_ontology.yaml",
            ).lstrip("/")
            check("fabric_iq resolves the ontology", ontology.is_file(),
                  f"looked at {ontology}")

            demo = derived / env.get("EDGEIQ_DEMO_DATA_PATH", "data/customdata").lstrip("/")
            check("fabric_iq resolves the demo dataset",
                  demo.is_dir() and any(demo.glob("*.csv")),
                  f"looked in {demo}")

        check("gunicorn config present", (root / "app" / "gunicorn.conf.py").is_file())


def test_mcp_image() -> None:
    print("\nMcpApp.Dockerfile")
    dockerfile = REPO / "McpApp.Dockerfile"
    env = env_of(dockerfile)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stage(dockerfile, root)

        pythonpath = env.get("PYTHONPATH", "")
        gateway = root / pythonpath.lstrip("/") / "mcp_servers" / "gateway.py"
        check("gateway.py importable as 'mcp_servers.gateway'", gateway.exists(),
              str(gateway))

        datasource = root / pythonpath.lstrip("/") / "mcp_servers" / "datasource.py"
        check("datasource.py present", datasource.exists())

        # datasource.py: _ROOT = parents[2] -> data/customdata
        if datasource.exists():
            derived = datasource.resolve().parents[2]
            demo = derived / "data" / "customdata"
            check("datasource resolves the demo dataset (default path)",
                  demo.is_dir() and any(demo.glob("*.csv")),
                  f"looked in {demo}")

        # The explicit override must also point somewhere real.
        override = env.get("EDGEIQ_DEMO_DATA_PATH", "")
        if override.startswith("/"):
            target = root / override.lstrip("/")
            check("EDGEIQ_DEMO_DATA_PATH override is populated",
                  target.is_dir() and any(target.glob("*.csv")),
                  f"looked in {target}")

        servers = root / pythonpath.lstrip("/") / "mcp_servers" / "servers"
        found = sorted(p.stem for p in servers.glob("*.py")
                       if p.stem != "__init__") if servers.is_dir() else []
        check("all 5 MCP servers present", len(found) == 5, f"found {found}")

        compat = root / pythonpath.lstrip("/") / "mcp_servers" / "compat.py"
        check("compat shim present (MCP SDK v1/v2)", compat.exists())


def main() -> int:
    print("Edge IQ - container layout check")
    test_api_image()
    test_mcp_image()

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("\nFAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
