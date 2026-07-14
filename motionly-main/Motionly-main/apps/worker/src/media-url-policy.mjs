import { lookup as dnsLookup } from "node:dns/promises";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { BlockList, isIP } from "node:net";
import { Readable } from "node:stream";

const MAX_DATA_AUDIO_BYTES = 1_048_576;
const MAX_URL_LENGTH = 8_192;
export const MAX_MEDIA_REDIRECTS = 3;
const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308]);
const LOCAL_NAMES = new Set(["localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"]);
const LOCAL_SUFFIXES = [".localhost", ".local", ".localdomain", ".internal", ".lan", ".home"];
const STORAGE_BUCKET = /^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$/;
const HOST_LABEL = /^[a-z0-9-]+$/;

const forbiddenIpv4 = new BlockList();
const forbiddenIpv6 = new BlockList();
for (const [network, prefix] of [
  ["0.0.0.0", 8],
  ["10.0.0.0", 8],
  ["100.64.0.0", 10],
  ["127.0.0.0", 8],
  ["169.254.0.0", 16],
  ["172.16.0.0", 12],
  ["192.0.0.0", 24],
  ["192.0.2.0", 24],
  ["192.88.99.0", 24],
  ["192.168.0.0", 16],
  ["198.18.0.0", 15],
  ["198.51.100.0", 24],
  ["203.0.113.0", 24],
  ["224.0.0.0", 4],
  ["240.0.0.0", 4],
]) forbiddenIpv4.addSubnet(network, prefix, "ipv4");
for (const [network, prefix] of [
  ["::", 8],
  ["64:ff9b:1::", 48],
  ["100::", 64],
  ["2001::", 23],
  ["2001:db8::", 32],
  ["2002::", 16],
  ["3fff::", 20],
  ["5f00::", 16],
  ["fc00::", 7],
  ["fec0::", 10],
  ["fe80::", 10],
  ["ff00::", 8],
]) forbiddenIpv6.addSubnet(network, prefix, "ipv6");

export class MediaUrlPolicyError extends Error {
  constructor(message) {
    super(message);
    this.name = "MediaUrlPolicyError";
    this.code = "unsafe_media_url";
  }
}

export async function assertSafeOutboundMediaUrl(
  value,
  {
    allowSupabase = false,
    allowFixture = false,
    allowDataAudio = false,
    resolveDns = true,
    requireDns = true,
    lookup = dnsLookup,
  } = {},
) {
  if (typeof value !== "string" || !value || value.length > MAX_URL_LENGTH) {
    throw new MediaUrlPolicyError("Media URL is missing or too long.");
  }
  if ([...value].some((character) => character.charCodeAt(0) < 32 || /\s/.test(character))) {
    throw new MediaUrlPolicyError("Media URL contains invalid characters.");
  }
  if (value.startsWith("supabase://")) {
    if (!allowSupabase) throw new MediaUrlPolicyError("Internal media references are not allowed here.");
    validateSupabaseReference(value);
    return { value, kind: "supabase" };
  }
  if (value.startsWith("fixture://")) {
    if (!allowFixture) throw new MediaUrlPolicyError("Fixture media references are not allowed here.");
    validateFixtureReference(value);
    return { value, kind: "fixture" };
  }
  if (value.startsWith("data:")) {
    if (!allowDataAudio) throw new MediaUrlPolicyError("Data media URLs are not allowed here.");
    validateDataAudio(value);
    return { value, kind: "data_audio" };
  }

  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new MediaUrlPolicyError("Media URL is malformed.");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new MediaUrlPolicyError("Media URL scheme is not supported.");
  }
  if (!parsed.hostname) throw new MediaUrlPolicyError("Media URL host is missing.");
  if (parsed.username || parsed.password) {
    throw new MediaUrlPolicyError("Credential-bearing media URLs are not allowed.");
  }

  const hostname = parsed.hostname.replace(/^\[|\]$/g, "").replace(/\.$/, "").toLowerCase();
  const family = isIP(hostname);
  let addresses;
  if (family) {
    assertPublicAddress(hostname, family);
    addresses = [{ address: hostname, family }];
  } else {
    validateHostname(hostname);
    addresses = [];
    if (resolveDns) {
      let records;
      try {
        records = await lookup(hostname, { all: true, verbatim: true });
      } catch (error) {
        if (requireDns) throw new MediaUrlPolicyError("Media URL host could not be resolved.");
        records = [];
      }
      if (!Array.isArray(records) || (!records.length && requireDns)) {
        throw new MediaUrlPolicyError("Media URL host could not be resolved.");
      }
      addresses = records.map(normalizeLookupRecord);
      for (const record of addresses) assertPublicAddress(record.address, record.family);
    }
  }
  return { value, kind: "http", addresses };
}

