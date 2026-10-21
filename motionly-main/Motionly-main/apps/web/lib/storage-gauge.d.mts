export interface StorageQuotaInput {
  used_bytes?: number;
  max_bytes?: number;
  percentage?: number;
}

export interface StorageGauge {
  metered: boolean;
  label: string;
  percent: number | null;
  percentLabel: string | null;
  ariaLabel: string;
}

export function formatBytes(bytes: number): string;

export function storageGauge(
  quota: StorageQuotaInput | null | undefined,
): StorageGauge;
