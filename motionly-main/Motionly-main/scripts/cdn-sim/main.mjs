#!/usr/bin/env node
// Runs the CPU-stage CloudFront simulator (TEST INFRASTRUCTURE, not
// CloudFront). Configuration, all required unless a default is shown:
//   CDN_SIM_CONTRACT           infra/aws/edge-contract.json
//   CDN_SIM_ORIGIN             origin URL, e.g. http://ingress:80
//   CDN_SIM_TLS_CERT           PEM certificate for the viewer HTTPS listener
//   CDN_SIM_TLS_KEY            PEM private key for that certificate
//   CDN_SIM_SECRETS_DIR        directory holding the origin custom header values
//                              (file names: see secretFileName in edge.mjs)
//   CDN_SIM_HTTPS_PORT         8443
//   CDN_SIM_HTTP_PORT          8080 (redirects to HTTPS)
//   CDN_SIM_PUBLIC_HTTPS_PORT  HTTPS port viewers use, for redirects (default: CDN_SIM_HTTPS_PORT)
//   CDN_SIM_VIEWER_COUNTRY     IN (simulated CloudFront-Viewer-Country)
import fs from "node:fs";
import path from "node:path";

import { createEdge, loadContract, secretFileName } from "./edge.mjs";

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function port(name, fallback) {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(value) || value < 1 || value > 65535)
    throw new Error(`${name} must be a port`);
  return value;
}

function readSecret(directory, valueSource) {
  const file = path.join(directory, secretFileName(valueSource));
  const value = fs.readFileSync(file, "utf8").trim();
  if (!value) throw new Error(`${file} is empty`);
  return value;
}

async function main() {
  const contract = loadContract(required("CDN_SIM_CONTRACT"));
  const secretsDir = required("CDN_SIM_SECRETS_DIR");
  const customHeaderValues = Object.fromEntries(
    (contract.origin.custom_headers ?? []).map((header) => [
      header.name,
      readSecret(secretsDir, header.value_source),
    ]),
  );
  const httpsPort = port("CDN_SIM_HTTPS_PORT", 8443);
  const edge = createEdge({
    contract,
    originUrl: required("CDN_SIM_ORIGIN"),
    tls: {
      cert: fs.readFileSync(required("CDN_SIM_TLS_CERT")),
      key: fs.readFileSync(required("CDN_SIM_TLS_KEY")),
    },
    customHeaderValues,
    publicHttpsPort: port("CDN_SIM_PUBLIC_HTTPS_PORT", httpsPort),
    viewerCountry: process.env.CDN_SIM_VIEWER_COUNTRY || "IN",
  });
  await edge.listen({ httpsPort, httpPort: port("CDN_SIM_HTTP_PORT", 8080) });
  process.stdout.write(
    `${JSON.stringify({ component: "cdn-sim", message: "listening (test infrastructure, not CloudFront)", ports: edge.ports })}\n`,
  );
  const stop = () => edge.close().then(() => process.exit(0));
  process.once("SIGTERM", stop);
  process.once("SIGINT", stop);
}

main().catch((error) => {
  process.stderr.write(`cdn-sim: ${error.message}\n`);
  process.exit(1);
});
