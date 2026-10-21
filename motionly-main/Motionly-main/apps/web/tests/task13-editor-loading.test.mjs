// Task 13 / B27: the editor loads even when one panel's request fails.
import assert from "node:assert/strict";
import test from "node:test";

import {
  settlePanels,
  loadPanel,
  hasAnyPanelLoaded,
  failedPanels,
  panelErrorMessage,
} from "../lib/editor-loading.mjs";

test("one failing panel does not reject the whole load", async () => {
  const panels = await settlePanels({
    transcript: async () => ({ text: "hello" }),
    captions: async () => {
      throw new Error("Motionly API request failed (502)");
    },
    broll: async () => ({ slots: [] }),
  });

  assert.equal(panels.transcript.status, "ok");
  assert.deepEqual(panels.transcript.data, { text: "hello" });
  assert.equal(panels.broll.status, "ok");

  // The failed panel is an error state, not a thrown rejection.
  assert.equal(panels.captions.status, "error");
  assert.match(panels.captions.error, /could not be loaded/);
  assert.match(panels.captions.error, /Retry/);
  assert.match(panels.captions.error, /502/);
});

test("the editor still opens when at least one panel loaded", async () => {
  const panels = await settlePanels({
    project: async () => ({ id: "p1" }),
    captions: async () => {
      throw new Error("boom");
    },
  });
  assert.equal(hasAnyPanelLoaded(panels), true);
  assert.deepEqual(failedPanels(panels), ["captions"]);
});

test("loadPanel never throws and tags the panel label", async () => {
  const result = await loadPanel("Captions", async () => {
    throw new Error("nope");
  });
  assert.equal(result.status, "error");
  assert.match(result.error, /^Captions could not be loaded\./);
});

test("panelErrorMessage is accessible and actionable for non-Error values", () => {
  const message = panelErrorMessage("Transcript", "offline");
  assert.match(message, /Transcript could not be loaded\./);
  assert.match(message, /offline/);
  assert.match(message, /Use Retry to try again\./);
});
