// Liveness for the Compose health check: the worker refreshes this file while
// its queue consumers run, and the health check fails once it goes stale, so
// `docker compose up --wait` at release time notices a worker that hangs or
// never starts (infra/docker-compose.gpu.yml).
import { closeSync, openSync, statSync, utimesSync } from "node:fs";

export const WORKER_LIVENESS_FILE = "/tmp/motionly-worker-alive";

export function markAlive(file = WORKER_LIVENESS_FILE) {
  closeSync(openSync(file, "a"));
  const now = new Date();
  utimesSync(file, now, now);
}

export function isAlive(file = WORKER_LIVENESS_FILE, maxAgeMs = 120_000) {
  try {
    return Date.now() - statSync(file).mtimeMs < maxAgeMs;
  } catch {
    return false;
  }
}