export async function validateRenderMediaManifest(renderProps, options = {}) {
  const check = async (value, extra = {}) => {
    if (value === null || value === undefined || value === "") return;
    if (typeof value !== "string") throw new MediaUrlPolicyError("Media URL must be a string.");
    await assertSafeOutboundMediaUrl(value, { allowSupabase: true, ...options, ...extra });
  };

  await check(renderProps?.sourceVideo?.src);
  for (const asset of renderProps?.assets ?? []) {
    if (["video", "audio", "image"].includes(asset?.kind)) await check(asset.src);
  }
  for (const element of renderProps?.editPlan?.manual_elements ?? []) {
    if (!["broll", "music", "sfx"].includes(element?.type)) continue;
    for (const key of ["assetUrl", "src"]) {
      if (Object.hasOwn(element?.properties ?? {}, key)) {
        await check(element.properties[key], { allowDataAudio: ["music", "sfx"].includes(element.type) });
      }
    }
  }
  const track = renderProps?.musicSelection?.track;
  if (track) {
    for (const key of ["full_track_url", "preview_url"]) await check(track[key]);
    for (const key of ["audio_url", "download_url", "preview_url", "src"]) {
      await check(track.raw_metadata?.[key]);
    }
    for (const value of track.raw_metadata?.sfx_urls ?? []) {
      await check(value, { allowDataAudio: true });
    }
    for (const [bucketKey, pathKey] of [
      ["audio_bucket", "audio_path"],
      ["storage_bucket", "storage_path"],
      ["bucket", "path"],
    ]) {
      const bucket = track.raw_metadata?.[bucketKey];
      const path = track.raw_metadata?.[pathKey];
      if (bucket === undefined && path === undefined) continue;
      if (typeof bucket !== "string" || typeof path !== "string") {
        throw new MediaUrlPolicyError("Internal media reference is invalid.");
      }
      await check(`supabase://${bucket}/${path}`);
    }
  }
  for (const stemName of ["vocals", "no_vocals"]) {
    const stem = renderProps?.sourceStems?.[stemName];
    await check(stem?.src);
    if (stem?.bucket !== undefined || stem?.path !== undefined) {
      if (typeof stem?.bucket !== "string" || typeof stem?.path !== "string") {
        throw new MediaUrlPolicyError("Internal media reference is invalid.");
      }
      await check(`supabase://${stem.bucket}/${stem.path}`);
    }
  }
  const matte = renderProps?.segmentationMatte;
  await check(matte?.src);
  if (matte?.bucket !== undefined || matte?.path !== undefined) {
    if (typeof matte?.bucket !== "string" || typeof matte?.path !== "string") {
      throw new MediaUrlPolicyError("Internal media reference is invalid.");
    }
    await check(`supabase://${matte.bucket}/${matte.path}`);
  }
}

