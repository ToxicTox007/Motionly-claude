/**
 * Build the caption references for a render request.
 *
 * A localized package supplies its own reviewed caption track server-side, so the
 * raw source track must never accompany it. Disabling captions omits the raw
 * track altogether.
 */
export function captionSelectionForExport({
  includeCaptions,
  captionTrackId = null,
  localizedPublishPackageId = null,
} = {}) {
  const packageId = localizedPublishPackageId || null;
  return {
    caption_track_id:
      includeCaptions && !packageId ? (captionTrackId || null) : null,
    localized_publish_package_id: packageId,
  };
}

/**
 * A bulk request needs a raw track only when at least one target burns captions.
 * A localized package still takes precedence for every target.
 */
export function captionSelectionForBulkExport({
  targetIncludesCaptions = [],
  ...selection
} = {}) {
  return captionSelectionForExport({
    ...selection,
    includeCaptions:
      Boolean(selection.includeCaptions) ||
      targetIncludesCaptions.some((includeCaptions) => Boolean(includeCaptions)),
  });
}
