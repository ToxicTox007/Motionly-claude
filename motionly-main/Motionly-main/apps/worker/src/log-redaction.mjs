/**
 * Credential redaction for everything the worker writes to its logs.
 *
 * The worker logs its own events and passes through the stderr/stdout of the
 * Python executors and the Remotion renderer (tracebacks, provider error
 * bodies, URLs). Any of those can carry a credential. installLogRedaction()
 * wraps process.stdout/stderr once at start-up so every write — console.*,
 * child passthrough — is redacted with the same rules as the API's
 * app/core/logging.py redact_string.
 *
 * Writes are redacted per chunk. Child passthrough is line-buffered by
 * createRedactingLineWriter so a credential split across two pipe reads is
 * still caught.
 */

const RULES = [
  [
    /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)/g,
    "[REDACTED_PRIVATE_KEY]",
  ],
  // A JWT: Supabase access tokens, the anon/service-role/worker keys.
  [
    /\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*/g,
    "[REDACTED_JWT]",
  ],
  [/\bmtly_[A-Za-z0-9_-]{16,}/g, "[REDACTED_API_KEY]"],
  [/\bsb_secret_[A-Za-z0-9_-]{8,}/g, "[REDACTED_SUPABASE_KEY]"],
  // Passwords in connection URLs: redis://:pw@host, postgres://user:pw@host.
  [/\b([a-z][a-z0-9+.-]*:\/\/[^\s:/@]*):[^\s@/]+@/gi, "$1:[REDACTED]@"],
  [/\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g, "[REDACTED_AWS_KEY]"],
  [/(bearer\s+)[a-zA-Z0-9_\-.~+/]+=*/gi, "$1[REDACTED]"],
  [
    /\b(token|signature|sig|pass|password|key|api_key|access_token|refresh_token|apikey|secret)=[^&"'\s#]+/gi,
    "$1=[REDACTED]",
  ],
  [
    /(["'](?:api_key|token|secret|access_token|refresh_token|signature|authorization|password|apikey|service_role_key)["']\s*:\s*)(["'])[^"']*?\2/gi,
    "$1$2[REDACTED]$2",
  ],
];

// Process-wide registry of exact secret values. Mirrors the API's
// app/core/logging.py register_secret_value: a resolved inference secret
// registers its exact value here and every later log write that contains it is
// masked with SECRET_VALUE_MARKER, whatever the value's shape. Values shorter
// than MIN_REGISTERED_SECRET_LEN are not substring-matched, to avoid
// over-redacting short common strings (they are still never logged on purpose).
const SECRET_VALUE_MARKER = "[REDACTED_SECRET]";
const MIN_REGISTERED_SECRET_LEN = 8;
const registeredSecretValues = new Set();

/** Register an exact secret value so redactLogText masks it everywhere. */
export function registerSecretValue(value) {
  if (typeof value !== "string" || value.length < MIN_REGISTERED_SECRET_LEN) {
    return;
  }
  registeredSecretValues.add(value);
}

/** Test/diagnostic helper: a snapshot of the registered exact values. */
export function registeredSecretValueCount() {
  return registeredSecretValues.size;
}

function redactRegisteredValues(text) {
  if (registeredSecretValues.size === 0) return text;
  // Longest first so a superstring secret is masked whole.
  const values = [...registeredSecretValues].sort(
    (a, b) => b.length - a.length,
  );
  let redacted = text;
  for (const value of values) {
    if (redacted.includes(value)) {
      redacted = redacted.split(value).join(SECRET_VALUE_MARKER);
    }
  }
  return redacted;
}

export function redactLogText(text) {
  if (typeof text !== "string" || !text) return text;
  // Registered exact values first, so a value is masked whole before any shape
  // rule can half-redact it.
  let redacted = redactRegisteredValues(text);
  for (const [pattern, replacement] of RULES) {
    redacted = redacted.replace(pattern, replacement);
  }
  return redacted;
}

let installed = false;

function wrapStream(stream) {
  const original = stream.write.bind(stream);
  stream.write = (chunk, encoding, callback) => {
    if (typeof chunk === "string") {
      return original(redactLogText(chunk), encoding, callback);
    }
    if (chunk instanceof Uint8Array) {
      const text = Buffer.from(chunk).toString(
        typeof encoding === "string" ? encoding : "utf8",
      );
      return original(
        redactLogText(text),
        typeof encoding === "function" ? encoding : undefined,
        typeof encoding === "function" ? undefined : callback,
      );
    }
    return original(chunk, encoding, callback);
  };
  return original;
}

/** Redact every later write to process.stdout and process.stderr (idempotent). */
export function installLogRedaction(target = process) {
  if (installed && target === process) return;
  wrapStream(target.stdout);
  wrapStream(target.stderr);
  if (target === process) installed = true;
}

/**
 * A line-buffered writer for a child's output: whole lines are redacted and
 * written; a trailing partial line waits for its newline (or flush()).
 */
export function createRedactingLineWriter(write, { prefix = "" } = {}) {
  let buffer = "";
  return {
    write(chunk) {
      buffer += String(chunk);
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline + 1);
        buffer = buffer.slice(newline + 1);
        write(redactLogText(prefix + line));
      }
      // Guard memory against a child that never writes a newline.
      if (buffer.length > 64 * 1024) {
        write(redactLogText(prefix + buffer));
        buffer = "";
      }
    },
    flush() {
      if (buffer) write(redactLogText(prefix + buffer));
      buffer = "";
    },
  };
}
