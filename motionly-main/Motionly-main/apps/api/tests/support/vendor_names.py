"""Vendor-name checks for tests, backed by the neutrality scanner's matcher.

Tests that guard against a vendor name coming back (in settings, payloads,
secret names, docs) assert through ``names_vendor`` instead of spelling the
names, so the guard itself adds no vendor token to the codebase and uses the
same token rules as ``scripts/verify/vendor_neutrality.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from functools import cache
from pathlib import Path
from types import ModuleType

_SCANNER = Path(__file__).resolve().parents[4] / "scripts" / "verify" / "vendor_neutrality.py"


@cache
def _scanner() -> ModuleType:
    name = "motionly_vendor_neutrality"
    module = sys.modules.get(name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, _SCANNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def vendors_in(text: str) -> list[str]:
    """The audited vendors named in ``text`` (sorted, deduplicated)."""
    return [vendor for vendor, _token in _scanner().match_vendors(text)]


def names_vendor(text: str) -> bool:
    """Whether ``text`` names any audited vendor."""
    return bool(vendors_in(text))
