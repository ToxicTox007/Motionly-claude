"""Normalizer parity tests (plan Task 15, Section 2.10 "Motionly normalizers").

The object-detection cases are ported from the archived object-detection adapter's test suite
so Task 17's differential test against the archived adapter can pass: the same
label aliasing, whitespace collapse, 80-char cut, clamping, rounding, sort and
top-N behaviour, and the same vertex-to-box conversion where an omitted
coordinate reads as 0.
"""

from __future__ import annotations

from app.services.inference_normalization import (
    RawDetection,
    box_from_vertices_norm,
    box_from_xywh_norm,
    clamp01,
    map_stem_name,
    normalize_detections,
    normalize_label,
    sort_diarization_segments,
)
from app.services.model_gateway import DiarizationSegmentOutput

# Historical COCO alias map (data the normalizer reads).
ALIASES = {
    "mobile phone": "cell phone",
    "television": "tv",
    "man": "person",
    "woman": "person",
}


def test_label_normalization_matches_archived_adapter() -> None:
    assert normalize_label("  Mobile   Phone ", ALIASES) == "cell phone"
    assert normalize_label("Television", ALIASES) == "tv"
    assert normalize_label("Woman", ALIASES) == "person"
    assert normalize_label("Bicycle wheel", ALIASES) == "bicycle wheel"
    # The 80-char cut happens after aliasing.
    assert len(normalize_label("x" * 200, ALIASES)) == 80
    # No alias table still lowercases and collapses whitespace.
    assert normalize_label("  Laptop ") == "laptop"


def test_clamp01() -> None:
    assert clamp01(-0.5) == 0.0
    assert clamp01(1.7) == 1.0
    assert clamp01(0.3) == 0.3


def test_vertices_box_omitted_coordinates_count_as_zero() -> None:
    # The PHONE case from the archived test: omitted x/y read as 0.
    vertices = [{}, {"x": 0.25}, {"x": 0.25, "y": 0.5}, {"y": 0.5}]
    assert box_from_vertices_norm(vertices) == (0.0, 0.0, 0.25, 0.5)


def test_vertices_box_axis_aligned() -> None:
    vertices = [
        {"x": 0.1, "y": 0.2},
        {"x": 0.6, "y": 0.2},
        {"x": 0.6, "y": 0.7},
        {"x": 0.1, "y": 0.7},
    ]
    assert box_from_vertices_norm(vertices) == (0.1, 0.2, 0.5, 0.5)


def test_vertices_box_clamps_out_of_range() -> None:
    vertices = [{"x": -0.2, "y": 0.1}, {"x": 1.4, "y": 1.2}]
    assert box_from_vertices_norm(vertices) == (0.0, 0.1, 1.0, 0.9)


def test_vertices_box_empty_is_none() -> None:
    assert box_from_vertices_norm([]) is None
    assert box_from_vertices_norm(None) is None


def test_xywh_box_list_and_mapping() -> None:
    assert box_from_xywh_norm([0.1, 0.2, 0.3, 0.4]) == (0.1, 0.2, 0.3, 0.4)
    assert box_from_xywh_norm({"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}) == (0.1, 0.2, 0.3, 0.4)
    assert box_from_xywh_norm(
        {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
    ) == (0.1, 0.2, 0.3, 0.4)
    # Clamped.
    assert box_from_xywh_norm([1.5, -0.2, 2.0, 0.4]) == (1.0, 0.0, 1.0, 0.4)
    assert box_from_xywh_norm(None) is None


def test_detections_sorted_and_capped_matches_archived_adapter() -> None:
    wide = RawDetection("Box", 1.7, [{"x": -0.2, "y": 0.1}, {"x": 1.4, "y": 1.2}])
    tiny = [{"x": 0, "y": 0}, {"x": 0.1, "y": 0.1}]
    many = [RawDetection(f"Item {i}", i / 100, tiny) for i in range(40)]

    detections = normalize_detections(
        [wide, *many], box_format="vertices_norm", max_results=5
    )

    assert len(detections) == 5
    assert (detections[0].label, detections[0].confidence) == ("box", 1.0)
    assert (
        detections[0].x,
        detections[0].y,
        detections[0].width,
        detections[0].height,
    ) == (0.0, 0.1, 1.0, 0.9)


def test_detections_apply_aliases_and_drop_empty_labels() -> None:
    box = [{"x": 0.0, "y": 0.0}, {"x": 0.5, "y": 0.5}]
    raw = [
        RawDetection("Mobile phone", 0.55, box),
        RawDetection("", 0.9, box),  # empty label is dropped
        RawDetection("Laptop", 0.91, box),
    ]

    detections = normalize_detections(
        raw, box_format="vertices_norm", max_results=24, label_aliases=ALIASES
    )

    assert [(d.label, d.confidence) for d in detections] == [
        ("laptop", 0.91),
        ("cell phone", 0.55),
    ]


def test_detection_without_box_is_dropped() -> None:
    raw = [RawDetection("thing", 0.5, None)]
    assert normalize_detections(raw, box_format="vertices_norm", max_results=24) == ()


def test_xywh_detections() -> None:
    raw = [RawDetection("cat", 0.8, [0.1, 0.1, 0.2, 0.2])]
    detections = normalize_detections(raw, box_format="xywh_norm", max_results=24)
    assert detections[0].label == "cat"
    assert (detections[0].x, detections[0].width) == (0.1, 0.2)


def test_sort_diarization_segments() -> None:
    segments = (
        DiarizationSegmentOutput(speaker="B", start_ms=2000, end_ms=3000),
        DiarizationSegmentOutput(speaker="A", start_ms=0, end_ms=1000),
        DiarizationSegmentOutput(speaker="A", start_ms=0, end_ms=500),
    )
    ordered = sort_diarization_segments(segments)
    assert [(s.start_ms, s.end_ms) for s in ordered] == [(0, 500), (0, 1000), (2000, 3000)]


def test_map_stem_name() -> None:
    name_map = {"track_a": "vocals", "track_b": "no_vocals"}
    assert map_stem_name("track_a", name_map) == "vocals"
    assert map_stem_name("track_b", name_map) == "no_vocals"
    assert map_stem_name("track_c", name_map) is None
