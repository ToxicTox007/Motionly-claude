"""Static inventory of every database object the runtime code touches.

Used by ``tests/integration/test_schema_matches_code.py``. Python call sites are
read from the AST of ``apps/api/app``; worker and web call sites come from
``scripts/verify/js-db-usage.mjs``. Nothing here talks to a database: the live
comparison against the PostgREST schema happens in the integration test.

Covered: tables, ``select`` projections (including embedded resources), filter
and ordering columns, literal write payload keys, RPC names, and RPC argument
names. Anything the extractor cannot resolve is reported in
``Inventory.unresolved`` so a new call pattern can never be skipped silently.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
API_ROOT = ROOT / "apps" / "api"
API_APP = API_ROOT / "app"
JS_EXTRACTOR = ROOT / "scripts" / "verify" / "js-db-usage.mjs"

HTTP_METHODS = {"GET", "POST", "PATCH", "PUT", "DELETE", "HEAD"}
WRITE_METHODS = {"POST", "PATCH", "PUT"}
TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
RPC_PATH = re.compile(r"^/rpc/([a-z][a-z0-9_]*)$")
REST_URL_SUFFIX = re.compile(r"/(?:rest/v1/)?([a-z][a-z0-9_]*)$")
# Helpers whose first argument is an RPC function name (they build /rpc/{name}).
RPC_HELPERS = {"_rpc", "_rpc_single", "_rpc_rows", "_rpc_count"}
# Supabase REST helpers called as helper(METHOD, "/table" | "table" | table, ...).
REQUEST_HELPERS = {"request", "async_request", "_request", "_request_async", "_rows"}
# Helpers whose first string argument is a table (no leading slash).
TABLE_HELPERS = {"_get_rows"}
HTTPX_VERBS = {"get", "post", "patch", "delete", "put", "request", "stream"}
NON_COLUMN_PARAMS = {"select", "order", "limit", "offset", "on_conflict", "columns", "or", "and"}
# REST URLs held in attributes rather than built at the call site.
ATTRIBUTE_TABLES = {
    # LicensedCatalogMusicProvider.rest_url is {supabase}/rest/v1/{table_name};
    # table_name defaults to settings.licensed_music_catalog_table.
    ("app/services/music.py", "rest_url"): "licensed_music_tracks",
}
# Modules whose request helpers target GoTrue (``/auth/v1``), never PostgREST.
# Their single-segment paths (e.g. ``/verify``, ``/signup``, ``/token``) are
# GoTrue endpoints, not Supabase tables, so they are not table usages.
GOTRUE_CLIENT_MODULES = {
    "app/services/auth_admin.py",
}
# Modules whose PostgREST calls target a private non-``public`` schema via an
# ``Accept-Profile``/``Content-Profile`` header, so their relation names
# (``orders``, ``subscriptions``, ``refunds``, ``idempotency_keys``,
# ``webhook_events``) are NOT ``public`` tables and must not be matched against
# the public schema. The mock-gateway store reads/writes the ``mock_gateway``
# schema with its own ``mock_gateway_role`` JWT (migration 202610190801); that
# schema has its own pgTAP coverage (task_22_mock_gateway_schema_test.sql).
NON_PUBLIC_SCHEMA_CLIENT_MODULES = {
    "app/mock_gateway/store_db.py",
}


@dataclass(frozen=True)
class Usage:
    role: str  # "service_role" (API, worker) or "authenticated" (web user session)
    kind: str  # table | select | filter | write | delete | rpc | rpc_select
    source: str
    table: str | None = None
    rpc: str | None = None
    columns: tuple[str, ...] = ()
    embeds: tuple[tuple[str, tuple[str, ...]], ...] = ()
    args: tuple[str, ...] | None = None  # RPC argument names, when determinable
    action: str | None = None  # write: insert | update | upsert


@dataclass
class Inventory:
    usages: list[Usage] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    dynamic_writes: list[str] = field(default_factory=list)
    uncalled: list[str] = field(default_factory=list)


def parse_select(value: str) -> tuple[tuple[str, ...], tuple[tuple[str, tuple[str, ...]], ...]]:
    """Split a PostgREST ``select`` into top-level columns and embedded resources."""
    columns: list[str] = []
    embeds: list[tuple[str, tuple[str, ...]]] = []
    for raw in _split_top_level(value):
        item = raw.strip()
        if not item or item == "*" or item.startswith("..."):
            continue
        if item.endswith(")") and "(" in item:
            head, inner = item[:-1].split("(", 1)
            relation = head.split(":")[-1].split("!")[0].strip()
            inner_columns, _ = parse_select(inner)
            embeds.append((relation, inner_columns))
            continue
        base = item.split("::")[0]
        base = base.split(":")[-1]
        base = re.split(r"->>?", base)[0].strip()
        if base:
            columns.append(base)
    return tuple(columns), tuple(embeds)


def order_columns(value: str) -> tuple[str, ...]:
    return tuple(part.split(".")[0].strip() for part in value.split(",") if part.strip())


def _split_top_level(value: str) -> list[str]:
    parts, depth, current = [], 0, []
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


class _Module:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.rel = path.relative_to(API_ROOT).as_posix()
        self.tree = ast.parse(path.read_text(encoding="utf-8"))
        self.parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(self.tree):
            for child in ast.iter_child_nodes(node):
                self.parents[child] = node
        self.constants: dict[str, object] = {}
        for node in self.tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                try:
                    self.constants[node.targets[0].id] = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    pass
        self.functions: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                self.functions.setdefault(node.name, []).append(node)
        self.uncalled: set[str] = set()
        # Attributes that hold a Supabase REST base URL (".../rest/v1").
        self.rest_base_attributes: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                        and "/rest/v1" in ast.unparse(node.value)
                    ):
                        self.rest_base_attributes.add(target.attr)

    def where(self, node: ast.AST) -> str:
        return f"apps/api/{self.rel}:{getattr(node, 'lineno', 0)}"

    def enclosing_function(self, node: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        current = self.parents.get(node)
        while current is not None:
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
                return current
            current = self.parents.get(current)
        return None

    def string_value(self, node: ast.AST | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and isinstance(self.constants.get(node.id), str):
            return str(self.constants[node.id])
        return None

    def dict_literal(self, node: ast.AST | None, scope: ast.AST | None) -> ast.Dict | None:
        """Resolve a Dict literal directly, via a local name, or via a builder call."""
        if isinstance(node, ast.Dict):
            return node
        if isinstance(node, ast.Name) and scope is not None:
            assigned = None
            for candidate in ast.walk(scope):
                if (
                    isinstance(candidate, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == node.id for t in candidate.targets)
                    and isinstance(candidate.value, ast.Dict)
                ):
                    assigned = candidate.value
            return assigned
        if isinstance(node, ast.Call):
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            for function in self.functions.get(name or "", []):
                returns = [n for n in ast.walk(function) if isinstance(n, ast.Return)]
                if returns and isinstance(returns[-1].value, ast.Dict):
                    return returns[-1].value
        return None

    def dict_keys(self, node: ast.Dict, scope: ast.AST | None) -> tuple[str, ...] | None:
        keys = []
        for key in node.keys:
            value = self.string_value(key)
            if value is None:
                return None
            keys.append(value)
        # Subscript assignments that add keys to a named dict later in the scope.
        return tuple(keys)

    def added_keys(self, name: str, scope: ast.AST | None) -> tuple[str, ...]:
        if scope is None:
            return ()
        added = []
        for candidate in ast.walk(scope):
            if isinstance(candidate, ast.Assign):
                for target in candidate.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == name
                    ):
                        key = self.string_value(target.slice)
                        if key is not None:
                            added.append(key)
        return tuple(added)

    def parameter_tables(self, node: ast.Name, call: ast.Call) -> list[str] | None:
        """Resolve a table passed in as a parameter of the enclosing function."""
        function = self.enclosing_function(call)
        if function is None:
            return None
        positional_params = [arg.arg for arg in function.args.args]
        params = positional_params + [arg.arg for arg in function.args.kwonlyargs]
        if node.id not in params:
            return None
        index = (
            positional_params.index(node.id)
            - (1 if positional_params and positional_params[0] in {"self", "cls"} else 0)
            if node.id in positional_params
            else -1
        )
        tables = []
        called = False
        for candidate in ast.walk(self.tree):
            if not isinstance(candidate, ast.Call):
                continue
            func = candidate.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called != function.name:
                continue
            called = True
            value = None
            for keyword in candidate.keywords:
                if keyword.arg == node.id:
                    value = self.string_value(keyword.value)
            if value is None and 0 <= index < len(candidate.args):
                value = self.string_value(candidate.args[index])
            if value is not None and TABLE_NAME.match(value.lstrip("/")):
                tables.append(value.lstrip("/"))
        if not called:
            self.uncalled.add(f"{self.rel}:{function.name}")
            return []
        return tables or None


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _python_usages(module: _Module, inventory: Inventory) -> None:
    for call in ast.walk(module.tree):
        if not isinstance(call, ast.Call):
            continue
        name = _call_name(call)
        scope = module.enclosing_function(call)
        keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
        positional = list(call.args)
        strings = [module.string_value(arg) for arg in positional]

        method = next((value for value in strings if value in HTTP_METHODS), None)
        if method is None and name in {"get", "post", "patch", "delete", "put"}:
            method = name.upper()

        rpc = None
        for value in strings:
            match = RPC_PATH.match(value or "")
            if match:
                rpc = match.group(1)
        rpc_body: ast.AST | None = keywords.get("json")
        if name in RPC_HELPERS and strings and strings[0] and TABLE_NAME.match(strings[0]):
            rpc = strings[0]
            rpc_body = positional[1] if len(positional) > 1 else keywords.get("payload")

        tables: list[str] | None = None
        dynamic_table = False
        if rpc is None:
            tables, dynamic_table, skip, query_columns = _rest_tables(
                module, call, name, positional, strings, keywords
            )
            if skip:
                continue

        else:
            query_columns = ()
        params_node = keywords.get("params")
        if params_node is None and name in TABLE_HELPERS and len(positional) > 1:
            params_node = positional[1]
        params = module.dict_literal(params_node, scope)
        select = None
        filters: list[str] = list(query_columns)
        if params is not None:
            for key_node, value_node in zip(params.keys, params.values, strict=True):
                key = module.string_value(key_node)
                if key is None:
                    continue
                if key == "select":
                    select = module.string_value(value_node)
                    if select is None:
                        inventory.unresolved.append(
                            f"{module.where(call)}: select value is not a constant"
                        )
                elif key == "order":
                    value = module.string_value(value_node)
                    if value:
                        filters.extend(order_columns(value))
                elif key == "on_conflict":
                    value = module.string_value(value_node)
                    if value:
                        filters.extend(order_columns(value))
                elif key not in NON_COLUMN_PARAMS:
                    filters.append(re.split(r"->>?", key)[0])
            if isinstance(params_node, ast.Name):
                filters.extend(
                    key
                    for key in module.added_keys(params_node.id, scope)
                    if key not in NON_COLUMN_PARAMS
                )

        where = module.where(call)
        if rpc is not None:
            args: tuple[str, ...] | None
            body = module.dict_literal(rpc_body, scope)
            if rpc_body is None:
                args = ()
            elif body is not None:
                args = module.dict_keys(body, scope)
                if args is not None and isinstance(rpc_body, ast.Name):
                    args = tuple(dict.fromkeys(args + module.added_keys(rpc_body.id, scope)))
            else:
                args = None
            if args is None:
                inventory.unresolved.append(f"{where}: arguments for rpc {rpc} are not literal")
            inventory.usages.append(
                Usage(role="service_role", kind="rpc", source=where, rpc=rpc, args=args)
            )
            if select is not None:
                columns, embeds = parse_select(select)
                inventory.usages.append(
                    Usage(
                        role="service_role",
                        kind="rpc_select",
                        source=where,
                        rpc=rpc,
                        columns=columns,
                        embeds=embeds,
                    )
                )
            continue

        if tables is None:
            if select is not None or dynamic_table:
                inventory.unresolved.append(f"{where}: REST call whose table cannot be resolved")
            continue

        body_node = keywords.get("json")
        headers_text = ast.unparse(keywords["headers"]) if "headers" in keywords else ""
        action = {"POST": "insert", "PATCH": "update", "PUT": "upsert"}.get(method or "")
        if action == "insert" and "merge-duplicates" in headers_text:
            action = "upsert"
        for table in tables:
            inventory.usages.append(
                Usage(role="service_role", kind="table", source=where, table=table)
            )
            if method == "DELETE":
                inventory.usages.append(
                    Usage(role="service_role", kind="delete", source=where, table=table)
                )
            if select is not None:
                columns, embeds = parse_select(select)
                inventory.usages.append(
                    Usage(
                        role="service_role",
                        kind="select",
                        source=where,
                        table=table,
                        columns=columns,
                        embeds=embeds,
                    )
                )
            if filters:
                inventory.usages.append(
                    Usage(
                        role="service_role",
                        kind="filter",
                        source=where,
                        table=table,
                        columns=tuple(dict.fromkeys(filters)),
                    )
                )
            if method in WRITE_METHODS and body_node is not None:
                literal_rows = (
                    [element for element in body_node.elts if isinstance(element, ast.Dict)]
                    if isinstance(body_node, ast.List)
                    else [module.dict_literal(body_node, scope)]
                )
                keys: list[str] = []
                resolved = True
                for row in literal_rows:
                    row_keys = module.dict_keys(row, scope) if row is not None else None
                    if row_keys is None:
                        resolved = False
                        break
                    keys.extend(row_keys)
                if resolved and isinstance(body_node, ast.Name):
                    keys.extend(module.added_keys(body_node.id, scope))
                if resolved and keys:
                    inventory.usages.append(
                        Usage(
                            role="service_role",
                            kind="write",
                            source=where,
                            table=table,
                            columns=tuple(dict.fromkeys(keys)),
                            action=action,
                        )
                    )
                else:
                    inventory.dynamic_writes.append(f"{where}: {method} {table}")


def _in_request_helper(module: _Module, call: ast.Call) -> bool:
    function = module.enclosing_function(call)
    return function is not None and (
        function.name in REQUEST_HELPERS or function.name.lstrip("_").startswith("request")
    )


def _url_path(module: _Module, url: ast.JoinedStr) -> list[ast.AST | str] | None:
    """Tokens of the REST path after the Supabase REST base, or None if not REST."""
    values = list(url.values)
    head = values[0] if values else None
    if (
        isinstance(head, ast.FormattedValue)
        and isinstance(head.value, ast.Attribute)
        and head.value.attr in module.rest_base_attributes
    ):
        tokens: list[ast.AST | str] = []
        for part in values[1:]:
            tokens.append(str(part.value) if isinstance(part, ast.Constant) else part)
        return tokens
    for index, part in enumerate(values):
        if isinstance(part, ast.Constant) and "/rest/v1/" in str(part.value):
            tokens = [str(part.value).split("/rest/v1/", 1)[1]]
            for later in values[index + 1 :]:
                tokens.append(str(later.value) if isinstance(later, ast.Constant) else later)
            return tokens
    return None


def _rest_tables(
    module: _Module,
    call: ast.Call,
    name: str | None,
    positional: list[ast.AST],
    strings: list[str | None],
    keywords: dict[str, ast.AST],
) -> tuple[list[str] | None, bool, bool, tuple[str, ...]]:
    """Return (tables, dynamic, skip, query_columns) for a Supabase REST call.

    ``skip`` marks calls that are not table usages: generic request-helper
    bodies (their callers are analysed instead), RPC helpers, the REST root
    health probe, and non-Supabase HTTP calls.
    """
    if module.rel in GOTRUE_CLIENT_MODULES and (
        name in REQUEST_HELPERS or name in HTTPX_VERBS
    ):
        return None, False, True, ()  # GoTrue /auth/v1 call, not a Supabase table
    if module.rel in NON_PUBLIC_SCHEMA_CLIENT_MODULES and (
        name in REQUEST_HELPERS or name in HTTPX_VERBS
    ):
        return None, False, True, ()  # private non-public schema (Accept-Profile)
    if name in REQUEST_HELPERS or name in TABLE_HELPERS:
        for index, arg in enumerate(positional):
            follows_method = index > 0 and strings[index - 1] in HTTP_METHODS
            first_table_arg = name in TABLE_HELPERS and index == 0
            if not (follows_method or first_table_arg):
                continue
            value = strings[index]
            if value is not None:
                table = value[1:] if value.startswith("/") else value
                if TABLE_NAME.match(table):
                    return [table], False, False, ()
            if isinstance(arg, ast.JoinedStr):
                head = arg.values[0] if arg.values else None
                if isinstance(head, ast.Constant) and str(head.value).startswith("/rpc/"):
                    return None, False, True, ()
            if isinstance(arg, ast.Name):
                if _in_request_helper(module, call):
                    return None, False, True, ()
                tables = module.parameter_tables(arg, call)
                if tables is None:
                    return None, False, True, ()  # a local URL variable: not Supabase REST
                return tables, False, False, ()
    if name in HTTPX_VERBS:
        url = positional[0] if positional else keywords.get("url")
        if name in {"request", "stream"} and len(positional) > 1:
            url = positional[1]
        if isinstance(url, ast.JoinedStr):
            tokens = _url_path(module, url)
            if tokens is None:
                return None, False, True, ()
            first = tokens[0] if tokens else ""
            query_columns: tuple[str, ...] = ()
            rendered_constants = "".join(token for token in tokens if isinstance(token, str))
            if "?" in rendered_constants:
                query = rendered_constants.split("?", 1)[1]
                for pair in query.split("&"):
                    key, _, value = pair.partition("=")
                    if key == "on_conflict":
                        query_columns = order_columns(value)
            if isinstance(first, str):
                text = first.lstrip("/")
                if text == "" and len(tokens) == 1:
                    return None, False, True, ()  # REST root health probe
                if text.startswith("rpc/"):
                    return None, False, True, ()
                table = re.split(r"[/?]", text, maxsplit=1)[0]
                if table and TABLE_NAME.match(table):
                    return [table], False, False, query_columns
                if table == "" and len(tokens) > 1:
                    first = tokens[1]
            if isinstance(first, ast.FormattedValue):
                if _in_request_helper(module, call):
                    return None, False, True, ()
                if isinstance(first.value, ast.Name):
                    tables = module.parameter_tables(first.value, call)
                    return tables, tables is None, False, query_columns
            return None, True, False, ()
        if isinstance(url, ast.Attribute):
            mapped = ATTRIBUTE_TABLES.get((module.rel, url.attr))
            if mapped:
                return [mapped], False, False, ()
    return None, False, True, ()


def python_inventory() -> Inventory:
    inventory = Inventory()
    for path in sorted(API_APP.rglob("*.py")):
        module = _Module(path)
        _python_usages(module, inventory)
        inventory.uncalled.extend(sorted(module.uncalled))
    return inventory


def javascript_inventory() -> Inventory:
    completed = subprocess.run(
        ["node", str(JS_EXTRACTOR)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    inventory = Inventory(
        unresolved=list(payload["unresolved"]),
        dynamic_writes=list(payload["dynamic_writes"]),
    )
    for item in payload["usages"]:
        columns: tuple[str, ...] = tuple(item.get("columns") or ())
        embeds: tuple[tuple[str, tuple[str, ...]], ...] = ()
        if item.get("select") is not None:
            columns, embeds = parse_select(item["select"])
        args = item.get("args")
        inventory.usages.append(
            Usage(
                role=item["role"],
                kind=item["kind"],
                source=item["source"],
                table=item.get("table"),
                rpc=item.get("rpc"),
                columns=columns,
                embeds=embeds,
                args=tuple(args) if args is not None else None,
                action=item.get("action"),
            )
        )
    return inventory


def full_inventory() -> Inventory:
    python = python_inventory()
    javascript = javascript_inventory()
    return Inventory(
        usages=python.usages + javascript.usages,
        unresolved=python.unresolved + javascript.unresolved,
        dynamic_writes=python.dynamic_writes + javascript.dynamic_writes,
        uncalled=python.uncalled,
    )
