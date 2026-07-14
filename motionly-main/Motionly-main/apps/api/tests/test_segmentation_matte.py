from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


from app.services.segmentation_matte import generate_selfie_segmentation_matte


class _FakeSelfieSegmentation:
    def process(self, rgb: Any) -> object:
        height, width = rgb.shape[:2]
        mask = np.zeros((height, width), dtype=np.float32)
        mask[:, int(width * 0.55) :] = 1.0
        return SimpleNamespace(segmentation_mask=mask)


@pytest.mark.skipif(not HAS_CV2, reason="cv2 not installed")
def test_generate_selfie_segmentation_matte_writes_grayscale_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "matte.mp4"
    writer = cv2.VideoWriter(
        str(source_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        10.0,
        (160, 90),
    )
    for _ in range(5):
        frame = np.zeros((90, 160, 3), dtype=np.uint8)
        frame[:, 90:] = (40, 180, 40)
        writer.write(frame)
    writer.release()

    monkeypatch.setattr(
        "app.services.segmentation_matte._encode_matte_video",
        lambda *, frame_dir, output_path, fps: output_path.write_bytes(b"matte"),
    )

    stats = generate_selfie_segmentation_matte(
        cv2=cv2,
        np=np,
        selfie_segmentation=_FakeSelfieSegmentation(),
        source_path=source_path,
        output_path=output_path,
    )

    assert output_path.exists()
    assert stats["frame_count"] == 5
    assert stats["width"] == 160
    assert stats["height"] == 90
