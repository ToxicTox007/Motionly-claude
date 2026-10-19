// Cross-tab auth lock for the browser Supabase client. It behaves exactly like
// Supabase's navigatorLock, except that when an immediate acquisition
// (acquireTimeout 0, used by the background token-refresh tick) finds the lock
// busy, it rejects after the lock request settles instead of throwing inside
// the navigator.locks callback. Firefox reports an error thrown inside that
// callback as an uncaught page error even though the caller handles it.
import {
  NavigatorLockAcquireTimeoutError,
  navigatorLock,
} from "@supabase/supabase-js";

const BUSY = Symbol("lock busy");

export async function browserAuthLock(name, acquireTimeout, fn) {
  if (acquireTimeout !== 0) return navigatorLock(name, acquireTimeout, fn);
  const result = await globalThis.navigator.locks.request(
    name,
    { mode: "exclusive", ifAvailable: true },
    async (lock) => (lock ? { value: await fn() } : BUSY),
  );
  if (result === BUSY) {
    throw new NavigatorLockAcquireTimeoutError(
      `Acquiring an exclusive Navigator LockManager lock "${name}" immediately failed`,
    );
  }
  return result.value;
}
