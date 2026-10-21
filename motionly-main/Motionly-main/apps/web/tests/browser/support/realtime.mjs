// Task 25 — Supabase Realtime private-channel helpers for the browser suite.
//
// Private channels are authorized by RLS on realtime.messages: a client may
// JOIN a topic only when realtime.motionly_can_access_topic(topic) passes, and
// may send presence only when the INSERT policy passes as well. The topics are
// tenant-scoped: `workspace:<workspace-uuid>` / `project:<project-uuid>`.
//
// The browser suite reaches Realtime exactly as a viewer's browser does: the
// supabase-js client talks to `<origin>/supabase`, so its realtime socket
// connects to `<origin>/supabase/realtime/v1/websocket`, which the CloudFront
// simulator forwards to Kong's realtime-dev tenant. We drive supabase-js in
// Node with each user's real access token (obtained through the same Auth
// endpoint the browser uses) rather than inside a page, because presence is a
// pure WebSocket exchange with no UI and Node keeps the assertions
// deterministic.
import { createClient } from "@supabase/supabase-js";

import { stackCredentials } from "./stack.mjs";

/**
 * Exchange a user's email + password for a real access token through the
 * stack's Auth endpoint (the same password grant the browser login uses).
 */
export async function accessTokenFor(stack, { email, password }) {
  const { anonKey } = stackCredentials();
  const response = await stack.admin.post(
    "/supabase/auth/v1/token?grant_type=password",
    {
      headers: { apikey: anonKey, "content-type": "application/json" },
      data: { email, password },
    },
  );
  if (!response.ok()) {
    throw new Error(
      `password grant failed for ${email}: ${response.status()} ${await response.text()}`,
    );
  }
  const session = await response.json();
  if (!session.access_token) {
    throw new Error(`no access_token in grant response for ${email}`);
  }
  return session.access_token;
}

/**
 * A supabase-js client pointed at the stack through the CDN proxy, carrying the
 * given access token (or none, for the signed-out case). Realtime connects over
 * the public origin, exactly as a browser would.
 */
export function realtimeClientFor(baseURL, accessToken) {
  const { anonKey } = stackCredentials();
  const client = createClient(`${baseURL}/supabase`, anonKey, {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: false,
    },
    realtime: { params: { apikey: anonKey } },
  });
  // Supabase Realtime needs the user's JWT on a private channel so RLS can load
  // auth.uid(); a signed-out client has none, so the join is unauthorized.
  if (accessToken) {
    client.realtime.setAuth(accessToken);
  }
  return client;
}

/**
 * Join a private topic with presence and resolve to one of:
 *   { status: "joined", presence: {...} }  — subscribe reached SUBSCRIBED
 *   { status: "denied" }                   — the join was rejected (RLS)
 *   { status: "timeout" }                  — neither happened in time
 *
 * `onReady` (optional) runs once this client is SUBSCRIBED and has tracked its
 * own presence, so a test can coordinate two joiners.
 */
export function joinPrivateTopic(
  client,
  topic,
  { key, meta = {}, timeoutMs = 20_000 } = {},
) {
  return new Promise((resolve) => {
    const channel = client.channel(topic, {
      config: {
        private: true,
        presence: { key: key ?? meta.user ?? "anonymous" },
      },
    });

    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ channel, ...value });
    };

    const timer = setTimeout(() => finish({ status: "timeout" }), timeoutMs);

    channel.subscribe(async (status, error) => {
      if (status === "SUBSCRIBED") {
        await channel.track({ user: key, ...meta });
        finish({
          status: "joined",
          presenceState: () => channel.presenceState(),
        });
      } else if (
        status === "CHANNEL_ERROR" ||
        status === "TIMED_OUT" ||
        status === "CLOSED"
      ) {
        // A private channel the user may not access is rejected by the server
        // (RLS), surfaced to supabase-js as a channel error.
        finish({ status: "denied", error: error ? String(error) : status });
      }
    });
  });
}

/** Wait until `channel`'s presence state contains `expectedKey`. */
export async function waitForPresence(
  channel,
  expectedKey,
  timeoutMs = 20_000,
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = channel.presenceState();
    const keys = Object.keys(state);
    if (keys.includes(expectedKey)) return state;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return channel.presenceState();
}

/** Tear down a realtime client and all its channels. */
export async function closeRealtime(client) {
  if (!client) return;
  try {
    await client.removeAllChannels();
  } catch {
    // Already closing.
  }
  try {
    await client.realtime.disconnect();
  } catch {
    // Already disconnected.
  }
}
