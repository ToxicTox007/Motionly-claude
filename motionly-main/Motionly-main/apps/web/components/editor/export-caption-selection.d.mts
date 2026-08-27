export type CaptionSelectionInput = {
  includeCaptions?: boolean;
  captionTrackId?: string | null;
  localizedPublishPackageId?: string | null;
};

export type CaptionSelection = {
  caption_track_id: string | null;
  localized_publish_package_id: string | null;
};

export function captionSelectionForExport(
  input?: CaptionSelectionInput,
): CaptionSelection;

export function captionSelectionForBulkExport(
  input?: CaptionSelectionInput & {
    targetIncludesCaptions?: readonly boolean[];
  },
): CaptionSelection;
