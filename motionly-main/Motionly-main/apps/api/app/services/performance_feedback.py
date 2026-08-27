from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from app.schemas.performance_feedback import (
    DurationBucket,
    PerformanceFeedbackSuggestion,
    PerformanceFeedbackSuggestionKind,
)


@dataclass(frozen=True)
class EditFeatureSnapshot:
    """Only allow-listed, non-content edit attributes linked to a published export."""

    caption_preset: str | None
    hook_style: str | None
    duration_bucket: DurationBucket
    template_id: str | None
    captions_enabled: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "caption_preset": self.caption_preset,
            "hook_style": self.hook_style,
            "duration_bucket": self.duration_bucket.value,
            "template_id": self.template_id,
            "captions_enabled": self.captions_enabled,
        }


def build_edit_feature_snapshot(
    *, render_job, export, plan=None, caption_track=None
) -> EditFeatureSnapshot:
    """Create a compact feature snapshot and intentionally exclude all source/content fields."""
    duration_ms = export.duration_ms
    if duration_ms is None and plan is not None:
        duration_ms = int(plan.target_duration_s) * 1000
    duration_seconds = (duration_ms or 0) / 1000
    duration_bucket = (
        DurationBucket.UNKNOWN
        if duration_seconds <= 0
        else DurationBucket.SHORT
        if duration_seconds < 30
        else DurationBucket.MEDIUM
        if duration_seconds <= 60
        else DurationBucket.LONG
    )
    return EditFeatureSnapshot(
        caption_preset=(str(caption_track.preset) if caption_track is not None else None),
        hook_style=(str(plan.style) if plan is not None else None),
        duration_bucket=duration_bucket,
        template_id=(str(render_job.template_id) if render_job.template_id else None),
        captions_enabled=render_job.caption_track_id is not None,
    )


def retention_rate(metric) -> float | None:
    """Use completion when supplied; otherwise derive a bounded rate from duration."""
    if metric.completion_rate is not None:
        return float(metric.completion_rate)
    if (
        metric.average_view_duration_seconds is not None
        and metric.duration_bucket != DurationBucket.UNKNOWN
        and metric.duration_seconds is not None
        and metric.duration_seconds > 0
    ):
        return min(1.0, float(metric.average_view_duration_seconds) / metric.duration_seconds)
    return None


def build_performance_suggestions(
    metrics: Iterable[object], *, minimum_examples: int
) -> list[PerformanceFeedbackSuggestion]:
    """Return observational recommendations, never causal or audience-level claims."""
    scored = [(metric, retention_rate(metric)) for metric in metrics]
    scored = [(metric, score) for metric, score in scored if score is not None]
    if len(scored) < minimum_examples:
        return []

    suggestions: list[PerformanceFeedbackSuggestion] = []
    for kind, attribute in (
        (PerformanceFeedbackSuggestionKind.CAPTIONS, "caption_preset"),
        (PerformanceFeedbackSuggestionKind.HOOK_STYLE, "hook_style"),
        (PerformanceFeedbackSuggestionKind.DURATION, "duration_bucket"),
    ):
        groups: dict[str, list[float]] = defaultdict(list)
        for metric, score in scored:
            value = getattr(metric, attribute, None)
            if value and value != DurationBucket.UNKNOWN:
                groups[str(getattr(value, "value", value))].append(float(score))
        if not groups:
            continue
        value, values = max(
            groups.items(), key=lambda item: (sum(item[1]) / len(item[1]), len(item[1]), item[0])
        )
        average = sum(values) / len(values)
        sample_count = len(values)
        confidence = "high" if sample_count >= 8 else "medium" if sample_count >= 4 else "limited"
        if kind == PerformanceFeedbackSuggestionKind.CAPTIONS:
            instruction = (
                f"Consider the {value} caption preset: across {sample_count} consented published "
                f"videos it had an average {average:.0%} retention rate."
            )
        elif kind == PerformanceFeedbackSuggestionKind.HOOK_STYLE:
            instruction = (
                f"Try a {value} opening style: {sample_count} consented published videos using it "
                f"averaged {average:.0%} retention."
            )
        else:
            friendly_value = value.replace("_", " ")
            instruction = (
                f"Test a {friendly_value} edit: {sample_count} consented published videos in this "
                f"duration range averaged {average:.0%} retention."
            )
        suggestions.append(
            PerformanceFeedbackSuggestion(
                kind=kind,
                recommended_value=value,
                editor_instruction=instruction,
                sample_count=sample_count,
                average_retention_rate=round(average, 4),
                confidence=confidence,
            )
        )
    return suggestions
