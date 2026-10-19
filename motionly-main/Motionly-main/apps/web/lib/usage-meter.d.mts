export interface UsageCounts {
  renders_used: number;
  renders_limit: number | null;
  remaining_renders: number | null;
  percent_used: number | null;
}

export interface UsageMeter {
  metered: boolean;
  label: string;
  percent: number | null;
  percentLabel: string | null;
  remainingLabel: string;
  ariaLabel: string | null;
}

export function usageMeter(usage: UsageCounts): UsageMeter;
