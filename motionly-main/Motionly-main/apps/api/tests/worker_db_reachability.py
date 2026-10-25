"""Which database objects can code that runs with the worker credential touch?

Media workers authenticate to PostgREST as the restricted ``motionly_worker``
role (Task 18). They run the Node worker (``apps/worker/src``) and, through it,
the Python job entry points in ``app.workers`` with the full app imported. This
module computes, statically, the tables and RPCs those code paths can reach, so
the worker's database grants can be an allowlist that is checked against the
code instead of "everything in public".

The Python side is a function-level call graph over ``apps/api/app`` built from
the worker entry points. Name resolution is deliberately generous (any call or
reference to a name reaches every function with that name, and string
constants such as ``_repository_call(repo, "get_x_async", "get_x")`` count as
references), so the result over-approximates what workers can do: a missing
grant would break a worker, an extra grant only widens the role slightly. A
handful of very generic method names are not followed by attribute (``.get``,
``.update``...), because they are overwhelmingly dict/HTTP calls; repository
methods with those names are reached through their specific names instead.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from tests.schema_usage import API_APP, ROOT, Usage, javascript_inventory, python_inventory

# Python entry points the Node worker runs (apps/worker/src/worker.mjs), plus
# the model prefetch the worker images run at start.
WORKER_ROOTS = (
    ("app/workers/domain_job.py", "_execute"),
    ("app/workers/visual_job.py", "execute"),
    ("app/workers/matte_job.py", "execute"),
    ("app/workers/prefetch_models.py", "main"),
    # `from app.main import app` runs create_app() in every worker process.
    ("app/main.py", "create_app"),
)
WORKER_JS_PREFIX = "apps/worker/src/"

# Attribute names too generic to resolve by name (dict/HTTP/stdlib methods).
GENERIC_ATTRIBUTES = frozenset(
    {
        "get",
        "set",
        "update",
        "pop",
        "items",
        "keys",
        "values",
        "append",
        "extend",
        "add",
        "copy",
        "join",
        "split",
        "strip",
        "format",
        "read",
        "write",
        "open",
        "close",
        "request",
        "json",
        "post",
        "patch",
        "delete",
        "put",
        "send",
        "run",
        "start",
        "stop",
        "wait",
        "result",
        "encode",
        "decode",
        "lower",
        "upper",
        "replace",
        "startswith",
        "endswith",
        "model_dump",
        "model_copy",
        "model_validate",
        "observe",
        "info",
        "warning",
        "error",
        "exception",
        "debug",
    }
)


@dataclass
class WorkerDatabaseUsage:
    tables: dict[str, set[str]] = field(default_factory=dict)  # table -> privileges
    rpcs: set[str] = field(default_factory=set)
    reachable_functions: int = 0

    def add_usage(self, usage: Usage) -> None:
        if usage.kind == "rpc" and usage.rpc:
            self.rpcs.add(usage.rpc)
            return
        if not usage.table:
            return
        privileges = self.tables.setdefault(usage.table, set())
        if usage.kind in {"table", "select", "filter"}:
            privileges.add("SELECT")
        elif usage.kind == "write":
            if usage.action in {"insert", "upsert"}:
                privileges.add("INSERT")
            if usage.action in {"update", "upsert"}:
                privileges.add("UPDATE")
            if usage.action is None:
                privileges.update({"INSERT", "UPDATE"})
        elif usage.kind == "delete":
            privileges.add("DELETE")


@dataclass
class _Function:
    module: str
    name: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    references: set[str] = field(default_factory=set)


def _local_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Parameters and variables bound in the function (not references to defs)."""
    names = {arg.arg for arg in ast.walk(node.args) if isinstance(arg, ast.arg)}
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            names.add(child.id)
    return names


