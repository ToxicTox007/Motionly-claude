"""The database-usage extractor sees every call site (unit stage, no database).

``tests/integration/test_schema_matches_code.py`` compares this inventory with
the migrated database. These checks make sure the inventory itself is complete:
a call pattern the extractor cannot resolve fails here instead of being skipped.
"""

from __future__ import annotations

from tests.schema_usage import full_inventory, parse_select


def test_every_database_call_site_is_resolved() -> None:
    inventory = full_inventory()

    assert inventory.unresolved == []
    assert inventory.uncalled == []


def test_inventory_covers_api_worker_and_web_call_sites() -> None:
    usages = full_inventory().usages
    sources = {usage.source.split("/")[1] for usage in usages if usage.source.startswith("apps/")}
    rpcs = {(usage.rpc, usage.role) for usage in usages if usage.kind == "rpc"}
    selects = {
        (usage.table, column)
        for usage in usages
        if usage.kind == "select"
        for column in usage.columns
    }

    assert sources == {"api", "worker", "web"}
    # Module-level projection constants resolve (captions.CAPTION_TRACK_COLUMNS).
    assert ("caption_tracks", "review_status") in selects
    # Worker RPCs: supabase-js call and runtime-built workflow parameters.
    assert ("motionly_complete_render", "service_role") in rpcs
    assert ("motionly_begin_workflow_step", "service_role") in rpcs
    # Web calls run as the signed-in user.
    assert ("update_own_profile", "authenticated") in rpcs
    # Helper-based RPCs and payload builders resolve their argument names.
    by_name = {usage.rpc: usage.args for usage in usages if usage.kind == "rpc"}
    assert by_name["motionly_reserve_storage_quota"] == (
        "p_reservation_id",
        "p_workspace_id",
        "p_byte_amount",
        "p_object_amount",
        "p_expires_at",
    )
    assert by_name["motionly_expire_upload_sessions"] == ()
    assert "p_plan" in (by_name["motionly_create_active_edit_plan"] or ())


def test_select_parser_handles_embeds_aliases_and_casts() -> None:
    assert parse_select("workspace_id, workspaces(name)") == (
        ("workspace_id",),
        (("workspaces", ("name",)),),
    )
    assert parse_select("id,total:amount::text,meta->>kind,owner:profiles!fk(full_name)") == (
        ("id", "amount", "meta"),
        (("profiles", ("full_name",)),),
    )
    assert parse_select("*") == ((), ())
