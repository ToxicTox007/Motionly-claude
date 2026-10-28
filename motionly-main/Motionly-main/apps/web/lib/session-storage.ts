// Motionly's browser-side state (upload sessions, editor history with request
// bodies, brand-kit and editor-mode choices, a pending invite token) is
// cleared when the viewer signs out, so the next person on a shared device
// inherits none of it, and every other open tab is told to leave.
//
// Every key the app writes starts with "motionly:" (or the older
// "motionly_invite_token"); nothing else on the origin is touched.

const MOTIONLY_KEY = /^motionly[:_]/;
const CHANNEL_NAME = "motionly:auth";
const SIGNAL_KEY = "motionly:auth:signed-out";

type StorageLike = Pick<Storage, "length" | "key" | "removeItem">;

function browserStorages(): StorageLike[] {
  const storages: StorageLike[] = [];
  for (const read of [() => window.localStorage, () => window.sessionStorage]) {
    try {
      storages.push(read());
    } catch {
      // Storage can be disabled (privacy mode); nothing to clear then.
    }
  }
  return storages;
}

/** Removes every Motionly key from the given storages (default: browser). */
export function clearMotionlyStorage(
  storages: StorageLike[] = browserStorages(),
): number {
  let removed = 0;
  for (const storage of storages) {
    const keys: string[] = [];
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (key && MOTIONLY_KEY.test(key)) keys.push(key);
    }
    for (const key of keys) {
      try {
        storage.removeItem(key);
        removed += 1;
      } catch {
        // Best effort.
      }
    }
  }
  return removed;
}

/**
 * Called just before the viewer signs out in this tab: clears local state and
 * tells the other tabs (BroadcastChannel, with a storage event as fallback).
 */
export function announceSignOut() {
  clearMotionlyStorage();
  try {
    const channel = new BroadcastChannel(CHANNEL_NAME);
    channel.postMessage({ type: "signed-out" });
    channel.close();
  } catch {
    // Older browsers: the storage event below still reaches other tabs.
  }
  try {
    window.localStorage.setItem(SIGNAL_KEY, String(Date.now()));
    window.localStorage.removeItem(SIGNAL_KEY);
  } catch {
    // Storage disabled: other tabs learn on their next request instead.
  }
}

/** Runs `onSignedOut` when another tab announces a sign-out. */
export function subscribeToSignOut(onSignedOut: () => void): () => void {
  let channel: BroadcastChannel | null = null;
  try {
    channel = new BroadcastChannel(CHANNEL_NAME);
    channel.onmessage = (event: MessageEvent) => {
      if (event.data?.type === "signed-out") onSignedOut();
    };
  } catch {
    channel = null;
  }
  const onStorage = (event: StorageEvent) => {
    if (event.key === SIGNAL_KEY && event.newValue) onSignedOut();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    channel?.close();
    window.removeEventListener("storage", onStorage);
  };
}
