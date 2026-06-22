from hashlib import sha256

from app.schemas.regeneration import RegenerateRequest, RegenerationAction, RegenerationLayer

PIPELINE_LAYERS = [
    RegenerationLayer.TRANSCRIPT,
    RegenerationLayer.CAPTIONS,
    RegenerationLayer.PLAN,
    RegenerationLayer.CUTS,
    RegenerationLayer.BROLL,
    RegenerationLayer.MUSIC,
    RegenerationLayer.EFFECTS,
    RegenerationLayer.RENDER,
]

DOWNSTREAM_DEPENDENCIES = {
    RegenerationLayer.TRANSCRIPT: PIPELINE_LAYERS,
    RegenerationLayer.CAPTIONS: PIPELINE_LAYERS[1:],
    RegenerationLayer.PLAN: PIPELINE_LAYERS[2:],
    RegenerationLayer.CUTS: [
        RegenerationLayer.CUTS,
        RegenerationLayer.BROLL,
        RegenerationLayer.MUSIC,
        RegenerationLayer.EFFECTS,
        RegenerationLayer.RENDER,
    ],
    RegenerationLayer.BROLL: [RegenerationLayer.BROLL, RegenerationLayer.RENDER],
    RegenerationLayer.MUSIC: [RegenerationLayer.MUSIC, RegenerationLayer.RENDER],
    RegenerationLayer.EFFECTS: [RegenerationLayer.EFFECTS, RegenerationLayer.RENDER],
    RegenerationLayer.RENDER: [RegenerationLayer.RENDER],
}

ACTION_LAYER_MAP = {
    RegenerationAction.MAKE_PUNCHIER: [RegenerationLayer.PLAN],
    RegenerationAction.MORE_CINEMATIC: [RegenerationLayer.EFFECTS, RegenerationLayer.MUSIC],
    RegenerationAction.LESS_BROLL: [RegenerationLayer.BROLL],
    RegenerationAction.CHANGE_MUSIC: [RegenerationLayer.MUSIC],
}


def affected_layers_for_request(payload: RegenerateRequest) -> list[RegenerationLayer]:
    roots = set(payload.layers)
    if payload.action:
        roots.update(ACTION_LAYER_MAP[payload.action])

    controls = payload.controls
    if controls.caption_style is not None:
        roots.add(RegenerationLayer.CAPTIONS)
    if controls.broll_density is not None:
        roots.add(RegenerationLayer.BROLL)
    if controls.music_volume is not None or controls.sfx_intensity is not None:
        roots.add(RegenerationLayer.MUSIC)
    if controls.effects_intensity is not None:
        roots.add(RegenerationLayer.EFFECTS)
    if controls.duration_preset is not None:
        roots.add(RegenerationLayer.PLAN)

    affected = {layer for root in roots for layer in DOWNSTREAM_DEPENDENCIES[root]}
    if affected and RegenerationLayer.RENDER not in affected:
        affected.add(RegenerationLayer.RENDER)
    return [layer for layer in PIPELINE_LAYERS if layer in affected]


def preserved_layers(affected_layers: list[RegenerationLayer]) -> list[RegenerationLayer]:
    affected = set(affected_layers)
    return [layer for layer in PIPELINE_LAYERS if layer not in affected]


def settings_summary(payload: RegenerateRequest) -> dict[str, object]:
    controls = payload.controls.model_dump(mode="json", exclude_none=True)
    return {
        "action": payload.action.value if payload.action else None,
        "controls": controls,
        "export_options": payload.export_options.model_dump(mode="json"),
        "summary": ", ".join(_summary_labels(payload)) or "Custom regeneration",
    }


def build_regeneration_cache_key(payload: RegenerateRequest) -> str:
    raw = payload.model_dump_json()
    return sha256(raw.encode("utf-8")).hexdigest()


def _summary_labels(payload: RegenerateRequest) -> list[str]:
    labels = []
    if payload.action:
        labels.append(payload.action.value.replace("_", " "))
    for key, value in payload.controls.model_dump(exclude_none=True).items():
        labels.append(f"{key.replace('_', ' ')}: {value}")
    labels.append(
        f"{payload.export_options.platform.value} {payload.export_options.resolution.value}"
    )
    return labels
