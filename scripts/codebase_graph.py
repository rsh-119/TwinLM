"""Validate (and refresh) the codebase graph in docs/codebase-graph.json.

    python scripts/codebase_graph.py            # check; exit 1 on problems
    python scripts/codebase_graph.py --update   # refresh generated parts + source stamp

Curated nodes/edges (purpose text, calls, reads/writes, tests, config
dependencies) are written by hand. Generated parts (`"auto": true`) come
from the AST: module imports between `app` modules and FastAPI routes.
FastAPI wiring that the AST can't see (conditional `include_router`,
dependency overrides, lru_cache singletons) lives in `annotations`.

The graph is an index, not proof that the code works.
"""
import argparse
import ast
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "docs" / "codebase-graph.json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
VERIFIED_BY_REFERENCE = {"calls", "reads", "writes", "tests", "configures", "routes_provider"}


def _tree(path: str) -> ast.Module:
    return ast.parse((ROOT / path).read_text(encoding="utf-8"))


def _module_path(module: str) -> str:
    return module.replace(".", "/") + ".py"


def app_modules() -> list:
    return sorted(
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / "app").rglob("*.py")
        if p.name != "__init__.py" or p.stat().st_size
    )


def defined_symbols(path: str) -> set:
    """Top-level defs/classes/assignments, plus `Class.method`."""
    names = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if isinstance(node, ast.ClassDef):
                names.update(
                    f"{node.name}.{item.name}" for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _scope(path: str, symbol=None) -> ast.AST:
    """The def/class named `symbol` ("Class.method" allowed), else the module."""
    scope = _tree(path)
    for part in (symbol.split(".") if symbol else []):
        scope = next((n for n in getattr(scope, "body", [])
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part), scope)
    return scope


def referenced_names(path: str, symbol=None) -> set:
    names = set()
    for node in ast.walk(_scope(path, symbol)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.rsplit(".", 1)[-1])
            if node.asname:
                names.add(node.asname)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 80:
            names.add(node.value)
    return names


def imported_modules(path: str) -> set:
    """app.* modules imported by `path` (module-level or inside functions)."""
    found = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("app"))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
            for alias in node.names:
                sub = f"{node.module}.{alias.name}"
                found.add(sub if (ROOT / _module_path(sub)).exists() else node.module)
    return {m for m in found if (ROOT / _module_path(m)).exists()}


def discover_routes() -> list:
    """(METHOD path, module, handler) for every decorated FastAPI route."""
    routes = []
    for path in app_modules():
        tree = _tree(path)
        prefixes = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                if getattr(func, "id", getattr(func, "attr", None)) in ("APIRouter", "FastAPI"):
                    prefix = next((k.value.value for k in node.value.keywords
                                   if k.arg == "prefix" and isinstance(k.value, ast.Constant)), "")
                    for target in node.targets:
                        prefixes[target.id] = prefix
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                        and deco.func.attr in HTTP_METHODS and isinstance(deco.func.value, ast.Name)
                        and deco.func.value.id in prefixes and deco.args
                        and isinstance(deco.args[0], ast.Constant)):
                    full = prefixes[deco.func.value.id] + deco.args[0].value
                    routes.append((f"{deco.func.attr.upper()} {full or '/'}", path, node.name))
    return sorted(routes)


def generated(nodes_by_id: dict) -> tuple:
    nodes, edges = [], []
    for method_path, path, handler in discover_routes():
        rid = f"route:{method_path}"
        old = nodes_by_id.get(rid, {})
        nodes.append({"id": rid, "kind": "route", "path": path, "symbol": handler,
                      "purpose": old.get("purpose", ""), "auto": True})
        edges.append({"from": rid, "to": _module_id(path), "type": "routes_to",
                      "note": handler, "auto": True})
    for path in app_modules():
        for module in sorted(imported_modules(path)):
            if module != _module_id(path):
                edges.append({"from": _module_id(path), "to": module, "type": "imports", "auto": True})
    return nodes, edges


def _module_id(path: str) -> str:
    return path[:-3].replace("/", ".").removesuffix(".__init__")


