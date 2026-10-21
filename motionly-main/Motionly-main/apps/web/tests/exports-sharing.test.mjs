import assert from "node:assert/strict";
import test from "node:test";

import {
  SHARE_LINK_EXPIRY_OPTIONS,
  buildWatchUrl,
  isShareLinkInactive,
  readTokenFromHash,
  shareLinkStatusLabel,
  shareStrategy,
} from "../components/editor/export-sharing.mjs";

test("expiry options are the three allowed lifetimes", () => {
  assert.deepEqual(
    SHARE_LINK_EXPIRY_OPTIONS.map((option) => option.value),
    [1, 7, 30],
  );
});

test("watch URL keeps the token in the fragment", () => {
  const url = buildWatchUrl("https://app.motionly.com/", "abc 123");
  assert.equal(url, "https://app.motionly.com/watch#t=abc%20123");
  // The token is only ever after the '#', never in the path or query.
  const [beforeHash] = url.split("#");
  assert.ok(!beforeHash.includes("abc"));
});

test("token is read from a #t= fragment", () => {
  assert.equal(readTokenFromHash("#t=secret-token"), "secret-token");
  assert.equal(readTokenFromHash("#t=a%20b&x=1"), "a b");
});

test("token is read from a bare fragment", () => {
  assert.equal(readTokenFromHash("#just-a-token"), "just-a-token");
});

test("missing token resolves to null", () => {
  assert.equal(readTokenFromHash(""), null);
  assert.equal(readTokenFromHash("#"), null);
  assert.equal(readTokenFromHash("#x=1"), null);
});

test("share strategy prefers the native sheet when available", () => {
  assert.equal(shareStrategy({ share: () => {} }), "native");
  assert.equal(shareStrategy({}), "copy");
  assert.equal(shareStrategy(undefined), "copy");
});

test("inactive detection covers revoked and expired links", () => {
  const now = new Date("2026-01-10T00:00:00Z");
  assert.equal(
    isShareLinkInactive(
      { expires_at: "2026-01-20T00:00:00Z", revoked_at: null },
      now,
    ),
    false,
  );
  assert.equal(
    isShareLinkInactive(
      {
        expires_at: "2026-01-20T00:00:00Z",
        revoked_at: "2026-01-05T00:00:00Z",
      },
      now,
    ),
    true,
  );
  assert.equal(
    isShareLinkInactive(
      { expires_at: "2026-01-01T00:00:00Z", revoked_at: null },
      now,
    ),
    true,
  );
});

test("status label describes download capability and lifecycle", () => {
  const now = new Date("2026-01-10T00:00:00Z");
  assert.equal(
    shareLinkStatusLabel(
      { expires_at: "2026-01-20T00:00:00Z", allow_download: true },
      now,
    ),
    "Active · downloads on",
  );
  assert.equal(
    shareLinkStatusLabel(
      { expires_at: "2026-01-20T00:00:00Z", allow_download: false },
      now,
    ),
    "Active · watch only",
  );
  assert.equal(
    shareLinkStatusLabel(
      {
        expires_at: "2026-01-20T00:00:00Z",
        revoked_at: "2026-01-05T00:00:00Z",
      },
      now,
    ),
    "Revoked",
  );
  assert.equal(
    shareLinkStatusLabel(
      { expires_at: "2026-01-01T00:00:00Z", allow_download: true },
      now,
    ),
    "Expired",
  );
});
