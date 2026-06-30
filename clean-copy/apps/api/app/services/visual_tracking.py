from app.schemas.visual import VideoManifest


def subject_track_from_manifest(manifest: VideoManifest | None) -> list[dict[str, object]]:
    """Build a stable normalized subject track, preferring faces over objects."""
    if manifest is None:
        return []
    track: list[dict[str, object]] = []
    previous_x = previous_y = None
    smoothing = 0.65
    for frame in sorted(manifest.frame_samples, key=lambda item: item.timestamp_ms):
        source = "face"
        detections = frame.faces
        if not detections:
            source = "object"
            detections = [
                item for item in frame.objects if item.label.lower() in {"person", "face", "head"}
            ] or frame.objects
        if not detections:
            continue
        detection = max(detections, key=lambda item: item.confidence)
        x = detection.box.x + detection.box.width / 2
        y = detection.box.y + detection.box.height / 2
        if previous_x is not None and previous_y is not None:
            x = previous_x * smoothing + x * (1 - smoothing)
            y = previous_y * smoothing + y * (1 - smoothing)
        previous_x, previous_y = x, y
        track.append(
            {
                "timestamp_ms": frame.timestamp_ms,
                "x": round(max(0, min(1, x)), 5),
                "y": round(max(0, min(1, y)), 5),
                "width": round(detection.box.width, 5),
                "height": round(detection.box.height, 5),
                "confidence": round(detection.confidence, 5),
                "source": source,
            }
        )
    return track
