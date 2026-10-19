// The server Supabase client writes refreshed session cookies where Next
// allows it (server actions, route handlers) and skips the write in server
// components, where cookies are read-only; proxy.ts refreshes the session
// for every page request instead.
import assert from "node:assert/strict";
import test from "node:test";

import { writeSessionCookies } from "../lib/supabase/session-cookies.mjs";

const READ_ONLY =
  "Cookies can only be modified in a Server Action or Route Handler. Read more: https://nextjs.org/docs/app/api-reference/functions/cookies#options";

test("cookies are written when the store allows it", () => {
  const written = [];
  writeSessionCookies({ set: (...args) => written.push(args) }, [
    { name: "a", value: "1", options: { path: "/" } },
    { name: "b", value: "2", options: {} },
  ]);
  assert.deepEqual(written, [
    ["a", "1", { path: "/" }],
    ["b", "2", {}],
  ]);
});

test("a read-only store (server component) is skipped without an error", () => {
  const store = {
    set: () => {
      throw new Error(READ_ONLY);
    },
  };
  assert.doesNotThrow(() =>
    writeSessionCookies(store, [{ name: "a", value: "1", options: {} }]),
  );
});

test("any other failure still surfaces", () => {
  const store = {
    set: () => {
      throw new Error("disk full");
    },
  };
  assert.throws(
    () => writeSessionCookies(store, [{ name: "a", value: "1", options: {} }]),
    /disk full/,
  );
});
