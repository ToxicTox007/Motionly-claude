// CPU-stage CloudFront simulator: TEST INFRASTRUCTURE, not CloudFront.
//
// Puts the behaviour described in infra/aws/edge-contract.json in front of the
// CPU-stage stack, so browser tests and the stack checker exercise the same
// edge rules production uses: HTTPS with HTTP redirects, path behaviors,
// allowed methods, origin request policies (headers, cookies, query strings,
// the viewer Host, CloudFront-* headers), the origin secret header, caching
// with CloudFront's TTL rules and normalized Accept-Encoding, compression,
// the response headers policy, origin timeouts and retries, error responses
// and WebSocket tunnelling.
//
// Not simulated (documented divergences): HTTP/2 and HTTP/3 (viewers use
// HTTP/1.1), the TLSv1 minimum of the default certificate (Node's TLSv1.2
// minimum applies), range requests served from cache (ranged requests bypass
// the cache), and geographic headers beyond CloudFront-Viewer-Country, which
// is a configured test value.
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import zlib from "node:zlib";

const SUPPORTED_SCHEMA = 1;
// Viewer request headers CloudFront never forwards (hop-by-hop and the ones
// the "custom origin" behaviour table says it removes or replaces).
const DROPPED_REQUEST_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-connection",
  "te",
  "trailer",
  "upgrade",
  "expect",
  "x-forwarded-proto",
  "host",
  "content-length",
  "transfer-encoding",
]);
const HOP_BY_HOP_RESPONSE_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-connection",
  "transfer-encoding",
  "te",
  "trailer",
  "upgrade",
]);
const COMPRESSIBLE_TYPE =
  /^(text\/|application\/(javascript|x-javascript|json|ld\+json|manifest\+json|xml|xhtml\+xml|wasm|vnd\.ms-fontobject)|image\/(svg\+xml|x-icon|vnd\.microsoft\.icon)|font\/(ttf|otf))/i;
const COMPRESS_MIN_BYTES = 1000;
const COMPRESS_MAX_BYTES = 10_000_000;
const MAX_REQUEST_HEADER_BYTES = 32_768;
// CloudFront closes a WebSocket tunnel after this much inactivity (default: its
// documented 10-minute idle timeout). Any frame in either direction — including
// Realtime's periodic heartbeats — resets the timer, so a heartbeating socket
// never hits it. Overridable per edge (createEdge `websocketIdleMs`) so tests can
// drive a short idle window instead of waiting ten minutes.
const WEBSOCKET_IDLE_MS = 10 * 60 * 1000;
const INVALID_METHOD_BODY =
  "This distribution is not configured to allow the HTTP request method that was used for this request. The distribution supports only cachable requests.";

// --- Contract ---------------------------------------------------------------

export function loadContract(contractPath) {
  return validateContract(JSON.parse(fs.readFileSync(contractPath, "utf8")));
}

export function validateContract(contract) {
  if (contract?.schema_version !== SUPPORTED_SCHEMA) {
    throw new Error(`edge contract schema_version must be ${SUPPORTED_SCHEMA}`);
  }
  if (contract.origin?.protocol_policy !== "http-only") {
    throw new Error("cdn-sim supports only origin protocol_policy http-only");
  }
  const behaviors = contract.behaviors ?? [];
  if (behaviors.at(-1)?.path_pattern !== "*") {
    throw new Error("the last behavior must be the default (*)");
  }
  for (const behavior of behaviors) {
    const where = `behavior ${behavior.path_pattern}`;
    if (!contract.cache_policies?.[behavior.cache_policy]) {
      throw new Error(
        `${where}: unknown cache policy ${behavior.cache_policy}`,
      );
    }
    if (!contract.origin_request_policies?.[behavior.origin_request_policy]) {
      throw new Error(
        `${where}: unknown origin request policy ${behavior.origin_request_policy}`,
      );
    }
    if (
      !contract.response_headers_policies?.[behavior.response_headers_policy]
    ) {
      throw new Error(
        `${where}: unknown response headers policy ${behavior.response_headers_policy}`,
      );
    }
    if (behavior.viewer_protocol_policy !== "redirect-to-https") {
      throw new Error(
        `${where}: cdn-sim supports only viewer_protocol_policy redirect-to-https`,
      );
    }
  }
  for (const header of contract.origin.custom_headers ?? [])
    secretFileName(header.value_source);
  return contract;
}

