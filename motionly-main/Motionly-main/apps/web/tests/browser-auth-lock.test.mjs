// The browser Supabase client's auth lock (lib/supabase/browser-lock.mjs).
// Firefox reports an error thrown inside a navigator.locks callback as an
// uncaught page error even when the caller handles it, so "not immediately
// available" must be reported outside the callback.
import assert from "node:assert/strict";
import test from "node:test";

import { NavigatorLockAcquireTimeoutError } from "@supabase/supabase-js";

import { browserAuthLock } from "../lib/supabase/browser-lock.mjs";

function fakeLocks({ available }) {
  const calls = [];
  return {
    calls,
    async request(name, options, callback) {
      calls.push({ name, options });
      let callbackError = null;
      const result = await callback(available ? { name } : null).catch(
        (error) => {
          callbackError = error;
          throw error;
        },
      );
      if (callbackError) throw callbackError;
      return result;
    },
  };
}

function withNavigatorLocks(locks, run) {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", {
    value: { locks },
    configurable: true,
  });
  return Promise.resolve(run()).finally(() => {
    if (descriptor) Object.defineProperty(globalThis, "navigator", descriptor);
    else delete globalThis.navigator;
  });
}

test("an immediate lock that is busy rejects without throwing inside the lock callback", async () => {
  const locks = fakeLocks({ available: false });
  let ran = false;
  const thrownInside = [];
  const original = locks.request;
  locks.request = async (name, options, callback) =>
    original(name, options, async (lock) => {
      try {
        return await callback(lock);
      } catch (error) {
        thrownInside.push(error);
        throw error;
      }
    });

  await withNavigatorLocks(locks, async () => {
    await assert.rejects(
      browserAuthLock("lock:sb-motionly-auth-token", 0, async () => {
        ran = true;
      }),
      (error) =>
        error instanceof NavigatorLockAcquireTimeoutError &&
        error.isAcquireTimeout === true,
    );
  });

  assert.equal(ran, false);
  assert.deepEqual(thrownInside, []);
  assert.deepEqual(locks.calls[0].options, {
    mode: "exclusive",
    ifAvailable: true,
  });
});

test("an immediate lock that is free runs the operation and returns its result", async () => {
  const locks = fakeLocks({ available: true });

  await withNavigatorLocks(locks, async () => {
    assert.equal(
      await browserAuthLock("lock:x", 0, async () => "refreshed"),
      "refreshed",
    );
  });
});

test("waiting acquisitions use Supabase's navigator lock unchanged", async () => {
  const locks = fakeLocks({ available: true });

  await withNavigatorLocks(locks, async () => {
    assert.equal(
      await browserAuthLock("lock:x", -1, async () => "session"),
      "session",
    );
  });
  assert.equal(locks.calls[0].options.ifAvailable, undefined);
});
