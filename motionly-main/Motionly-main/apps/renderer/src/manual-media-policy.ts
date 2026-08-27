const SAFE_BACKGROUND = /^[A-Za-z0-9#(),.%+\-\s]*$/;
const CSS_URL = /url\s*\(/i;
const MAX_DATA_AUDIO_LENGTH = 1_398_112;

export function safeRendererMediaUrl(
  value: unknown,
  { allowDataAudio = false }: { allowDataAudio?: boolean } = {},
): string | null {
  if (typeof value !== "string" || !value || /[\u0000-\u001f\u007f\s]/.test(value)) {
    return null;
  }
  if (
    allowDataAudio &&
    value.startsWith("data:audio/wav;base64,") &&
    value.length <= MAX_DATA_AUDIO_LENGTH
  ) {
    return value;
  }
  try {
    const parsed = new URL(value);
    const loopback = parsed.hostname === "127.0.0.1" || parsed.hostname === "::1";
    return (
      parsed.protocol === "http:" &&
      loopback &&
      /^\/media\/\d+$/.test(parsed.pathname) &&
      !parsed.username &&
      !parsed.password
    )
      ? parsed.toString()
      : null;
  } catch {
    return null;
  }
}

export function safeRendererBackground(
  value: unknown,
  fallback = "transparent",
): string {
  if (typeof value !== "string") return fallback;
  const candidate = value.trim();
  if (
    !candidate ||
    candidate.length > 1_000 ||
    !SAFE_BACKGROUND.test(candidate) ||
    CSS_URL.test(candidate)
  ) {
    return fallback;
  }
  return candidate;
}

export function manualVisualBackground(
  properties: Record<string, unknown>,
  elementType: string,
): string {
  const fallback = elementType === "shape" ? "#14b8a6" : "transparent";
  return safeRendererBackground(
    properties.background ?? properties.backgroundColor ?? fallback,
    fallback,
  );
}
