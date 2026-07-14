import assert from "node:assert/strict";
import test from "node:test";

import {
  MediaUrlPolicyError,
  assertSafeOutboundMediaUrl,
  safeFetchMedia,
  validateRenderMediaManifest,
} from "./media-url-policy.mjs";

const publicLookup = async () => [{ address: "93.184.216.34", family: 4 }];

test("media URL policy rejects non-public IPv4 and IPv6 literals", async () => {
  for (const url of [
    "http://127.0.0.1/media.mp4",
    "http://169.254.169.254/latest/meta-data/",
    "http://192.0.2.1/media.mp4",
    "http://224.0.0.1/media.mp4",
    "http://[::1]/media.mp4",
    "http://[fc00::1]/media.mp4",
    "http://[fe80::1]/media.mp4",
    "http://[ff02::1]/media.mp4",
    "http://[2001:db8::1]/media.mp4",
  ]) {
    await assert.rejects(assertSafeOutboundMediaUrl(url), MediaUrlPolicyError);
  }
});

test("media URL policy rejects local names, credentials, and unsupported schemes", async () => {
  for (const url of [
    "http://localhost/video.mp4",
    "http://media.localhost/video.mp4",
    "http://service.internal/video.mp4",
    "http://media/video.mp4",
    "file:///etc/passwd",
    "ftp://media.example.com/video.mp4",
    "https://user:secret@media.example.com/video.mp4",
  ]) {
    await assert.rejects(
      assertSafeOutboundMediaUrl(url, { resolveDns: false }),
      MediaUrlPolicyError,
    );
  }
});

test("media URL policy checks DNS results and accepts public HTTPS", async () => {
  await assert.rejects(
    assertSafeOutboundMediaUrl("https://media.example.com/video.mp4", {
      lookup: async () => [{ address: "127.0.0.1", family: 4 }],
    }),
    MediaUrlPolicyError,
  );
  await assert.rejects(
    assertSafeOutboundMediaUrl("https://media.example.com/video.mp4", {
      lookup: async () => [
        { address: "93.184.216.34", family: 4 },
        { address: "fe80::1", family: 6 },
      ],
    }),
    MediaUrlPolicyError,
  );
  assert.equal(
    (await assertSafeOutboundMediaUrl(
      "https://media.example.com/video.mp4?signature=secret",
      { lookup: publicLookup },
    )).kind,
    "http",
  );
  assert.equal(
    (await assertSafeOutboundMediaUrl("https://[2606:4700:4700::1111]/video.mp4")).kind,
    "http",
  );
});

test("media URL policy preserves strict Supabase refs and bounded WAV data", async () => {
  assert.equal(
    (await assertSafeOutboundMediaUrl(
      "supabase://raw-uploads/workspaces/ws/projects/project/video.mp4",
      { allowSupabase: true },
    )).kind,
    "supabase",
  );
  await assert.rejects(
    assertSafeOutboundMediaUrl("supabase://raw-uploads/workspaces/ws/../secret.mp4", {
      allowSupabase: true,
    }),
    MediaUrlPolicyError,
  );
  assert.equal(
    (await assertSafeOutboundMediaUrl("data:audio/wav;base64,UklGRg==", {
      allowDataAudio: true,
    })).kind,
    "data_audio",
  );
});

test("render manifest validation covers manual video/audio and provider URLs", async () => {
  const props = {
    sourceVideo: { src: "supabase://raw-uploads/workspaces/ws/source.mp4" },
    assets: [{ kind: "video", src: "https://media.example.com/broll.mp4" }],
    editPlan: {
      manual_elements: [
        { type: "sfx", properties: { assetUrl: "data:audio/wav;base64,UklGRg==" } },
        { type: "broll", properties: { src: "https://media.example.com/manual.mp4" } },
      ],
    },
    musicSelection: {
      track: {
        raw_metadata: {
          audio_url: "https://media.example.com/music.mp3",
          sfx_urls: ["https://media.example.com/pop.wav"],
        },
      },
    },
  };
  await validateRenderMediaManifest(props, { lookup: publicLookup });
  props.editPlan.manual_elements[1].properties.src = "http://127.0.0.1/private.mp4";
  await assert.rejects(
    validateRenderMediaManifest(props, { lookup: publicLookup }),
    MediaUrlPolicyError,
  );
});

test("safe media fetch revalidates redirects and bounds redirect count", async () => {
  let calls = 0;
  const privateRedirect = async () => {
    calls += 1;
    return new Response(null, {
      status: 302,
      headers: { location: "http://127.0.0.1/private.mp4?token=hidden" },
    });
  };
  await assert.rejects(
    safeFetchMedia("https://media.example.com/start.mp4?token=secret", {
      lookup: publicLookup,
      fetchImpl: privateRedirect,
    }),
    (error) => {
      assert.equal(error instanceof MediaUrlPolicyError, true);
      assert.equal(error.message.includes("secret"), false);
      assert.equal(error.message.includes("hidden"), false);
      return true;
    },
  );
  assert.equal(calls, 1);

  const redirectForever = async () => new Response(null, {
    status: 302,
    headers: { location: "/next.mp4" },
  });
  await assert.rejects(
    safeFetchMedia("https://media.example.com/start.mp4", {
      lookup: publicLookup,
      fetchImpl: redirectForever,
      maxRedirects: 1,
    }),
    /redirect limit exceeded/,
  );
});
