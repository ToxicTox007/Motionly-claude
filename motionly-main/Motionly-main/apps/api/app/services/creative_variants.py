from collections.abc import Sequence

from app.schemas.captions import CaptionPresetKey
from app.schemas.creative_variants import (
    CreativeBrollVariant,
    CreativeCaptionVariant,
    CreativeHookVariant,
    CreativeOpeningFrameVariant,
    CreativePacingVariant,
    CreativeVariant,
    CreativeVariantDimension,
)
from app.schemas.plans import BrollDensity, EditPlan

_CAPTION_ALTERNATIVES = (
    CreativeCaptionVariant(
        preset=CaptionPresetKey.BOLD_INFLUENCER,
        label="Bold attention captions",
        rationale="Large, high-contrast word pops make the first seconds easier to scan.",
    ),
    CreativeCaptionVariant(
        preset=CaptionPresetKey.MINIMAL_LINE,
        label="Minimal editorial captions",
        rationale="A quiet single-line treatment keeps focus on the speaker and the opening frame.",
    ),
    CreativeCaptionVariant(
        preset=CaptionPresetKey.EDUCATIONAL_CARD,
        label="Step-card captions",
        rationale="Structured cards make the promise and proof easier to follow.",
    ),
    CreativeCaptionVariant(
        preset=CaptionPresetKey.VOGUE_WHITE,
        label="Editorial white captions",
        rationale="An elevated type-on treatment creates a cleaner premium contrast.",
    ),
)

_BROLL_ALTERNATIVES = (
    CreativeBrollVariant(
        density=BrollDensity.HIGH,
        direction="Use proof-first inserts around each claim.",
        shot_strategy="Product close-ups, screen details, and result cutaways lead each beat.",
    ),
    CreativeBrollVariant(
        density=BrollDensity.MEDIUM,
        direction="Use contextual cutaways only at semantic transitions.",
        shot_strategy=(
            "Environment, reaction, and process shots reinforce without " "interrupting delivery."
        ),
    ),
    CreativeBrollVariant(
        density=BrollDensity.LOW,
        direction="Keep the speaker dominant and reserve inserts for the payoff.",
        shot_strategy="One clear proof visual per major idea; avoid decorative cutaways.",
    ),
)

_PACING_ALTERNATIVES = (
    CreativePacingVariant(
        label="Fast proof loop",
        cut_duration_multiplier=0.78,
        direction="Front-load short cuts and remove breaths before the first payoff.",
    ),
    CreativePacingVariant(
        label="Measured authority",
        cut_duration_multiplier=1.18,
        direction="Hold key statements long enough to establish trust before each cutaway.",
    ),
    CreativePacingVariant(
        label="Rhythmic story beat",
        cut_duration_multiplier=0.92,
        direction="Alternate concise talking-head beats with one deliberate visual reset.",
    ),
)

_OPENING_FRAME_ALTERNATIVES = (
    (
        "Outcome first",
        "Show the result before context.",
        "Visible outcome plus a concise benefit headline.",
    ),
    (
        "Question card",
        "Open on the tension the viewer wants resolved.",
        "High-contrast question with a clear subject silhouette.",
    ),
    (
        "Before / after",
        "Lead with a visual contrast between the old and desired state.",
        "Split-state thumbnail with the improved outcome emphasized.",
    ),
)


