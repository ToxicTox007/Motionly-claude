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
from app.schemas.plans import (
    BrollDensity,
    BrollRequest,
    CaptionHighlight,
    EditPlan,
    EditPlanCut,
    PlanOpeningFrame,
)

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
                    "Review-only: selecting this direction does not render or alter the timeline.",
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


class AppliedCreativeVariant:
    """Result of applying a creative variant to a plan.

    ``plan`` is a new EditPlan that differs from the source plan ONLY in the
    fields owned by the variant's primary dimension; ``broll_density`` is the
    density to persist on the plan record (only changed for a B-roll variant).
    """

    __slots__ = ("plan", "broll_density", "dimension")

    def __init__(
        self,
        *,
        plan: EditPlan,
        broll_density: BrollDensity,
        dimension: CreativeVariantDimension,
    ) -> None:
        self.plan = plan
        self.broll_density = broll_density
        self.dimension = dimension


def apply_creative_variant(
    *,
    plan: EditPlan,
    variant: CreativeVariant,
    broll_density: BrollDensity,
) -> AppliedCreativeVariant:
    """Turn a chosen creative variant into concrete plan changes.

    Only the variant's ``primary_dimension`` is applied, so the resulting plan
    differs from the source plan exactly in that dimension. Each dimension maps
    to real, persisted plan content (not just a recorded choice):

    - pacing: scale every cut's duration by ``cut_duration_multiplier`` (cuts
      repacked back-to-back) and recompute ``target_duration_s``.
    - caption_style: set ``plan.caption_preset`` to the chosen preset.
    - broll: set the plan record's B-roll density and trim ``broll_requests``.
    - hook: set ``plan.hook_text`` and refresh the opening caption highlight.
    - opening_frame: set ``plan.opening_frame`` and move the first cut's start.
    """
    dimension = variant.primary_dimension
    updates: dict[str, object] = {}
    next_density = broll_density

    if dimension == CreativeVariantDimension.PACING:
        updates["cuts"] = _rescale_cuts(plan.cuts, variant.pacing.cut_duration_multiplier)
        updates["target_duration_s"] = _retimed_target_duration(updates["cuts"])  # type: ignore[arg-type]
    elif dimension == CreativeVariantDimension.CAPTION_STYLE:
        updates["caption_preset"] = variant.caption.preset
    elif dimension == CreativeVariantDimension.BROLL:
        next_density = variant.broll.density
        updates["broll_requests"] = _trim_broll_requests(plan.broll_requests, variant.broll.density)
    elif dimension == CreativeVariantDimension.HOOK:
        updates["hook_text"] = variant.hook.text
        updates["caption_highlights"] = _hook_caption_highlights(plan, variant.hook)
    elif dimension == CreativeVariantDimension.OPENING_FRAME:
        updates["opening_frame"] = PlanOpeningFrame(
            label=variant.opening_frame.label,
            start_ms=variant.opening_frame.start_ms,
            frame_direction=variant.opening_frame.frame_direction,
            thumbnail_direction=variant.opening_frame.thumbnail_direction,
        )
        updates["cuts"] = _shift_first_cut_start(plan.cuts, variant.opening_frame.start_ms)

    new_plan = plan.model_copy(update=updates)
    return AppliedCreativeVariant(plan=new_plan, broll_density=next_density, dimension=dimension)


def _rescale_cuts(cuts: list[EditPlanCut], multiplier: float) -> list[EditPlanCut]:
    """Scale each cut's duration by ``multiplier`` and repack back-to-back.

    The timeline keeps the same first-cut start; each subsequent cut starts where
    the previous scaled cut ends, so the whole edit tightens or relaxes evenly.
    """
    if not cuts:
        return cuts
    cursor = cuts[0].start_ms
    rescaled: list[EditPlanCut] = []
    for cut in cuts:
        duration = max(1, round((cut.end_ms - cut.start_ms) * multiplier))
        rescaled.append(
            EditPlanCut(start_ms=cursor, end_ms=cursor + duration, reason=cut.reason)
        )
        cursor += duration
    return rescaled


def _retimed_target_duration(cuts: list[EditPlanCut]) -> int:
    total_ms = sum(cut.end_ms - cut.start_ms for cut in cuts)
    return max(10, min(90, round(total_ms / 1000)))


def _trim_broll_requests(
    requests: list[BrollRequest], density: BrollDensity
) -> list[BrollRequest]:
    if density == BrollDensity.NONE:
        return []
    if density == BrollDensity.LOW:
        return list(requests[:1])
    if density == BrollDensity.MEDIUM:
        return list(requests[: (len(requests) + 1) // 2])
    return list(requests)


def _hook_caption_highlights(plan: EditPlan, hook: CreativeHookVariant) -> list[CaptionHighlight]:
    """Refresh the opening highlight so the hook word pops at the plan start."""
    opening_ms = plan.cuts[0].start_ms if plan.cuts else 0
    hook_word = _bounded(hook.text.split()[0] if hook.text.split() else hook.text, 80)
    opening = CaptionHighlight(timestamp_ms=opening_ms, word=hook_word, effect="pulse")
    remainder = [
        highlight for highlight in plan.caption_highlights if highlight.timestamp_ms != opening_ms
    ]
    return [opening, *remainder][:12]


def _shift_first_cut_start(cuts: list[EditPlanCut], start_ms: int) -> list[EditPlanCut]:
    if not cuts:
        return cuts
    first = cuts[0]
    if start_ms >= first.end_ms:
        return cuts
    return [
        EditPlanCut(start_ms=start_ms, end_ms=first.end_ms, reason=first.reason),
        *cuts[1:],
    ]
