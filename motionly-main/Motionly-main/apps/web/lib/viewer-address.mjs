// The viewer's address for Auth's per-viewer rate limits. The ingress sets
// x-motionly-viewer-ip (from CloudFront-Viewer-Address, after the origin
// guard; infra/Caddyfile.origin-guard). Auth calls the web server makes on a
// viewer's behalf pass it on, or Auth would see one address for everyone.
export const VIEWER_IP_HEADER = "x-motionly-viewer-ip";

const IPV4 =
  /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;

function isIPv6(value) {
  if (value.length > 45 || !value.includes(":")) return false;
  const halves = value.split("::");
  if (halves.length > 2) return false;
  const groups = halves.flatMap((half) => (half === "" ? [] : half.split(":")));
  let count = 0;
  for (const [index, group] of groups.entries()) {
    if (/^[0-9A-Fa-f]{1,4}$/.test(group)) count += 1;
    else if (index === groups.length - 1 && IPV4.test(group)) count += 2;
    else return false;
  }
  return halves.length === 2 ? count < 8 : count === 8;
}

export function viewerAddressHeaders(headers) {
  const value = headers.get(VIEWER_IP_HEADER);
  if (!value || !(IPV4.test(value) || isIPv6(value))) return {};
  return { [VIEWER_IP_HEADER]: value };
}
