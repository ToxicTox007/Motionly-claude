"""B22: inventory of plan-mutating API routes — every route that writes plan
content MUST support optimistic concurrency (``expected_revision`` +
``_update_plan_with_revision``) so a 409 ``collaboration_revision_conflict``
reaches the editor. A new mutating route that skips this will fail this test.

The test parses ``app/api/plans.py`` with the ``ast`` module and inspects each
route handler's body, so it does not require booting the app.
"""

import ast
from pathlib import Path

import pytest

PLANS_PY = Path(__file__).resolve().parents[1] / "app" / "api" / "plans.py"

# Routes that write plan *content* and therefore require revision enforcement.
# Keyed by handler function name.
REVISION_ENFORCED_WRITERS = {
    "update_plan_cuts",
    "update_plan_caption_highlights",
    "update_plan_manual_elements",
    "compile_project_timeline",
}

# Plan routes that intentionally do NOT do in-place CAS updates:
#  - create_plan / restore_plan_snapshot create brand-new plan versions
#  - create_plan_snapshot / list_* / get_plan are reads
NON_CAS_PLAN_ROUTES = {
    "create_plan",
    "restore_plan_snapshot",
    "create_plan_snapshot",
    "list_plan_snapshots",
    "list_plans",
    "get_plan",
    "get_plan_job_status",
}


def _module() -> ast.Module:
    return ast.parse(PLANS_PY.read_text(encoding="utf-8"))


def _route_handlers(module: ast.Module):
    """Yield (name, node) for every function decorated with @router.<method>."""
    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            # @router.post(...) / @router.patch(...) etc.
            func = deco.func if isinstance(deco, ast.Call) else deco
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "router"
                and func.attr in {"post", "patch", "put", "delete"}
            ):
                yield node.name, node
                break


def _calls_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _references_expected_revision(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr == "expected_revision":
            return True
        if isinstance(child, ast.Name) and child.id == "expected_revision":
            return True
        if isinstance(child, ast.keyword) and child.arg == "expected_revision":
            return True
    return False


def test_every_mutating_plan_route_is_classified():
    """Fail if a new write route appears that isn't explicitly classified.

    Forces the author of any new @router.post/patch route to decide whether it
    needs revision enforcement (add to REVISION_ENFORCED_WRITERS) or is a
    create/read (add to NON_CAS_PLAN_ROUTES).
    """
    module = _module()
    handlers = {name for name, _ in _route_handlers(module)}
    classified = REVISION_ENFORCED_WRITERS | NON_CAS_PLAN_ROUTES
    unclassified = handlers - classified
    assert not unclassified, (
        "New plan route(s) are not classified for conflict handling. If they "
        "mutate plan content, add them to REVISION_ENFORCED_WRITERS and route "
        f"them through _update_plan_with_revision: {sorted(unclassified)}"
    )


@pytest.mark.parametrize("handler_name", sorted(REVISION_ENFORCED_WRITERS))
def test_mutating_route_enforces_revision(handler_name: str):
    """Each content-writer must reference expected_revision and the CAS helper."""
    module = _module()
    handlers = dict(_route_handlers(module))
    assert handler_name in handlers, f"Route {handler_name} not found in plans.py"
    node = handlers[handler_name]
    assert _references_expected_revision(node), (
        f"{handler_name} does not reference expected_revision — it cannot "
        "detect a stale write."
    )
    called = _calls_names(node)
    assert "_update_plan_with_revision" in called, (
        f"{handler_name} must call _update_plan_with_revision so a stale "
        "revision raises RevisionConflictError (409)."
    )
