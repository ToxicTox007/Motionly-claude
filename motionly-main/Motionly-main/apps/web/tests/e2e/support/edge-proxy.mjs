// A local stand-in for the edge (Caddy behind CloudFront) for the browser
// performance harness. The harness measures a production server
// (`next start`), which has no dev rewrites, so this serves the one public
// origin the browser uses: /api goes to FastAPI and /supabase to Supabase,
// both without the prefix (as deployed), and everything else to Next.js.
// Test tooling only; never part of the app.
//
//   EDGE_PORT=3000 EDGE_WEB_ORIGIN=http://127.0.0.1:3001 \
//   EDGE_API_ORIGIN=http://127.0.0.1:8000 \
//   EDGE_SUPABASE_ORIGIN=http://127.0.0.1:54321 \
//   node tests/e2e/support/edge-proxy.mjs
import http from "node:http";
import https from "node:https";
import net from "node:net";
import tls from "node:tls";
import { pathToFileURL } from "node:url";

const PREFIXES = [
  ["/api", "api"],
  ["/supabase", "supabase"],
];

/** Where a request path goes: its upstream origin and the upstream path. */
export function upstreamFor(path, targets) {
  for (const [prefix, name] of PREFIXES) {
    const rest = path.slice(prefix.length);
    if (path.startsWith(prefix) && (rest === "" || /^[/?]/.test(rest))) {
      return {
        origin: targets[name],
        path: rest.startsWith("/") ? rest : `/${rest}`,
        rewritten: true,
      };
    }
  }
  return { origin: targets.web, path, rewritten: false };
}

function forwardHeaders(req, target, url) {
  const viewerHost = req.headers.host ?? "";
  return {
    ...req.headers,
    // Next.js checks a server action's Origin against Host, so it sees the
    // viewer's host; the other services see their own.
    host: target.rewritten ? url.host : viewerHost,
    "x-forwarded-host": viewerHost,
    "x-forwarded-proto": "http",
    "x-forwarded-for": req.socket.remoteAddress ?? "",
  };
}

function badGateway(res, error) {
  if (!res.headersSent) {
    res.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
  }
  res.end(`edge proxy: upstream unavailable (${error.code ?? error.message})`);
}

/** Starts the proxy; resolves with the listening http.Server. */
export function startEdgeProxy({ port, host = "127.0.0.1", targets }) {
  const server = http.createServer((req, res) => {
    const target = upstreamFor(req.url ?? "/", targets);
    const url = new URL(target.path, target.origin);
    const transport = url.protocol === "https:" ? https : http;
    const upstream = transport.request(
      url,
      { method: req.method, headers: forwardHeaders(req, target, url) },
      (answer) => {
        res.writeHead(answer.statusCode ?? 502, answer.headers);
        answer.pipe(res);
      },
    );
    upstream.on("error", (error) => badGateway(res, error));
    req.pipe(upstream);
  });

  // WebSockets (Supabase Realtime): relay the upgrade request, then bytes.
  server.on("upgrade", (req, socket, head) => {
    const target = upstreamFor(req.url ?? "/", targets);
    const url = new URL(target.path, target.origin);
    const secure = url.protocol === "https:";
    const port = Number(url.port || (secure ? 443 : 80));
    const onConnect = () => {
      const headers = Object.entries(forwardHeaders(req, target, url))
        .map(([name, value]) => `${name}: ${value}`)
        .join("\r\n");
      upstream.write(
        `${req.method} ${url.pathname}${url.search} HTTP/1.1\r\n${headers}\r\n\r\n`,
      );
      if (head.length > 0) upstream.write(head);
      upstream.pipe(socket);
      socket.pipe(upstream);
    };
    const upstream = secure
      ? tls.connect(
          { host: url.hostname, port, servername: url.hostname },
          onConnect,
        )
      : net.connect(port, url.hostname, onConnect);
    upstream.on("error", () => socket.destroy());
    socket.on("error", () => upstream.destroy());
  });

  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => resolve(server));
  });
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const env = process.env;
  const port = Number(env.EDGE_PORT || 3000);
  const targets = {
    web: env.EDGE_WEB_ORIGIN || "http://127.0.0.1:3001",
    api: env.EDGE_API_ORIGIN || "http://127.0.0.1:8000",
    supabase: env.EDGE_SUPABASE_ORIGIN || "http://127.0.0.1:54321",
  };
  await startEdgeProxy({ port, targets });
  console.log(
    `edge proxy on http://127.0.0.1:${port}: /api -> ${targets.api}, /supabase -> ${targets.supabase}, rest -> ${targets.web}`,
  );
}
