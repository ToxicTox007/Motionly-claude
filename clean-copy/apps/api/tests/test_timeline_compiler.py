from uuid import uuid4

from app.schemas.plans import EditPlan, TimelineDocument
from app.services.timeline_compiler import (
    compile_timeline,
    migrate_legacy_plan_to_timeline,
    project_timeline_to_legacy_plan,
)


def legacy_plan() -> EditPlan:
    return EditPlan.model_validate(
        {
            "schema_version": 2,
            "style": "founder",
            "target_duration_s": 10,
            "cuts": [
                {"start_ms": 0, "end_ms": 1000, "reason": "hook"},
                {"start_ms": 3000, "end_ms": 4500, "reason": "payoff"},
            ],
            "caption_highlights": [],
            "broll_requests": [],
            "zoom_moments": [],
            "music_mood": "confident",
            "effects": ["zoom_punch"],
            "color_grade": "clean_founder",
            "risk_flags": [],
            "manual_elements": [
                {
                    "id": "transition-1",
                    "type": "transition",
                    "name": "Dissolve",
                    "zone": "timeline",
                    "start_ms": 900,
                    "end_ms": 1100,
                }
            ],
        }
    )


def test_legacy_plan_migrates_to_contiguous_versioned_timeline() -> None:
    video_id = uuid4()
    migrated = migrate_legacy_plan_to_timeline(
        plan=legacy_plan(),
        source_video_id=video_id,
        source_duration_ms=5000,
    )

    assert migrated.timeline is not None
    assert migrated.timeline.schema_version == 1
    assert migrated.timeline.duration_ms == 2500
    assert [clip.output_start_ms for clip in migrated.timeline.clips] == [0, 1000]
    assert migrated.timeline.clips[1].source_start_ms == 3000
    assert migrated.timeline.transitions[0].id == "transition-1"


def test_compiler_merges_every_render_layer_and_retimes_captions() -> None:
    plan = legacy_plan()
    timeline = compile_timeline(
        plan=plan,
        source_video_id=uuid4(),
        source_duration_ms=5000,
        captions=[
            {
                "id": "caption-1",
                "index": 0,
                "start_ms": 3200,
                "end_ms": 4000,
                "text": "the payoff",
                "line_count": 1,
                "words": [{"text": "payoff", "start_ms": 3300, "end_ms": 3800}],
            }
        ],
        caption_style={"font_family": "Inter"},
        broll=[
            {
                "id": "broll-1",
                "start_ms": 3200,
                "end_ms": 3800,
                "src": "https://cdn.example/broll.mp4",
            }
        ],
        music={"music_track_id": "track-1"},
        source_stems={"vocals": {"path": "vocals.wav"}},
        effects_config={"motion": {"keyframes": [{"at_ms": 500}]}},
    )

    assert timeline.captions[0].start_ms == 1200
    assert timeline.captions[0].words[0]["start_ms"] == 1300
    assert timeline.broll[0].id == "broll-1"
    assert timeline.broll[0].start_ms == 1200
    assert timeline.audio.music == {"music_track_id": "track-1"}
    assert timeline.audio.source_stems == {"vocals": {"path": "vocals.wav"}}
    assert timeline.effects.keyframes == [{"at_ms": 500}]


def test_timeline_is_authoritative_with_legacy_plan_as_projection() -> None:
    plan = legacy_plan()
    timeline = TimelineDocument.model_validate(
        {
            **compile_timeline(
                plan=plan,
                source_video_id=uuid4(),
                source_duration_ms=5000,
            ).model_dump(mode="json"),
            "clips": [
                {
                    "id": "cut-0",
                    "source_start_ms": 2000,
                    "source_end_ms": 3500,
                    "output_start_ms": 0,
                    "output_end_ms": 1500,
                    "reason": "user-selected clip",
                }
            ],
            "duration_ms": 1500,
        }
    )

    projected = project_timeline_to_legacy_plan(plan=plan, timeline=timeline)

    assert projected.timeline == timeline
    assert projected.cuts[0].start_ms == 2000
    assert projected.cuts[0].end_ms == 3500
    assert projected.manual_elements == [*timeline.manual_layers, *timeline.transitions]
