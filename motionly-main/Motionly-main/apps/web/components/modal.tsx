"use client";

import { useCallback, useId, type ReactNode } from "react";
import { IconButton } from "@/components/button";
import { PixelIcon } from "@/components/pixel-icons";
import { useFocusTrap } from "@/lib/useFocusTrap";

/**
 * The shared dialog. It traps focus itself (Tab wraps, Escape closes, focus
 * returns to the opener), so callers no longer wire their own trap; only the
 * topmost of several open dialogs reacts to Escape. Long content scrolls
 * inside the dialog instead of running off short screens. `closeDisabled`
 * keeps it open while an action that must finish is running.
 */
export function Modal({
  open,
  title,
  description,
  children,
  footer,
  onClose,
  closeDisabled = false,
}: {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  closeDisabled?: boolean;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const requestClose = useCallback(() => {
    if (!closeDisabled) onClose();
  }, [closeDisabled, onClose]);
  const trapRef = useFocusTrap<HTMLDivElement>(open, requestClose);

  if (!open) return null;
  return (
    <>
      {/* Pointer-only backdrop: hidden from assistive tech and the Tab
          order; keyboard users close with Escape or the Close button. */}
      <div
        aria-hidden="true"
        data-testid="modal-backdrop"
        className="fixed inset-0 z-40 bg-background/60 backdrop-blur-sm"
        onClick={requestClose}
      />
      <div
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        aria-busy={closeDisabled || undefined}
        tabIndex={-1}
        className="fixed left-1/2 top-1/2 z-50 max-h-[calc(100dvh-2rem)] w-[min(34rem,calc(100vw-1.5rem))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto overscroll-contain rounded-neu-xl bg-surface p-6 shadow-neu-raised-lg"
      >
        <div className="flex items-start justify-between gap-4 pb-3">
          <div className="min-w-0">
            <h2 id={titleId} className="text-lg font-bold text-foreground">
              {title}
            </h2>
            {description ? (
              <p
                id={descriptionId}
                className="mt-1 text-sm font-medium text-muted-foreground"
              >
                {description}
              </p>
            ) : null}
          </div>
          <IconButton
            label="Close modal"
            variant="ghost"
            size="icon"
            onClick={requestClose}
            disabled={closeDisabled}
          >
            <PixelIcon name="x" className="size-4" />
          </IconButton>
        </div>
        {children ? <div className="py-4">{children}</div> : null}
        {footer ? (
          <div className="mt-2 flex flex-wrap justify-end gap-3 pt-4">
            {footer}
          </div>
        ) : null}
      </div>
    </>
  );
}
