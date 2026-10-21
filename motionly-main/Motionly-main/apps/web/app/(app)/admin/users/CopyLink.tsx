"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";

// Shows a one-time reset link with a copy button. The link is held only in the
// browser for as long as this page is open; it is never persisted.
export function CopyLink({ link }: { link: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <code className="flex-1 overflow-x-auto rounded-neu bg-surface px-3 py-2 font-mono text-xs text-foreground shadow-neu-inset-sm">
        {link}
      </code>
      <button
        type="button"
        onClick={copy}
        aria-label="Copy reset link"
        className="inline-flex h-10 items-center gap-2 rounded-neu bg-primary px-4 text-xs font-semibold text-primary-foreground shadow-neu-glow transition-all duration-200 hover:brightness-105 active:shadow-neu-pressed"
      >
        {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}
