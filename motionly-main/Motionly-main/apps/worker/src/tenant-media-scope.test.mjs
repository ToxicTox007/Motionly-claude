import assert from "node:assert/strict";
import test from "node:test";

import {
  assertSupabaseTenantScope,
  validateRenderTenantScope,
} from "./remotion.mjs";

const scope = {
  workspaceId: "workspace-a",
  projectId: "project-a",
};
const own = (suffix) =>
  `supabase://raw-uploads/workspaces/${scope.workspaceId}/projects/${scope.projectId}/${suffix}`;
const foreign = (suffix) =>
  `supabase://raw-uploads/workspaces/workspace-b/projects/project-b/${suffix}`;

function completeManifest() {
  return {
    sourceVideo: { src: own("raw/source.mp4") },
    segmentationMatte: { src: own("mattes/source.webm") },
    assets: [{ kind: "video", src: own("broll/asset.mp4") }],
    editPlan: {
      manual_elements: [
        { type: "broll", properties: { assetUrl: own("broll/manual.mp4") } },
        { type: "graphic", properties: { assetUrl: own("graphics/logo.png") } },
        {
          type: "overlay",
          properties: { background: `url(${own("graphics/background.png")})` },
        },
      ],
    },
    musicSelection: {
      track: {
        raw_metadata: {
          audio_bucket: "raw-uploads",
          audio_path: `workspaces/${scope.workspaceId}/projects/${scope.projectId}/music/track.mp3`,
          sfx_urls: [own("sfx/pop.wav")],
        },
      },
    },
    sourceStems: {
      vocals: {
        bucket: "raw-uploads",
        path: `workspaces/${scope.workspaceId}/projects/${scope.projectId}/stems/vocals.wav`,
      },
      no_vocals: { src: own("stems/no-vocals.wav") },
    },
  };
}

test("internal media signer accepts only the render job tenant prefix", () => {
  assert.doesNotThrow(() =>
    assertSupabaseTenantScope(own("raw/source.mp4"), scope),
  );
  assert.throws(
    () => assertSupabaseTenantScope(foreign("raw/secret.mp4"), scope),
    /outside the render job tenant scope/,
  );
  assert.throws(
    () => assertSupabaseTenantScope(own("raw/source.mp4"), null),
    /Tenant scope is required/,
  );
});

test("render tenant validation covers every internal media layer", () => {
  assert.doesNotThrow(() =>
    validateRenderTenantScope(completeManifest(), scope),
  );

  const mutations = [
    (manifest) => {
      manifest.sourceVideo.src = foreign("raw/source.mp4");
    },
    (manifest) => {
      manifest.assets[0].src = foreign("broll/asset.mp4");
    },
    (manifest) => {
      manifest.editPlan.manual_elements[0].properties.assetUrl =
        foreign("broll/manual.mp4");
    },
    (manifest) => {
      manifest.editPlan.manual_elements[1].properties.assetUrl =
        foreign("graphics/logo.png");
    },
    (manifest) => {
      manifest.editPlan.manual_elements[2].properties.background = `url(${foreign("graphics/background.png")})`;
    },
    (manifest) => {
      manifest.segmentationMatte.src = foreign("mattes/source.webm");
    },
    (manifest) => {
      manifest.musicSelection.track.raw_metadata.audio_path =
        "workspaces/workspace-b/projects/project-b/music/track.mp3";
    },
    (manifest) => {
      manifest.musicSelection.track.raw_metadata.sfx_urls = [
        foreign("sfx/pop.wav"),
      ];
    },
    (manifest) => {
      manifest.sourceStems.vocals.path =
        "workspaces/workspace-b/projects/project-b/stems/vocals.wav";
    },
    (manifest) => {
      manifest.sourceStems.no_vocals.src = foreign("stems/no-vocals.wav");
    },
  ];

  for (const mutate of mutations) {
    const manifest = completeManifest();
    mutate(manifest);
    assert.throws(
      () => validateRenderTenantScope(manifest, scope),
      /outside the render job tenant scope/,
    );
  }
});

test("public HTTPS media remains subject to SSRF policy, not tenant prefixes", () => {
  const manifest = completeManifest();
  manifest.assets[0].src = "https://media.example.com/public.mp4";
  assert.doesNotThrow(() => validateRenderTenantScope(manifest, scope));
});