def _references(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    local = _local_names(node)
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            if isinstance(child.ctx, ast.Load) and child.id not in local:
                names.add(child.id)
        elif isinstance(child, ast.Attribute):
            if child.attr not in GENERIC_ATTRIBUTES:
                names.add(child.attr)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            if child.value.isidentifier():
                names.add(child.value)
    return names


def _functions() -> dict[str, list[_Function]]:
    by_name: dict[str, list[_Function]] = {}
    for path in sorted(API_APP.rglob("*.py")):
        module = str(path.relative_to(API_APP.parent))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = _Function(module=module, name=node.name, node=node)
                function.references = _references(node)
                by_name.setdefault(node.name, []).append(function)
    return by_name


def _reachable(by_name: dict[str, list[_Function]]) -> set[tuple[str, int]]:
    """(module, def line) of every function reachable from the worker roots."""
    pending: list[_Function] = []
    for module, name in WORKER_ROOTS:
        matches = [fn for fn in by_name.get(name, []) if fn.module == module]
        assert matches, f"worker entry point {module}:{name} not found"
        pending.extend(matches)
    seen: set[tuple[str, int]] = set()
    while pending:
        function = pending.pop()
        key = (function.module, function.node.lineno)
        if key in seen:
            continue
        seen.add(key)
        for name in function.references:
            pending.extend(by_name.get(name, ()))
    return seen


def _enclosing_def_line(module: str, line: int, spans: dict[str, list[tuple[int, int, int]]]):
    """The innermost function (by def line) containing ``line`` in ``module``."""
    best = None
    for start, end, def_line in spans.get(module, ()):
        if start <= line <= end and (best is None or start >= best[0]):
            best = (start, def_line)
    return best[1] if best else None


def _call_privileges(module: str, line: int, cache: dict[str, ast.Module]) -> set[str]:
    """Privileges a Python PostgREST call at ``module:line`` needs, from its method.

    ``GET`` reads; ``POST`` inserts (and updates when it is an upsert that
    merges duplicates); ``PATCH`` updates; ``DELETE`` deletes. Filters and
    ``return=representation`` read rows, so every write also needs SELECT. A
    call whose method is not a literal gets every privilege (over-approximate).
    """
    path = API_APP.parent / module
    tree = cache.get(module)
    if tree is None:
        tree = cache[module] = ast.parse(path.read_text(encoding="utf-8"))
        tree.source = path.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node.lineno != line:
            continue
        method = None
        if isinstance(node.func, ast.Attribute) and node.func.attr.upper() in HTTP_METHODS:
            method = node.func.attr  # httpx.get(...), client.post(...)
        elif isinstance(node.func, (ast.Attribute, ast.Name)) and (
            getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        ) in {"_get_rows"}:
            method = "GET"
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.upper() in HTTP_METHODS
        ):
            method = node.args[0].value
        for keyword in node.keywords:
            if keyword.arg == "method" and isinstance(keyword.value, ast.Constant):
                method = keyword.value.value
        if not isinstance(method, str) or method.upper() not in HTTP_METHODS:
            continue
        method = method.upper()
        text = ast.get_source_segment(tree.source, node) or ""  # type: ignore[attr-defined]
        if method in {"GET", "HEAD"}:
            return {"SELECT"}
        if method == "POST":
            upsert = "merge-duplicates" in text or (
                "on_conflict" in text and "ignore-duplicates" not in text
            )
            return {"SELECT", "INSERT", "UPDATE"} if upsert else {"SELECT", "INSERT"}
        if method in {"PATCH", "PUT"}:
            return {"SELECT", "UPDATE"}
        if method == "DELETE":
            return {"SELECT", "DELETE"}
    return {"SELECT", "INSERT", "UPDATE", "DELETE"}


HTTP_METHODS = {"GET", "HEAD", "POST", "PATCH", "PUT", "DELETE"}


def worker_database_usage() -> WorkerDatabaseUsage:
    by_name = _functions()
    reachable = _reachable(by_name)
    spans: dict[str, list[tuple[int, int, int]]] = {}
    for functions in by_name.values():
        for fn in functions:
            spans.setdefault(fn.module, []).append(
                (fn.node.lineno, fn.node.end_lineno or fn.node.lineno, fn.node.lineno)
            )

    usage = WorkerDatabaseUsage(reachable_functions=len(reachable))
    trees: dict[str, ast.Module] = {}
    for item in python_inventory().usages:
        path, _, line = item.source.rpartition(":")
        module = str(Path(path).relative_to("apps/api"))
        def_line = _enclosing_def_line(module, int(line), spans)
        if def_line is None or (module, def_line) not in reachable:
            continue
        usage.add_usage(item)
        if item.kind == "table" and item.table:
            usage.tables.setdefault(item.table, set()).update(
                _call_privileges(module, int(line), trees)
            )
    for item in javascript_inventory().usages:
        if item.source.startswith(WORKER_JS_PREFIX):
            usage.add_usage(item)
    return usage


def migration_text(name_fragment: str) -> str:
    matches = sorted((ROOT / "supabase" / "migrations").glob(f"*{name_fragment}*.sql"))
    assert len(matches) == 1, matches
    return matches[0].read_text(encoding="utf-8")