def fingerprint() -> str:
    digest = hashlib.sha256()
    files = sorted(
        p for pattern in ("app/**/*.py", "tests/*.py", "gateway.toml", "docker-compose.yml", "requirements*.txt")
        for p in ROOT.glob(pattern)
    )
    for path in files:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:16]


def source_stamp() -> dict:
    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    return {
        "revision": git("rev-parse", "HEAD"),
        "working_tree_dirty": bool(git("status", "--porcelain")),
        "fingerprint": fingerprint(),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def check(graph: dict) -> tuple:
    errors, warnings = [], []
    nodes = {n["id"]: n for n in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]):
        errors.append("duplicate node ids")
    symbols_cache, refs_cache = {}, {}
    for node in graph["nodes"]:
        path = node.get("path")
        if path and not (ROOT / path).exists():
            errors.append(f"stale path: {node['id']} -> {path}")
            continue
        symbol = node.get("symbol")
        if symbol and path and path.endswith(".py"):
            symbols = symbols_cache.setdefault(path, defined_symbols(path))
            if symbol not in symbols:
                errors.append(f"stale symbol: {node['id']} -> {path}:{symbol}")
        if not node.get("purpose") and node["kind"] != "route":
            warnings.append(f"node without purpose: {node['id']}")

    for module_path in app_modules():
        if _module_id(module_path) not in nodes:
            errors.append(f"missing node: module {_module_id(module_path)} ({module_path})")

    for edge in graph["edges"]:
        for end in ("from", "to"):
            if edge[end] not in nodes:
                errors.append(f"edge endpoint is not a node: {edge}")
        if edge["type"] in VERIFIED_BY_REFERENCE and not edge.get("inferred") and edge["from"] in nodes and edge["to"] in nodes:
            src, dst = nodes[edge["from"]], nodes[edge["to"]]
            if not (src.get("path", "").endswith(".py")):
                continue
            key = (src["path"], src.get("symbol"))
            refs = refs_cache.setdefault(key, referenced_names(*key))
            target_name = dst.get("symbol") or dst["id"].rsplit(".", 1)[-1].split(":")[-1]
            if target_name.split(".")[-1] not in refs:
                errors.append(f"unverified {edge['type']} edge {edge['from']} -> {edge['to']}: "
                              f"{target_name!r} not referenced in {src['path']} (mark it inferred or fix it)")

    stored_nodes = {n["id"] for n in graph["nodes"] if n.get("auto")}
    stored_edges = {(e["from"], e["to"], e["type"]) for e in graph["edges"] if e.get("auto")}
    fresh_nodes, fresh_edges = generated(nodes)
    if {n["id"] for n in fresh_nodes} != stored_nodes or {(e["from"], e["to"], e["type"]) for e in fresh_edges} != stored_edges:
        missing = sorted({n["id"] for n in fresh_nodes} - stored_nodes)
        extra = sorted(stored_nodes - {n["id"] for n in fresh_nodes})
        errors.append(f"generated routes/imports are stale (run --update); missing={missing} extra={extra}")

    if graph.get("source", {}).get("fingerprint") != fingerprint():
        warnings.append("code changed since the graph was last updated (fingerprint differs); "
                        "review curated edges, then run --update")
    return errors, warnings


def update(graph: dict) -> dict:
    nodes_by_id = {n["id"]: n for n in graph["nodes"]}
    fresh_nodes, fresh_edges = generated(nodes_by_id)
    graph["nodes"] = [n for n in graph["nodes"] if not n.get("auto")] + fresh_nodes
    graph["edges"] = [e for e in graph["edges"] if not e.get("auto")] + fresh_edges
    graph["source"] = source_stamp()
    return graph


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true", help="refresh generated parts and the source stamp")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = parser.parse_args()
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    if args.update:
        graph = update(graph)
        GRAPH.write_text(json.dumps(graph, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    errors, warnings = check(graph)
    for line in warnings:
        print(f"warning: {line}")
    for line in errors:
        print(f"error: {line}")
    counts = f"{len(graph['nodes'])} nodes, {len(graph['edges'])} edges"
    if errors or (args.strict and warnings):
        print(f"codebase graph: FAILED ({counts})")
        return 1
    print(f"codebase graph: ok ({counts})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
