from app.schemas.visual import (
    BoundingBox,
    FaceDetection,
    FrameSample,
    ObjectDetection,
    VideoManifest,
)
from app.services.visual_tracking import subject_track_from_manifest


def test_subject_track_prefers_faces_then_falls_back_to_objects_and_smooths() -> None:
    manifest = VideoManifest(
        provider="fixture",
        frame_samples=[
            FrameSample(
                timestamp_ms=0,
                width=1920,
                height=1080,
                faces=[
                    FaceDetection(
                        confidence=0.9,
                        box=BoundingBox(x=0.1, y=0.2, width=0.2, height=0.3),
                    )
                ],
                objects=[
                    ObjectDetection(
                        label="person",
                        confidence=0.99,
                        box=BoundingBox(x=0.7, y=0.1, width=0.2, height=0.5),
                    )
                ],
            ),
            FrameSample(
                timestamp_ms=1000,
                width=1920,
                height=1080,
                objects=[
                    ObjectDetection(
                        label="person",
                        confidence=0.8,
                        box=BoundingBox(x=0.7, y=0.2, width=0.2, height=0.4),
                    )
                ],
            ),
        ],
    )

    track = subject_track_from_manifest(manifest)

    assert [point["source"] for point in track] == ["face", "object"]
    assert track[0]["x"] == 0.2
    assert 0.2 < float(track[1]["x"]) < 0.8
