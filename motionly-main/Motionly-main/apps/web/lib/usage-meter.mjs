// Text for the render-usage meter. renders_limit, remaining_renders and
// percent_used are null when the workspace is unmetered.

export function usageMeter(usage) {
  const used = usage.renders_used;
  if (usage.renders_limit === null || usage.renders_limit === undefined) {
    return {
      metered: false,
      label: `${used} renders · unlimited`,
      percent: null,
      percentLabel: null,
      remainingLabel: "Unlimited renders this period.",
      ariaLabel: null,
    };
  }
  const percent = Math.max(0, Math.min(100, Number(usage.percent_used ?? 0)));
  return {
    metered: true,
    label: `${used}/${usage.renders_limit} renders`,
    percent,
    percentLabel: `${percent}%`,
    remainingLabel: `${usage.remaining_renders ?? Math.max(0, usage.renders_limit - used)} remaining this period.`,
    ariaLabel: `Renders used this period: ${used} of ${usage.renders_limit}`,
  };
}