export async function safeFetchMedia(
  url,
  {
    fetchImpl,
    lookup = dnsLookup,
    maxRedirects = MAX_MEDIA_REDIRECTS,
    signal,
    headers,
    trustedInternal = false,
    requestImpl,
  } = {},
) {
  if (!Number.isInteger(maxRedirects) || maxRedirects < 0 || maxRedirects > 10) {
    throw new MediaUrlPolicyError("Media redirect limit is invalid.");
  }
  let current = url;
  for (let redirectCount = 0; redirectCount <= maxRedirects; redirectCount += 1) {
    const validation = trustedInternal && redirectCount === 0
      ? await resolveTrustedInternalTarget(current, lookup)
      : await assertSafeOutboundMediaUrl(current, { lookup, requireDns: true });
    const response = fetchImpl
      ? await fetchImpl(current, { signal, headers, redirect: "manual" })
      : await pinnedFetch(current, {
        signal,
        headers,
        pinnedAddresses: validation.addresses,
        requestImpl,
      });
    if (!REDIRECT_STATUSES.has(response.status)) return response;
    const location = response.headers.get("location");
    if (!location) throw new MediaUrlPolicyError("Media redirect is missing a destination.");
    if (redirectCount >= maxRedirects) {
      await response.body?.cancel?.().catch?.(() => undefined);
      throw new MediaUrlPolicyError("Media redirect limit exceeded.");
    }
    await response.body?.cancel?.().catch?.(() => undefined);
    current = new URL(location, current).toString();
  }
  throw new MediaUrlPolicyError("Media redirect limit exceeded.");
}

async function resolveTrustedInternalTarget(value, lookup) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new MediaUrlPolicyError("Media URL is malformed.");
  }
  if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname) {
    throw new MediaUrlPolicyError("Media URL scheme is not supported.");
  }
  if (parsed.username || parsed.password) {
    throw new MediaUrlPolicyError("Credential-bearing media URLs are not allowed.");
  }
  const hostname = parsed.hostname.replace(/^\[|\]$/g, "").replace(/\.$/, "").toLowerCase();
  const family = isIP(hostname);
  if (family) return { addresses: [{ address: hostname, family }] };

  let records;
  try {
    records = await lookup(hostname, { all: true, verbatim: true });
  } catch {
    throw new MediaUrlPolicyError("Media URL host could not be resolved.");
  }
  if (!Array.isArray(records) || !records.length) {
    throw new MediaUrlPolicyError("Media URL host could not be resolved.");
  }
  return { addresses: records.map(normalizeLookupRecord) };
}

function normalizeLookupRecord(record) {
  const address = typeof record?.address === "string" ? record.address.split("%", 1)[0] : "";
  const actualFamily = isIP(address);
  const declaredFamily = Number(record?.family) || actualFamily;
  if (!actualFamily || declaredFamily !== actualFamily) {
    throw new MediaUrlPolicyError("Media URL resolved to an invalid address.");
  }
  return { address, family: actualFamily };
}

async function pinnedFetch(value, { signal, headers, pinnedAddresses, requestImpl }) {
  let lastError;
  for (const pinnedAddress of pinnedAddresses) {
    try {
      return await pinnedFetchAddress(value, { signal, headers, pinnedAddress, requestImpl });
    } catch (error) {
      if (signal?.aborted) throw error;
      lastError = error;
    }
  }
  throw lastError ?? new MediaUrlPolicyError("Media URL host could not be resolved.");
}

