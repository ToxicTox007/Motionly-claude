import assert from "node:assert/strict";
import test from "node:test";

import {
  captionSelectionForBulkExport,
  captionSelectionForExport,
} from "../components/editor/export-caption-selection.mjs";

test("captionless single exports omit the raw caption track", () => {
  assert.deepEqual(
    captionSelectionForExport({
      includeCaptions: false,
      captionTrackId: "source-caption-track",
    }),
    {
      caption_track_id: null,
      localized_publish_package_id: null,
    },
  );
});

test("localized package exports select the package and omit the raw caption track", () => {
  assert.deepEqual(
    captionSelectionForExport({
      includeCaptions: true,
      captionTrackId: "source-caption-track",
      localizedPublishPackageId: "reviewed-es-package",
    }),
    {
      caption_track_id: null,
      localized_publish_package_id: "reviewed-es-package",
    },
  );
});

test("bulk caption selection keeps the raw track only when a non-localized target needs it", () => {
  assert.deepEqual(
    captionSelectionForBulkExport({
      includeCaptions: false,
      targetIncludesCaptions: [false, true],
      captionTrackId: "source-caption-track",
    }),
    {
      caption_track_id: "source-caption-track",
      localized_publish_package_id: null,
    },
  );
  assert.deepEqual(
    captionSelectionForBulkExport({
      includeCaptions: true,
      targetIncludesCaptions: [true],
      captionTrackId: "source-caption-track",
      localizedPublishPackageId: "reviewed-es-package",
    }),
    {
      caption_track_id: null,
      localized_publish_package_id: "reviewed-es-package",
    },
  );
});
