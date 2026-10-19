export const VIEWER_IP_HEADER: "x-motionly-viewer-ip";
export function viewerAddressHeaders(headers: {
  get(name: string): string | null;
}): Record<string, string>;
