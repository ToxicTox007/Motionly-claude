"use client";

import { CopyField } from "@/components/copy-field";

// Shows a one-time reset link with a copy button. The link is held only in the
// browser for as long as this page is open; it is never persisted. A failed
// copy selects the link and says how to copy it by hand.
export function CopyLink({ link }: { link: string }) {
  return <CopyField value={link} label="Reset link" />;
}
