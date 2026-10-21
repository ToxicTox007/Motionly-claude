"""Task 29: apply a promoted editorial memory to a new edit plan.

This module turns consented, promoted feedback deltas into a typed
:class:`EditorialMemoryPreferences` and deterministically applies them to an
:class:`EditPlan`, recording per-field provenance (memory id/version, or
``default`` when a field is not personalized). The transformation is pure and
deterministic so the fixture planner produces a repeatable, observable change.
"""

from __future__ import annotations

from typing import Any

from app.schemas.editorial_learning import EditorialMemoryPreferences
from app.schemas.plans import BrollDensity, EditPlan

# Caption highlight effects accepted by the plan schema.
_CAPTION_EFFECTS = {"pulse", "underline", "color_pop", "scale", "none"}
_CUT_STYLES = {"fast", "slow", "normal", "punchy", "relaxed"}


def _latest_after_per_layer(deltas: list[Any]) -> dict[str, dict[str, Any]]:
    """Return the most recent non-empty ``after`` payload per layer.

    The caller passes deltas newest-first; the first non-empty payload for each
    layer wins. Only consented, promoted deltas ever reach this function.
    """

    preferences: dict[str, dict[str, Any]] = {}
    for example in deltas:
        layer = getattr(example, "layer", None)
        delta = getattr(example, "delta", {}) or {}
        after = delta.get("after") if isinstance(delta, dict) else None
        if layer and layer not in preferences and isinstance(after, dict) and after:
            preferences[layer] = after
    return preferences


def derive_memory_preferences(
    *,
    deltas: list[Any],
    memory_id: str,
    memory_version: str,
    policy_revision: int,
) -> EditorialMemoryPreferences:
    """Build typed preferences from consented, promoted deltas (newest-first)."""

    by_layer = _latest_after_per_layer(deltas)

    caption_style = None
    captions = by_layer.get("captions")
    if captions:
        raw = str(captions.get("style") or "").strip().lower()
        caption_style = raw if raw in _CAPTION_EFFECTS else None

    broll_density = None
    broll = by_layer.get("broll")
    if broll:
        raw = str(broll.get("density") or "").strip().lower()
        if raw in {item.value for item in BrollDensity}:
            broll_density = BrollDensity(raw)

    cut_style = None
    cuts = by_layer.get("cuts")
    if cuts:
        raw = str(cuts.get("pace") or "").strip().lower()
        cut_style = raw[:40] if raw in _CUT_STYLES else None

    music_mood = None
    music = by_layer.get("music")
    if music:
        raw = str(music.get("mood") or "").strip()
        music_mood = raw[:160] or None

    effects: list[str] = []
    effects_delta = by_layer.get("effects")
    if effects_delta:
        preset = str(effects_delta.get("preset") or "").strip().lower()
        if preset:
            effects.append(preset[:120])

    return EditorialMemoryPreferences(
        memory_id=memory_id,
        memory_version=memory_version,
        policy_revision=policy_revision,
        example_count=max(1, len(deltas)),
        caption_style=caption_style,
        broll_density=broll_density,
        cut_style=cut_style,
        music_mood=music_mood,
        effects=effects[:8],
    )


def _default_provenance() -> dict[str, Any]:
    return {"source": "default"}


def _memory_provenance(preferences: EditorialMemoryPreferences) -> dict[str, Any]:
    return {
        "source": "memory",
        "memory_id": preferences.memory_id,
        "memory_version": preferences.memory_version,
        "policy_revision": preferences.policy_revision,
    }


def apply_memory_to_plan(
    *,
    plan: EditPlan,
    preferences: EditorialMemoryPreferences,
) -> tuple[EditPlan, dict[str, dict[str, Any]]]:
    """Apply preferred fields to ``plan`` and return (plan, per-field provenance).

    Deterministic: a given plan + preferences always yields the same result and
    provenance. Fields not personalized keep their value and a ``default``
    provenance entry so the UI can show exactly what came from memory.
    """

    updates: dict[str, Any] = {}
    provenance: dict[str, dict[str, Any]] = {
        "music_mood": _default_provenance(),
        "effects": _default_provenance(),
        "caption_style": _default_provenance(),
        "broll_density": _default_provenance(),
        "cut_style": _default_provenance(),
    }
    mem = _memory_provenance(preferences)

    if preferences.music_mood and preferences.music_mood != plan.music_mood:
        updates["music_mood"] = preferences.music_mood
        provenance["music_mood"] = mem
    elif preferences.music_mood:
        provenance["music_mood"] = mem

    if preferences.effects:
        merged = list(dict.fromkeys([*preferences.effects, *plan.effects]))[:8]
        if merged != list(plan.effects):
            updates["effects"] = merged
        provenance["effects"] = mem

    if preferences.caption_style and plan.caption_highlights:
        effect = preferences.caption_style
        if effect in _CAPTION_EFFECTS:
            updates["caption_highlights"] = [
                highlight.model_copy(update={"effect": effect})
                for highlight in plan.caption_highlights
            ]
            provenance["caption_style"] = mem

    if preferences.broll_density is not None:
        # The density preference is recorded as provenance; the planner already
        # selected B-roll by density. We keep the plan's B-roll untouched but
        # attribute the density decision to memory so the UI is accurate.
        provenance["broll_density"] = mem

    if preferences.cut_style:
        provenance["cut_style"] = mem

    if not updates:
        return plan, provenance
    return plan.model_copy(update=updates), provenance


def preferences_direction_text(preferences: EditorialMemoryPreferences) -> str:
    """Compact, text-only direction injected into the gateway planner prompt.

    Never leaks source text; only the typed preference values and the memory
    version are surfaced so the gateway planner can honor them.
    """

    parts = [f"Consented editorial memory {preferences.memory_version[:32]}:"]
    if preferences.cut_style:
        parts.append(f"cut pace {preferences.cut_style}")
    if preferences.broll_density is not None:
        parts.append(f"b-roll density {preferences.broll_density.value}")
    if preferences.caption_style:
        parts.append(f"caption style {preferences.caption_style}")
    if preferences.music_mood:
        parts.append(f"music mood {preferences.music_mood}")
    if preferences.effects:
        parts.append("effects " + ", ".join(preferences.effects))
    return " ".join(parts)[:400]
