export type PanelResult<T> =
  | { status: "ok"; data: T }
  | { status: "error"; error: string };

export function panelErrorMessage(panelLabel: string, error: unknown): string;

export function loadPanel<T>(
  panelLabel: string,
  loader: () => Promise<T>,
): Promise<PanelResult<T>>;

export function settlePanels<L extends Record<string, () => Promise<unknown>>>(
  loaders: L,
): Promise<{
  [K in keyof L]: PanelResult<Awaited<ReturnType<L[K]>>>;
}>;

export function hasAnyPanelLoaded(
  panels: Record<string, PanelResult<unknown>>,
): boolean;

export function failedPanels(
  panels: Record<string, PanelResult<unknown>>,
): string[];
