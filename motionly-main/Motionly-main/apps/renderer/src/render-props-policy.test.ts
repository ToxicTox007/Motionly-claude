import assert from "node:assert/strict";
import test from "node:test";

import {
  assertProductionProps,
  renderPropsMediaUrls,
} from "./render-props-policy.js";
import {
  DEFAULT_TEMPLATE_ID,
  getTemplate,
  templates,
} from "./templates/registry.js";
import type { MotionlyRenderProps } from "./types";

const PROXY = "http://127.0.0.1:40123/media";

function props(
  overrides: Partial<MotionlyRenderProps> = {},
): MotionlyRenderProps {
  return {
    sourceVideo: {
      src: `${PROXY}/1`,
      width: 1080,
      height: 1920,
      duration_ms: 1000,
    },
    editPlan: {} as MotionlyRenderProps["editPlan"],
    captions: [],
    words: [],
    assets: [{ id: "a1", kind: "video", src: `${PROXY}/2` }],
    templateId: "founder_reel_v1",
    exportSettings: {} as MotionlyRenderProps["exportSettings"],
    musicSelection: {
      track: {
        full_track_url: `${PROXY}/3`,
        raw_metadata: {
          sfx_urls: [`${PROXY}/4`, "data:audio/wav;base64,UklGRg=="],
        },
      } as unknown as NonNullable<
        MotionlyRenderProps["musicSelection"]
      >["track"],
    },
    sourceStems: { active: "vocals", vocals: { src: `${PROXY}/5` } },
    ...overrides,
  };
}

test("unknown template ids fall back to the default template", () => {
  assert.equal(getTemplate("travel_reel_v1").id, "travel_reel_v1");
  for (const unknown of [
    "no_such_template",
    "",
    undefined,
    null,
    42,
    "__proto__",
    "toString",
  ]) {
    const template = getTemplate(unknown);
    assert.equal(template.id, DEFAULT_TEMPLATE_ID, String(unknown));
    assert.equal(typeof template.background, "string");
  }
  assert.ok(templates[DEFAULT_TEMPLATE_ID]);
});

test("worker-proxied props are accepted", () => {
  assertProductionProps(props());
});

test("every media field must be the worker's loopback proxy", () => {
  const cases: Array<[string, Partial<MotionlyRenderProps>]> = [
    [
      "sourceVideo.src",
      {
        sourceVideo: {
          src: "https://evil.example/v.mp4",
          width: 1,
          height: 1,
          duration_ms: 1,
        },
      },
    ],
    [
      "assets[a1].src",
      {
        assets: [
          {
            id: "a1",
            kind: "video",
            src: "http://169.254.169.254/latest/meta-data/",
          },
        ],
      },
    ],
    [
      "musicSelection.track.full_track_url",
      {
        musicSelection: {
          track: {
            full_track_url: "http://10.0.0.5:9000/song.mp3",
          } as unknown as NonNullable<
            MotionlyRenderProps["musicSelection"]
          >["track"],
        },
      },
    ],
    [
      "sourceStems.vocals.src",
      {
        sourceStems: {
          active: "vocals",
          vocals: { src: "file:///etc/passwd" },
        },
      },
    ],
    [
      "sourceVideo.src",
      {
        sourceVideo: {
          src: "fixture://sample",
          width: 1,
          height: 1,
          duration_ms: 1,
        },
      },
    ],
    [
      "sourceVideo.src",
      {
        sourceVideo: {
          src: "http://127.0.0.1:40123/private",
          width: 1,
          height: 1,
          duration_ms: 1,
        },
      },
    ],
  ];
  for (const [field, override] of cases) {
    assert.throws(
      () => assertProductionProps(props(override)),
      new RegExp(field.replace(/[[\]().]/g, "\\$&")),
      field,
    );
  }
});

test("inline audio is allowed only where audio plays, and only bounded WAV", () => {
  assert.throws(
    () =>
      assertProductionProps(
        props({
          sourceVideo: {
            src: "data:audio/wav;base64,UklGRg==",
            width: 1,
            height: 1,
            duration_ms: 1,
          },
        }),
      ),
    /sourceVideo\.src/,
  );
  assert.throws(
    () =>
      assertProductionProps(
        props({
          assets: [
            { id: "img", kind: "image", src: "data:image/png;base64,AA==" },
          ],
        }),
      ),
    /assets\[img\]\.src/,
  );
});

test("the policy sees every media field the composition reads", () => {
  const fields = renderPropsMediaUrls(props()).map(({ field }) => field);
  assert.deepEqual(fields, [
    "sourceVideo.src",
    "assets[a1].src",
    "musicSelection.track.full_track_url",
    "musicSelection.track.raw_metadata.sfx_urls[0]",
    "musicSelection.track.raw_metadata.sfx_urls[1]",
    "sourceStems.vocals.src",
  ]);
});
