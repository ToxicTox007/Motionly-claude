"""The database built from migrations provides everything the code uses.

Runs in the ``db`` verify stage against a database created from empty by
``supabase db reset``. The inventory of tables, columns, embeds and RPC
arguments used by the API, worker and web code comes from
``tests/schema_usage.py``; the database side comes from
``scripts/verify/db-schema.mjs``.

RPC matching follows PostgREST's overload resolution: a call matches an
overload when every required argument is passed and every passed argument
exists. No match is PGRST202; more than one match is PGRST203 (ambiguous).

Not covered here: write payloads assembled at runtime (listed in
``test-results/schema-usage.json``), which the repository contract tests
exercise against the same database.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.schema_usage import ROOT, Inventory, Usage, full_inventory

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_SUPABASE_CONTRACTS") != "1",
    reason="Set RUN_SUPABASE_CONTRACTS=1 against an isolated local Supabase stack.",
)

DB_SCHEMA_SCRIPT = ROOT / "scripts" / "verify" / "db-schema.mjs"
REPORT = ROOT / "test-results" / "schema-usage.json"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    # The CPU-stage stack captures the schema inside its private network.
    captured = os.environ.get("DB_SCHEMA_JSON")
    if captured:
        return json.loads(Path(captured).read_text(encoding="utf-8"))
    database_url = os.environ.get("DATABASE_URL")
    assert database_url, "DATABASE_URL must point at the migrated database"
    completed = subprocess.run(
        ["node", str(DB_SCHEMA_SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "DATABASE_URL": database_url},
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def inventory(schema: dict[str, Any]) -> Inventory:
    collected = full_inventory()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {
                "checked_usages": len(collected.usages),
                "tables": sorted({u.table for u in collected.usages if u.table}),
                "rpcs": sorted({u.rpc for u in collected.usages if u.rpc}),
                "not_checked_runtime_write_payloads": collected.dynamic_writes,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return collected


def _fail_if(problems: list[str]) -> None:
    unique = sorted(set(problems))
    assert not unique, f"{len(unique)} schema mismatches:\n" + "\n".join(unique)


def _column_problems(
    usage: Usage,
    relation: dict[str, Any],
    table: str,
    columns: tuple[str, ...],
    need: tuple[str, ...],
) -> list[str]:
    problems = []
    for column in columns:
        grants = relation["columns"].get(column)
        if grants is None:
            problems.append(f"{usage.source}: column {table}.{column} does not exist")
            continue
        for privilege in need:
            if not grants.get(usage.role, {}).get(privilege):
                problems.append(
                    f"{usage.source}: role {usage.role} lacks {privilege.upper()} "
                    f"on {table}.{column}"
                )
    return problems


def test_every_table_the_code_uses_exists(schema: dict[str, Any], inventory: Inventory) -> None:
    _fail_if(
        [
            f"{usage.source}: table {usage.table} does not exist"
            for usage in inventory.usages
            if usage.kind == "table" and usage.table not in schema["relations"]
        ]
    )


def test_every_selected_filtered_and_written_column_exists(
    schema: dict[str, Any], inventory: Inventory
) -> None:
    needs = {
        "select": ("select",),
        "filter": ("select",),
        "insert": ("insert",),
        "update": ("update",),
        "upsert": ("insert", "update"),
    }
    problems: list[str] = []
    for usage in inventory.usages:
        relation = schema["relations"].get(usage.table or "")
        if relation is None:
            continue  # reported by the table test
        if usage.kind in {"select", "filter"}:
            problems += _column_problems(
                usage, relation, str(usage.table), usage.columns, needs[usage.kind]
            )
        elif usage.kind == "write":
            problems += _column_problems(
                usage, relation, str(usage.table), usage.columns, needs[str(usage.action)]
            )
        elif usage.kind == "delete" and not relation["delete"].get(usage.role):
            problems.append(f"{usage.source}: role {usage.role} lacks DELETE on {usage.table}")
    _fail_if(problems)


def test_every_embedded_resource_is_related_and_has_its_columns(
    schema: dict[str, Any], inventory: Inventory
) -> None:
    related = {tuple(pair) for pair in schema["foreign_keys"]}
    problems: list[str] = []
    for usage in inventory.usages:
        if usage.kind != "select":
            continue
        for embed, columns in usage.embeds:
            relation = schema["relations"].get(embed)
            if relation is None:
                problems.append(f"{usage.source}: embedded table {embed} does not exist")
                continue
            if (usage.table, embed) not in related and (embed, usage.table) not in related:
                problems.append(
                    f"{usage.source}: no foreign key between {usage.table} and {embed} to embed"
                )
            problems += _column_problems(usage, relation, embed, columns, ("select",))
    _fail_if(problems)


def test_every_rpc_call_resolves_to_exactly_one_function(
    schema: dict[str, Any], inventory: Inventory
) -> None:
    problems: list[str] = []
    for usage in inventory.usages:
        if usage.kind != "rpc":
            continue
        overloads = schema["functions"].get(str(usage.rpc), [])
        if not overloads:
            problems.append(f"{usage.source}: function {usage.rpc} does not exist")
            continue
        passed = set(usage.args or ())
        matches = [
            overload
            for overload in overloads
            if set(overload["required"]) <= passed <= set(overload["args"])
        ]
        if not matches:
            signatures = "; ".join(overload["signature"] for overload in overloads)
            problems.append(
                f"{usage.source}: no overload of {usage.rpc} accepts "
                f"({', '.join(sorted(passed))}); have {signatures}"
            )
        elif len(matches) > 1:
            signatures = "; ".join(overload["signature"] for overload in matches)
            problems.append(f"{usage.source}: call to {usage.rpc} is ambiguous: {signatures}")
        elif usage.role not in matches[0]["executable_by"]:
            problems.append(
                f"{usage.source}: role {usage.role} cannot execute {matches[0]['signature']}"
            )
    _fail_if(problems)


def test_rpc_result_projections_exist(schema: dict[str, Any], inventory: Inventory) -> None:
    problems: list[str] = []
    for usage in inventory.usages:
        if usage.kind != "rpc_select":
            continue
        relations = {
            overload["return_relation"]
            for overload in schema["functions"].get(str(usage.rpc), [])
            if overload["return_relation"]
        }
        if not relations:
            problems.append(f"{usage.source}: {usage.rpc} does not return a table row type")
            continue
        for relation_name in sorted(relations):
            relation = schema["relations"][relation_name]
            problems += _column_problems(usage, relation, relation_name, usage.columns, ())
    _fail_if(problems)


def test_report_path_is_inside_ignored_test_results() -> None:
    assert Path(REPORT).relative_to(ROOT).parts[0] == "test-results"