/** CloudFront path patterns: case-sensitive; * = any characters, ? = one. */
export function matchesPathPattern(pattern, value) {
  if (pattern === "*") return true;
  let expression = "^";
  for (const character of pattern) {
    if (character === "*") expression += ".*";
    else if (character === "?") expression += ".";
    else expression += character.replace(/[\\^$.|+()[\]{}]/g, "\\$&");
  }
  return new RegExp(`${expression}$`, "s").test(value);
}

export function selectBehavior(contract, pathname) {
  return contract.behaviors.find((behavior) =>
    matchesPathPattern(behavior.path_pattern, pathname),
  );
}

/**
 * Custom origin header values come from Parameter Store in production, e.g.
 * ssm:/motionly/{environment}/edge/origin-verify; the simulator reads the same
 * value from the secret file edge_origin_verify.
 */
export function secretFileName(valueSource) {
  const match = /^ssm:\/motionly\/\{environment\}\/([a-z0-9/-]+)$/.exec(
    valueSource ?? "",
  );
  if (!match)
    throw new Error(`unsupported custom header value_source: ${valueSource}`);
  return match[1].replace(/[/-]/g, "_");
}

// --- Caching ----------------------------------------------------------------

function parseCacheControl(value) {
  const directives = {};
  for (const part of String(value ?? "").split(",")) {
    const [rawName, rawValue] = part.trim().split("=");
    if (!rawName) continue;
    directives[rawName.toLowerCase()] =
      rawValue === undefined ? true : rawValue.replace(/"/g, "");
  }
  return directives;
}

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

/** CloudFront's TTL rules ("Manage how long content stays in the cache"). */
export function cacheTtlSeconds(policy, headers, now = Date.now()) {
  const { min_ttl: min, default_ttl: fallback, max_ttl: max } = policy;
  if (max === 0) return 0;
  const directives = parseCacheControl(headers["cache-control"]);
  if (directives["no-cache"] || directives["no-store"] || directives.private)
    return min;
  const maxAge = directives["s-maxage"] ?? directives["max-age"];
  if (maxAge !== undefined && /^\d+$/.test(String(maxAge)))
    return clamp(Number(maxAge), min, max);
  if (headers.expires) {
    const expiresAt = Date.parse(headers.expires);
    if (!Number.isNaN(expiresAt)) {
      return clamp(Math.max(0, Math.floor((expiresAt - now) / 1000)), min, max);
    }
  }
  return clamp(fallback, min, max);
}

/** CloudFront normalizes Accept-Encoding to the codings it can serve. */
function normalizedAcceptEncoding(value) {
  const codings = new Set(
    String(value ?? "")
      .split(",")
      .map((part) => part.split(";")[0].trim().toLowerCase()),
  );
  return ["br", "gzip"].filter((coding) => codings.has(coding)).join(",");
}

class EdgeCache {
  constructor({
    maxBytes = 64 * 1024 * 1024,
    maxEntryBytes = 10 * 1024 * 1024,
  } = {}) {
    this.maxBytes = maxBytes;
    this.maxEntryBytes = maxEntryBytes;
    this.entries = new Map();
    this.bytes = 0;
  }

  get(key, now = Date.now()) {
    const entry = this.entries.get(key);
    if (!entry) return undefined;
    if (now >= entry.expiresAt) {
      this.delete(key);
      return undefined;
    }
    return entry;
  }

  set(key, entry) {
    if (entry.body.length > this.maxEntryBytes) return;
    this.delete(key);
    this.entries.set(key, entry);
    this.bytes += entry.body.length;
    for (const [oldest] of this.entries) {
      if (this.bytes <= this.maxBytes) break;
      this.delete(oldest);
    }
  }

  delete(key) {
    const entry = this.entries.get(key);
    if (!entry) return;
    this.bytes -= entry.body.length;
    this.entries.delete(key);
  }
}

// --- Headers ----------------------------------------------------------------

function viewerAddress(socket) {
  const address = String(socket.remoteAddress ?? "").replace(/^::ffff:/, "");
  return { ip: address, port: socket.remotePort };
}

function viewerTls(socket) {
  const protocol = socket.getProtocol?.() ?? "unknown";
  const cipher = socket.getCipher?.()?.standardName ?? "unknown";
  const handshake = socket.isSessionReused?.()
    ? "sessionResumed"
    : "fullHandshake";
  return `${protocol}:${cipher}:${handshake}`;
}

function cloudFrontHeaderValue(name, viewer, options) {
  switch (name.toLowerCase()) {
    case "cloudfront-forwarded-proto":
      return "https";
    case "cloudfront-viewer-address":
      return `${viewer.ip}:${viewer.port}`;
    case "cloudfront-viewer-http-version":
      return viewer.httpVersion;
    case "cloudfront-viewer-tls":
      return viewer.tls;
    case "cloudfront-viewer-country":
      return options.viewerCountry;
    default:
      return undefined;
  }
}

function forwardedPath(rawUrl, policy) {
  return policy.query_strings === "none" ? rawUrl.split("?")[0] : rawUrl;
}

function buildOriginHeaders({
  req,
  behavior,
  contract,
  viewer,
  cfId,
  options,
  websocket = false,
}) {
  const policy =
    contract.origin_request_policies[behavior.origin_request_policy];
  const cachePolicy = contract.cache_policies[behavior.cache_policy];
  const incoming = req.headers;
  const listed = Array.isArray(policy.headers)
    ? new Set(policy.headers.map((name) => name.toLowerCase()))
    : null;
  const connectionTokens = new Set(
    String(incoming.connection ?? "")
      .split(",")
      .map((token) => token.trim().toLowerCase()),
  );
  const headers = {};
  for (const [name, value] of Object.entries(incoming)) {
    if (DROPPED_REQUEST_HEADERS.has(name) || connectionTokens.has(name))
      continue;
    // Viewers cannot supply CloudFront's own headers.
    if (name.startsWith("cloudfront-")) continue;
    if (listed ? listed.has(name) : policy.headers === "all-viewer")
      headers[name] = value;
  }
  if (policy.cookies === "none") delete headers.cookie;
  else if (policy.cookies === "all" && incoming.cookie)
    headers.cookie = incoming.cookie;
  // CloudFront forwards Authorization through an origin request policy only
  // when the behavior does not cache.
  if (!(policy.headers === "all-viewer" && cachePolicy.max_ttl === 0))
    delete headers.authorization;
  if (cachePolicy.compressed_objects) {
    const encoding = normalizedAcceptEncoding(incoming["accept-encoding"]);
    if (encoding) headers["accept-encoding"] = encoding;
    else delete headers["accept-encoding"];
  }

  headers.host = policy.forwards_viewer_host
    ? incoming.host
    : options.originHost;
  headers["user-agent"] ??= "Amazon CloudFront";
  headers.via = [
    incoming.via,
    `${viewer.httpVersion} ${options.edgeId}.cloudfront.net (CloudFront)`,
  ]
    .filter(Boolean)
    .join(", ");
  headers["x-forwarded-for"] = [incoming["x-forwarded-for"], viewer.ip]
    .filter(Boolean)
    .join(", ");
  headers["x-amz-cf-id"] = cfId;
  for (const name of policy.cloudfront_headers ?? []) {
    const value = cloudFrontHeaderValue(name, viewer, options);
    if (value !== undefined) headers[name.toLowerCase()] = value;
  }
  // Origin custom headers overwrite a viewer header of the same name.
  for (const header of contract.origin.custom_headers ?? []) {
    headers[header.name.toLowerCase()] =
      options.customHeaderValues[header.name];
  }
  if (websocket) {
    headers.connection = "Upgrade";
    headers.upgrade = incoming.upgrade;
  } else {
    headers.connection = "keep-alive";
    if (incoming["content-length"] !== undefined)
      headers["content-length"] = incoming["content-length"];
    else if (incoming["transfer-encoding"])
      headers["transfer-encoding"] = "chunked";
  }
  return headers;
}

function applyResponseHeadersPolicy(headers, policy) {
  for (const name of policy.remove_headers ?? [])
    delete headers[name.toLowerCase()];
  const hsts = policy.strict_transport_security;
  if (hsts) {
    const value = [
      `max-age=${hsts.max_age_seconds}`,
      ...(hsts.include_subdomains ? ["includeSubDomains"] : []),
      ...(hsts.preload ? ["preload"] : []),
    ].join("; ");
    if (hsts.override || headers["strict-transport-security"] === undefined) {
      headers["strict-transport-security"] = value;
    }
  }
  for (const [key, header] of [
    ["content_type_options", "x-content-type-options"],
    ["frame_options", "x-frame-options"],
    ["referrer_policy", "referrer-policy"],
  ]) {
    const setting = policy[key];
    if (setting && (setting.override || headers[header] === undefined))
      headers[header] = setting.value;
  }
  if (policy.content_security_policy) {
    headers["content-security-policy"] = policy.content_security_policy;
  }
}

// --- The edge ---------------------------------------------------------------

export function createEdge({
  contract,
  originUrl,
  tls,
  customHeaderValues,
  publicHttpsPort,
  viewerCountry = "IN",
  pop = "SIM1-P1",
  log = (line) => process.stdout.write(`${line}\n`),
  cache = new EdgeCache(),
  websocketIdleMs = WEBSOCKET_IDLE_MS,
}) {
  validateContract(contract);
  for (const header of contract.origin.custom_headers ?? []) {
    if (!customHeaderValues?.[header.name]) {
      throw new Error(`missing value for origin custom header ${header.name}`);
    }
  }
  const origin = new URL(originUrl);
  if (origin.protocol !== "http:")
    throw new Error("cdn-sim origins are plain HTTP (http-only)");
  const originPort = Number(origin.port || 80);
  const timing = {
    attempts: contract.origin.connection_attempts,
    connectMs: contract.origin.connection_timeout_seconds * 1000,
    responseMs: contract.origin.response_timeout_seconds * 1000,
    keepaliveMs: contract.origin.keepalive_timeout_seconds * 1000,
  };
  const agent = new http.Agent({ keepAlive: true, maxSockets: 256 });
  // Idle pooled connections close after the keep-alive timeout. One listener
  // per socket: re-adding it on every reuse leaks listeners.
  const closeIdle = new WeakSet();
  agent.on("free", (socket) => {
    if (!closeIdle.has(socket)) {
      closeIdle.add(socket);
      socket.on("timeout", () => socket.destroy());
    }
    socket.setTimeout(timing.keepaliveMs);
  });
  const options = {
    customHeaderValues,
    viewerCountry,
    originHost: origin.host,
    edgeId: crypto.randomBytes(16).toString("hex"),
  };
  const ports = {};
  const servers = [];

  const newCfId = () =>
    crypto.randomBytes(42).toString("base64url").slice(0, 56);
  const viewerOf = (req) => ({
    ...viewerAddress(req.socket),
    httpVersion: req.httpVersion,
    tls: viewerTls(req.socket),
  });

  function edgeHeaders(headers, cfId, cacheStatus) {
    headers.via = [
      headers.via,
      `1.1 ${options.edgeId}.cloudfront.net (CloudFront)`,
    ]
      .filter(Boolean)
      .join(", ");
    headers["x-cache"] = cacheStatus;
    headers["x-amz-cf-pop"] = pop;
    headers["x-amz-cf-id"] = cfId;
    return headers;
  }

  function sendEdgeError(res, status, cfId, message) {
    const body = `${status} ERROR\n${message}\n`;
    res.writeHead(
      status,
      edgeHeaders(
        {
          "content-type": "text/plain; charset=utf-8",
          "content-length": Buffer.byteLength(body),
        },
        cfId,
        "Error from cloudfront",
      ),
    );
    res.end(body);
  }

  function redirectLocation(req) {
    const hostname = new URL(`http://${req.headers.host ?? "localhost"}`)
      .hostname;
    const port = publicHttpsPort ?? ports.https;
    const host = hostname.includes(":") ? `[${hostname}]` : hostname;
    return `https://${host}${port === 443 ? "" : `:${port}`}${req.url}`;
  }

  function onPlainRequest(req, res) {
    const status = ["GET", "HEAD"].includes(req.method) ? 301 : 307;
    res.writeHead(
      status,
      edgeHeaders(
        { location: redirectLocation(req), "content-length": 0 },
        newCfId(),
        "Redirect from cloudfront",
      ),
    );
    res.end();
    req.resume();
  }

  function attemptOrigin({ method, path, headers, body }) {
    return new Promise((resolve, reject) => {
      let settled = false;
      let connectTimer;
      let responseTimer;
      const request = http.request({
        host: origin.hostname,
        port: originPort,
        method,
        path,
        headers,
        agent,
        setHost: false,
      });
      const fail = (kind, error) => {
        if (settled) return;
        settled = true;
        clearTimeout(connectTimer);
        clearTimeout(responseTimer);
        request.destroy();
        reject(Object.assign(error ?? new Error(kind), { kind }));
      };
      let connected = false;
      request.on("socket", (socket) => {
        // In use: the idle timer applies only while pooled.
        socket.setTimeout(0);
        const send = () => {
          connected = true;
          clearTimeout(connectTimer);
          responseTimer = setTimeout(() => fail("timeout"), timing.responseMs);
          if (body) body.pipe(request);
          else request.end();
        };
        if (socket.connecting) {
          connectTimer = setTimeout(
            () => fail("connect", new Error("connect timeout")),
            timing.connectMs,
          );
          socket.once("connect", send);
        } else send();
      });
      request.on("response", (response) => {
        if (settled) return;
        settled = true;
        clearTimeout(responseTimer);
        resolve(response);
      });
      request.on("error", (error) => {
        // A pooled connection the origin already closed counts as a connection failure.
        const kind =
          !connected || (request.reusedSocket && error.code === "ECONNRESET")
            ? "connect"
            : "origin";
        fail(kind, error);
      });
    });
  }

  async function forwardToOrigin(request, { idempotent, hasBody }) {
    let lastError;
    for (let attempt = 1; attempt <= timing.attempts; attempt += 1) {
      try {
        return await attemptOrigin(request);
      } catch (error) {
        lastError = error;
        const retriable =
          (error.kind === "connect" &&
            !(hasBody && request.body.readableDidRead)) ||
          (error.kind === "timeout" && idempotent);
        if (!retriable) break;
      }
    }
    throw lastError;
  }

  function writeResponse({
    req,
    res,
    behavior,
    cfId,
    status,
    headers,
    stream,
    cacheKey,
    cacheStatus,
  }) {
    const cachePolicy = contract.cache_policies[behavior.cache_policy];
    const responseHeaders = {};
    for (const [name, value] of Object.entries(headers)) {
      if (!HOP_BY_HOP_RESPONSE_HEADERS.has(name)) responseHeaders[name] = value;
    }
    applyResponseHeadersPolicy(
      responseHeaders,
      contract.response_headers_policies[behavior.response_headers_policy],
    );

    const encoding = normalizedAcceptEncoding(
      req.headers["accept-encoding"],
    ).split(",")[0];
    const length = Number(responseHeaders["content-length"]);
    const compress =
      behavior.compress &&
      req.method !== "HEAD" &&
      encoding &&
      !responseHeaders["content-encoding"] &&
      Number.isFinite(length) &&
      length >= COMPRESS_MIN_BYTES &&
      length <= COMPRESS_MAX_BYTES &&
      COMPRESSIBLE_TYPE.test(String(responseHeaders["content-type"] ?? ""));
    let body = stream;
    if (compress) {
      body = stream.pipe(
        encoding === "br" ? zlib.createBrotliCompress() : zlib.createGzip(),
      );
      responseHeaders["content-encoding"] = encoding;
      delete responseHeaders["content-length"];
    }

    const ttl =
      cacheKey && req.method === "GET" && status === 200
        ? cacheTtlSeconds(cachePolicy, headers)
        : 0;
    const chunks = [];
    let size = 0;
    let cacheable = ttl > 0;
    res.writeHead(
      status,
      edgeHeaders({ ...responseHeaders }, cfId, cacheStatus),
    );
    if (req.method === "HEAD") {
      stream.resume();
      res.end();
      return;
    }
    body.on("data", (chunk) => {
      if (cacheable) {
        size += chunk.length;
        if (size > cache.maxEntryBytes) cacheable = false;
        else chunks.push(chunk);
      }
    });
    body.on("end", () => {
      if (cacheable) {
        cache.set(cacheKey, {
          status,
          headers: responseHeaders,
          body: Buffer.concat(chunks),
          storedAt: Date.now(),
          expiresAt: Date.now() + ttl * 1000,
        });
      }
    });
    body.on("error", () => res.destroy());
    body.pipe(res);
  }

  async function onRequest(req, res) {
    const started = Date.now();
    const cfId = newCfId();
    const pathname = req.url.split("?")[0];
    const behavior = selectBehavior(contract, pathname);
    const cachePolicy = contract.cache_policies[behavior.cache_policy];
    const originPolicy =
      contract.origin_request_policies[behavior.origin_request_policy];
    let outcome = "miss";
    let status;
    res.on("finish", () =>
      log(
        JSON.stringify({
          component: "cdn-sim",
          method: req.method,
          path: pathname,
          behavior: behavior.path_pattern,
          status,
          cache: outcome,
          duration_ms: Date.now() - started,
          cf_id: cfId,
        }),
      ),
    );

    if (!behavior.allowed_methods.includes(req.method)) {
      status = 403;
      outcome = "error";
      req.resume();
      return sendEdgeError(res, 403, cfId, INVALID_METHOD_BODY);
    }
    const hasBody =
      Number(req.headers["content-length"] ?? 0) > 0 ||
      req.headers["transfer-encoding"] !== undefined;
    if (req.method === "GET" && hasBody) {
      status = 403;
      outcome = "error";
      req.resume();
      return sendEdgeError(
        res,
        403,
        cfId,
        "Bad request: a GET request cannot have a body.",
      );
    }

    const cacheable =
      cachePolicy.max_ttl > 0 &&
      ["GET", "HEAD"].includes(req.method) &&
      !req.headers.range;
    const encoding = cachePolicy.compressed_objects
      ? normalizedAcceptEncoding(req.headers["accept-encoding"]).split(",")[0]
      : "";
    const cacheKey = cacheable
      ? `${forwardedPath(req.url, originPolicy)}\n${encoding}`
      : undefined;
    const hit = cacheKey && cache.get(cacheKey);
    if (hit) {
      outcome = "hit";
      status = hit.status;
      const headers = edgeHeaders(
        { ...hit.headers },
        cfId,
        "Hit from cloudfront",
      );
      headers.age = String(Math.floor((Date.now() - hit.storedAt) / 1000));
      headers["content-length"] = hit.body.length;
      res.writeHead(hit.status, headers);
      res.end(req.method === "HEAD" ? undefined : hit.body);
      return;
    }

    const viewer = viewerOf(req);
    try {
      const response = await forwardToOrigin(
        {
          method: req.method,
          path: forwardedPath(req.url, originPolicy),
          headers: buildOriginHeaders({
            req,
            behavior,
            contract,
            viewer,
            cfId,
            options,
          }),
          body: hasBody ? req : null,
        },
        { idempotent: ["GET", "HEAD"].includes(req.method), hasBody },
      );
      status = response.statusCode;
      response.setTimeout(timing.responseMs, () => response.destroy());
      writeResponse({
        req,
        res,
        behavior,
        cfId,
        status,
        headers: response.headers,
        stream: response,
        cacheKey,
        cacheStatus: "Miss from cloudfront",
      });
    } catch (error) {
      outcome = "error";
      status = error.kind === "timeout" ? 504 : 502;
      if (res.headersSent) return res.destroy();
      sendEdgeError(
        res,
        status,
        cfId,
        status === 504
          ? "The origin did not respond within the response timeout."
          : "The request could not be satisfied: CloudFront could not connect to the origin.",
      );
    }
  }

  function onUpgrade(req, socket, head) {
    const cfId = newCfId();
    const pathname = req.url.split("?")[0];
    const behavior = selectBehavior(contract, pathname);
    const originPolicy =
      contract.origin_request_policies[behavior.origin_request_policy];
    const reject = (status, text) => {
      socket.end(
        `HTTP/1.1 ${status} ${text}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n`,
      );
    };
    // CloudFront forwards WebSockets only when every viewer header reaches the origin.
    if (req.method !== "GET" || originPolicy.headers !== "all-viewer")
      return reject(403, "Forbidden");
    const headers = buildOriginHeaders({
      req,
      behavior,
      contract,
      viewer: viewerOf(req),
      cfId,
      options,
      websocket: true,
    });
    const upstream = net.connect({ host: origin.hostname, port: originPort });
    const connectTimer = setTimeout(
      () => upstream.destroy(new Error("connect timeout")),
      timing.connectMs,
    );
    upstream.once("connect", () => {
      clearTimeout(connectTimer);
      const lines = [`GET ${forwardedPath(req.url, originPolicy)} HTTP/1.1`];
      for (const [name, value] of Object.entries(headers)) {
        for (const item of Array.isArray(value) ? value : [value])
          lines.push(`${name}: ${item}`);
      }
      upstream.write(`${lines.join("\r\n")}\r\n\r\n`);
      if (head?.length) upstream.write(head);
      for (const side of [socket, upstream])
        side.setTimeout(websocketIdleMs, () => side.destroy());
      socket.pipe(upstream);
      upstream.pipe(socket);
    });
    upstream.on("error", () =>
      socket.writable ? reject(502, "Bad Gateway") : socket.destroy(),
    );
    socket.on("error", () => upstream.destroy());
    socket.on("close", () => upstream.destroy());
  }

  return {
    ports,
    async listen({ httpsPort, httpPort, host = "0.0.0.0" }) {
      const secure = https.createServer(
        { ...tls, maxHeaderSize: MAX_REQUEST_HEADER_BYTES },
        onRequest,
      );
      secure.on("upgrade", onUpgrade);
      const plain = http.createServer(
        { maxHeaderSize: MAX_REQUEST_HEADER_BYTES },
        onPlainRequest,
      );
      plain.on("upgrade", (req, socket) => {
        socket.end(
          `HTTP/1.1 301 Moved Permanently\r\nLocation: ${redirectLocation(req)}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n`,
        );
      });
      for (const [name, server, port] of [
        ["https", secure, httpsPort],
        ["http", plain, httpPort],
      ]) {
        await new Promise((resolve, reject) => {
          server.once("error", reject);
          server.listen(port, host, resolve);
        });
        ports[name] = server.address().port;
        servers.push(server);
      }
    },
    async close() {
      agent.destroy();
      await Promise.all(
        servers.map(
          (server) =>
            new Promise((resolve) => {
              server.close(resolve);
              server.closeAllConnections?.();
            }),
        ),
      );
    },
  };
}