def build_creative_variants(
    *,
    plan: EditPlan,
    broll_density: BrollDensity,
    dimensions: Sequence[CreativeVariantDimension],
    variant_count: int,
) -> list[CreativeVariant]:
    """Create transparent review directions without changing any source artifact."""
    focus = _focus_phrase(plan)
    base_hook = CreativeHookVariant(
        label="Direct promise",
        text=_bounded(f"What {focus} changes", 180),
    )
    base_caption = CreativeCaptionVariant(
        preset=CaptionPresetKey.FOUNDER_CLEAN,
        label="Clean captions",
        rationale="A balanced baseline preserves legibility without dominating the opening.",
    )
    base_broll = CreativeBrollVariant(
        density=broll_density,
        direction="Keep the current plan's B-roll intent.",
        shot_strategy="Use the planned semantic anchors and preserve the speaker-led opening.",
    )
    base_pacing = CreativePacingVariant(
        label="Plan baseline",
        cut_duration_multiplier=1,
        direction="Keep the active plan's current cut cadence.",
    )
    opening_start_ms = plan.cuts[0].start_ms if plan.cuts else 0
    base_opening = CreativeOpeningFrameVariant(
        label="Current opening",
        start_ms=opening_start_ms,
        frame_direction="Keep the active plan's first shot and subject framing.",
        thumbnail_direction="Use the existing opening frame as the comparison control.",
    )

    variants: list[CreativeVariant] = []
    for index in range(variant_count):
        primary_dimension = dimensions[index % len(dimensions)]
        alternative_index = index // len(dimensions)
        hook = base_hook
        caption = base_caption
        broll = base_broll
        pacing = base_pacing
        opening_frame = base_opening

        if primary_dimension == CreativeVariantDimension.HOOK:
            hook = _hook_alternative(focus, alternative_index)
        elif primary_dimension == CreativeVariantDimension.CAPTION_STYLE:
            caption = _CAPTION_ALTERNATIVES[alternative_index % len(_CAPTION_ALTERNATIVES)]
        elif primary_dimension == CreativeVariantDimension.BROLL:
            broll = _BROLL_ALTERNATIVES[alternative_index % len(_BROLL_ALTERNATIVES)]
        elif primary_dimension == CreativeVariantDimension.PACING:
            pacing = _PACING_ALTERNATIVES[alternative_index % len(_PACING_ALTERNATIVES)]
        elif primary_dimension == CreativeVariantDimension.OPENING_FRAME:
            label, frame_direction, thumbnail_direction = _OPENING_FRAME_ALTERNATIVES[
                alternative_index % len(_OPENING_FRAME_ALTERNATIVES)
            ]
            opening_frame = CreativeOpeningFrameVariant(
                label=label,
                start_ms=opening_start_ms,
                frame_direction=frame_direction,
                thumbnail_direction=thumbnail_direction,
            )

        number = index + 1
        primary_label = primary_dimension.value.replace("_", " ").title()
        variants.append(
            CreativeVariant(
                id=f"variant-{primary_dimension.value}-{number}",
                ordinal=number,
                label=f"{primary_label} test {number}",
                primary_dimension=primary_dimension,
                hook=hook,
                caption=caption,
                broll=broll,
                pacing=pacing,
                opening_frame=opening_frame,
                comparison_notes=[
                    f"Primary test: {primary_label.lower()}.",
                    "All other directions stay at the active-plan control for a fair comparison.",
                    (
                        "Review-only: selecting this direction does not render, alter "
                        "the timeline, or publish."
                    ),
                ],
            )
        )
    return variants


def _hook_alternative(focus: str, index: int) -> CreativeHookVariant:
    alternatives = (
        ("Contrarian hook", f"Stop overlooking {focus}."),
        ("Curiosity hook", f"The overlooked part of {focus}."),
        ("Outcome hook", f"Use {focus} to get a clearer result."),
    )
    label, copy = alternatives[index % len(alternatives)]
    return CreativeHookVariant(label=label, text=_bounded(copy, 180))


def _focus_phrase(plan: EditPlan) -> str:
    if plan.caption_highlights:
        return _bounded(plan.caption_highlights[0].word.strip() or "the key insight", 80)
    if plan.cuts:
        return _bounded(plan.cuts[0].reason.strip() or "the key insight", 80)
    return "the key insight"


def _bounded(value: str, maximum: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= maximum:
        return normalized
    return f"{normalized[: maximum - 1].rsplit(' ', 1)[0]}…"
