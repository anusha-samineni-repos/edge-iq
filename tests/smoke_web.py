"""
Static checks for the operator console.

Node isn't available in every environment this repo gets cloned into, so we
can't rely on `vite build` to catch mistakes. These checks cover the failures
that a bundler would catch (unresolved imports, missing exports) plus the one
it wouldn't: drift between the SSE event contract the server emits and the one
the client parses. That contract is a plain string on both sides, so nothing
else would notice if they diverged.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "src" / "App"
SRC = APP / "src"
ORCHESTRATOR = REPO / "src" / "api" / "python" / "orchestrator.py"
CHAT = REPO / "src" / "api" / "python" / "chat.py"

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


# --------------------------------------------------------------- structure --

def test_scaffold() -> None:
    print("\nProject scaffold")
    for rel in [
        "package.json",
        "vite.config.js",
        "index.html",
        "src/main.jsx",
        "src/App.jsx",
        "src/api.js",
        "src/styles.css",
    ]:
        check(f"{rel} exists", (APP / rel).is_file())

    check("WebApp.Dockerfile exists (azure.yaml references it)",
          (REPO / "WebApp.Dockerfile").is_file())

    html = (APP / "index.html").read_text(encoding="utf-8")
    check("index.html mounts #root", 'id="root"' in html)
    check("index.html loads the module entrypoint", "/src/main.jsx" in html)


# ----------------------------------------------------------------- imports --

_IMPORT_RE = re.compile(r"""^import\s+(?:[\w*{}\s,]+\s+from\s+)?['"]([^'"]+)['"]""",
                        re.MULTILINE)
_NAMED_RE = re.compile(r"^import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]",
                       re.MULTILINE)
_DEFAULT_RE = re.compile(r"^import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]",
                         re.MULTILINE)


def _resolve(importer: Path, spec: str) -> Path | None:
    if not spec.startswith("."):
        return None  # bare specifier -> package.json's problem
    target = (importer.parent / spec).resolve()
    if target.is_file():
        return target
    for ext in (".js", ".jsx"):
        if (cand := target.with_suffix(ext)).is_file():
            return cand
    return None


def test_imports() -> None:
    print("\nModule graph")
    files = sorted(SRC.rglob("*.jsx")) + sorted(SRC.rglob("*.js"))
    check("source files found", len(files) > 0, f"{len(files)} files")

    deps = set((APP / "package.json").read_text(encoding="utf-8").split())
    pkg_text = (APP / "package.json").read_text(encoding="utf-8")

    unresolved: list[str] = []
    missing_dep: list[str] = []

    for f in files:
        text = f.read_text(encoding="utf-8")
        for spec in _IMPORT_RE.findall(text):
            if spec.startswith("."):
                if spec.endswith(".css"):
                    if not (f.parent / spec).resolve().is_file():
                        unresolved.append(f"{f.name} -> {spec}")
                elif _resolve(f, spec) is None:
                    unresolved.append(f"{f.name} -> {spec}")
            else:
                root = spec.split("/")[0]
                if root.startswith("@"):
                    root = "/".join(spec.split("/")[:2])
                if f'"{root}"' not in pkg_text:
                    missing_dep.append(f"{f.name} -> {spec}")

    check("all relative imports resolve", not unresolved, "; ".join(unresolved))
    check("all bare imports are declared in package.json",
          not missing_dep, "; ".join(missing_dep))

    # Every default-imported local module must actually export a default.
    bad_default: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        for name, spec in _DEFAULT_RE.findall(text):
            if not spec.startswith(".") or spec.endswith(".css"):
                continue
            target = _resolve(f, spec)
            if target and "export default" not in target.read_text(encoding="utf-8"):
                bad_default.append(f"{f.name} imports default from {spec}")
    check("default imports have matching default exports",
          not bad_default, "; ".join(bad_default))

    # And every named import must be exported by name.
    bad_named: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        for names, spec in _NAMED_RE.findall(text):
            if not spec.startswith("."):
                continue
            target = _resolve(f, spec)
            if not target:
                continue
            body = target.read_text(encoding="utf-8")
            for raw in names.split(","):
                name = raw.split(" as ")[0].strip()
                if not name:
                    continue
                if not re.search(rf"export\s+(?:async\s+)?(?:function|const|let|class)\s+{name}\b", body):
                    bad_named.append(f"{f.name} imports {{{name}}} from {spec}")
    check("named imports have matching named exports",
          not bad_named, "; ".join(bad_named))


# ------------------------------------------------------------ SSE contract --

def test_event_contract() -> None:
    """The client must handle exactly the events the orchestrator emits."""
    print("\nSSE contract (client vs orchestrator)")

    server = ORCHESTRATOR.read_text(encoding="utf-8")
    client = (SRC / "App.jsx").read_text(encoding="utf-8")
    api = (SRC / "api.js").read_text(encoding="utf-8")

    emitted = set(re.findall(r'yield\s*\{\s*"type"\s*:\s*"(\w+)"', server))
    check("orchestrator emits the documented event types",
          emitted >= {"status", "route", "delta", "final", "error"},
          f"found {sorted(emitted)}")

    handled = set(re.findall(r"case\s+'(\w+)'\s*:", client))
    missing = emitted - handled
    check("client handles every emitted event type",
          not missing, f"unhandled: {sorted(missing)}")

    stray = handled - emitted
    check("client handles no phantom event types",
          not stray, f"never emitted: {sorted(stray)}")

    # The route event nests its payload - reading event.strategy instead of
    # event.route.strategy is a silent no-op, so assert the nesting explicitly.
    check("client reads the nested route payload", "event.route" in client)

    # Terminator and transport details.
    check("client honours the [DONE] terminator", "[DONE]" in api)
    check("client POSTs to /api/chat", "'/api/chat'" in api)
    check("client requests a stream", '"stream":' in api or "stream: true" in api)

    # The request body keys are camelCase on the wire (ChatRequest model).
    for key in ("conversationId", "maxClassification"):
        check(f"client sends {key}", key in api)


def test_endpoints() -> None:
    """Every endpoint the client calls must exist on the router."""
    print("\nEndpoint coverage")
    chat = CHAT.read_text(encoding="utf-8")
    history = (REPO / "src" / "api" / "python" / "history.py").read_text(encoding="utf-8")
    app = (REPO / "src" / "api" / "python" / "app.py").read_text(encoding="utf-8")
    server = chat + history + app

    api = (SRC / "api.js").read_text(encoding="utf-8")
    called = set(re.findall(r"fetch\(\s*[`'\"](/api/[\w/-]+)", api))
    # Template-literal calls like `/api/conversations/${id}` leave a trailing
    # slash once the interpolation is stripped; normalise it away.
    called |= {
        m.split("$")[0].rstrip("/")
        for m in re.findall(r"fetch\(\s*`(/api/[\w/-]*)", api)
    }
    called.discard("/api")

    for path in sorted(called):
        # Routers declare paths relative to their prefix ("/chat"), while
        # app.py declares absolute ones ("/api/health"). Accept either form.
        suffix = path.replace("/api", "", 1) or "/"
        candidates = [suffix, path]

        # Parameterised routes are declared as "/conversations/{id}"; the
        # client builds them by interpolation, so match on the prefix.
        param_route = re.search(
            rf'["\']{re.escape(suffix.rstrip("/"))}/\{{\w+\}}["\']', server
        )

        present = param_route is not None or any(
            f"{q}{p}{q}" in server for p in candidates for q in ('"', "'")
        )
        check(f"{path} is served", present)


def main() -> int:
    print("Edge IQ - operator console checks")
    test_scaffold()
    test_imports()
    test_event_contract()
    test_endpoints()

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