function pinnedFetchAddress(value, { signal, headers, pinnedAddress, requestImpl }) {
  const parsed = new URL(value);
  const hostname = parsed.hostname.replace(/^\[|\]$/g, "");
  const requester = requestImpl ?? (parsed.protocol === "https:" ? httpsRequest : httpRequest);
  const requestHeaders = Object.fromEntries(new Headers(headers));
  requestHeaders.host = parsed.host;

  return new Promise((resolve, reject) => {
    const request = requester(parsed, {
      headers: requestHeaders,
      signal,
      family: pinnedAddress.family,
      lookup(_hostname, options, callback) {
        const record = { address: pinnedAddress.address, family: pinnedAddress.family };
        queueMicrotask(() => {
          if (typeof options === "object" && options?.all) callback(null, [record]);
          else callback(null, record.address, record.family);
        });
      },
      ...(parsed.protocol === "https:" && !isIP(hostname) ? { servername: hostname } : {}),
    }, (incoming) => {
      try {
        const status = incoming.statusCode ?? 0;
        if (status < 200 || status > 599) {
          throw new MediaUrlPolicyError("Media response status is not supported.");
        }
        const responseHeaders = new Headers();
        for (let index = 0; index < incoming.rawHeaders.length; index += 2) {
          responseHeaders.append(incoming.rawHeaders[index], incoming.rawHeaders[index + 1]);
        }
        const hasBody = ![204, 205, 304].includes(status);
        resolve(new Response(hasBody ? Readable.toWeb(incoming) : null, {
          status,
          statusText: incoming.statusMessage,
          headers: responseHeaders,
        }));
      } catch (error) {
        incoming.destroy();
        reject(error);
      }
    });
    request.on("upgrade", (_incoming, socket) => {
      socket.destroy();
      reject(new MediaUrlPolicyError("Media response protocol is not supported."));
    });
    request.on("error", reject);
    request.end();
  });
}

function validateHostname(hostname) {
  if (!hostname || hostname.length > 253 || hostname.includes("%")) {
    throw new MediaUrlPolicyError("Media URL host is invalid.");
  }
  if (LOCAL_NAMES.has(hostname) || LOCAL_SUFFIXES.some((suffix) => hostname.endsWith(suffix))) {
    throw new MediaUrlPolicyError("Local media hosts are not allowed.");
  }
  if (!hostname.includes(".")) throw new MediaUrlPolicyError("Single-label media hosts are not allowed.");
  if (hostname.split(".").some((label) => (
    !label || label.length > 63 || label.startsWith("-") || label.endsWith("-") || !HOST_LABEL.test(label)
  ))) throw new MediaUrlPolicyError("Media URL host is invalid.");
}

function assertPublicAddress(address, family) {
  const blocked = Number(family) === 6
    ? forbiddenIpv6.check(address, "ipv6")
    : forbiddenIpv4.check(address, "ipv4");
  if (blocked) {
    throw new MediaUrlPolicyError("Media URL address is not publicly routable.");
  }
}

function validateSupabaseReference(value) {
  const withoutScheme = value.slice("supabase://".length);
  const slashIndex = withoutScheme.indexOf("/");
  if (slashIndex <= 0) throw new MediaUrlPolicyError("Internal media reference is invalid.");
  const bucket = withoutScheme.slice(0, slashIndex);
  const rawPath = withoutScheme.slice(slashIndex + 1);
  let path;
  try {
    path = decodeURIComponent(rawPath);
  } catch {
    throw new MediaUrlPolicyError("Internal media reference is invalid.");
  }
  if (
    value.includes("?") || value.includes("#") || !STORAGE_BUCKET.test(bucket) || !path ||
    path.includes("\\") || path.split("/").some((part) => !part || part === "." || part === "..")
  ) throw new MediaUrlPolicyError("Internal media reference is invalid.");
}

function validateFixtureReference(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new MediaUrlPolicyError("Fixture media reference is invalid.");
  }
  if (!parsed.hostname || !parsed.pathname || parsed.pathname === "/" || parsed.search || parsed.hash) {
    throw new MediaUrlPolicyError("Fixture media reference is invalid.");
  }
}

function validateDataAudio(value) {
  const prefix = "data:audio/wav;base64,";
  if (!value.startsWith(prefix)) throw new MediaUrlPolicyError("Only bounded WAV data audio is supported.");
  const encoded = value.slice(prefix.length);
  if (!encoded || encoded.length > Math.ceil(MAX_DATA_AUDIO_BYTES / 3) * 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) {
    throw new MediaUrlPolicyError("Data audio is malformed or too large.");
  }
  const decoded = Buffer.from(encoded, "base64");
  if (decoded.length > MAX_DATA_AUDIO_BYTES || decoded.subarray(0, 4).toString("ascii") !== "RIFF") {
    throw new MediaUrlPolicyError("Data audio is not a supported WAV payload.");
  }
}
