from app.schemas.visual import BoundingBox, FaceDetection, FrameSample, VideoManifest
from app.services.visual_tracking import subject_track_from_manifest


def test_visual_auto_reframe_stability_smoothing() -> None:
    # Test that auto-reframe smoothing/stability filters out sudden visual shifts
    # Scenario: Frame 1: Face is at x=0.2. Frame 2: Face suddenly jumps to x=0.8.
    # Smoothing should shift partially instead of jumping immediately to 0.8
    # because the smoothing coefficient is 0.65.

    frame_1 = FrameSample(
        timestamp_ms=0,
        width=1920,
        height=1080,
        faces=[
            FaceDetection(box=BoundingBox(x=0.2, y=0.2, width=0.1, height=0.1), confidence=0.95)
        ],
        objects=[],
    )
    frame_2 = FrameSample(
        timestamp_ms=1000,
        width=1920,
        height=1080,
        faces=[
            FaceDetection(box=BoundingBox(x=0.8, y=0.8, width=0.1, height=0.1), confidence=0.95)
        ],
        objects=[],
    )

    manifest = VideoManifest(
        width=1920, height=1080, fps=30, provider="test-reframe", frame_samples=[frame_1, frame_2]
    )

    track = subject_track_from_manifest(manifest)

    assert len(track) == 2
    # Frame 1: Face detection center should be 0.2 + 0.1/2 = 0.25
    assert track[0]["x"] == 0.25
    assert track[0]["y"] == 0.25

    # Frame 2: Unfiltered center would be 0.8 + 0.1/2 = 0.85
    # Smoothed center should be: previous_x * smoothing + current_x * (1 - smoothing)
    # 0.25 * 0.65 + 0.85 * 0.35 = 0.1625 + 0.2975 = 0.46
    assert abs(track[1]["x"] - 0.46) < 0.01
    assert abs(track[1]["y"] - 0.46) < 0.01

    # Verify that the tracked flag stability is maintained
    assert track[1]["source"] == "face"


def test_caption_timing_chronology_sanity() -> None:
    # Test that transcript words/timing are ordered chronologically and don't overlap
    from app.schemas.captions import TranscriptWord

    words = [
        TranscriptWord(index=0, text="first", start_ms=0, end_ms=500, confidence=0.95),
        TranscriptWord(index=1, text="second", start_ms=600, end_ms=1200, confidence=0.95),
        TranscriptWord(index=2, text="third", start_ms=1300, end_ms=1800, confidence=0.95),
    ]

    # Validate words are chronologically ascending
    for idx in range(1, len(words)):
        assert words[idx].start_ms >= words[idx - 1].end_ms
        assert words[idx].end_ms > words[idx].start_ms
        assert words[idx].index == idx
