"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AnimatePresence, m } from "motion/react";
import { PixelIcon } from "@/components/pixel-icons";

export type ToastVariant = "success" | "error" | "warning" | "info" | "loading";

export interface Toast {
  id: string;
  title: string;
  description?: string;
  variant: ToastVariant;
  /** Milliseconds before it closes; 0 keeps it until dismissed. */
  duration?: number;
}

interface ToastContextType {
  toast: (options: Omit<Toast, "id">) => string;
  dismiss: (id: string) => void;
  toasts: Toast[];
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

/** Success and info toasts close after this long (unless hovered/focused). */
export const TOAST_DURATION_MS = 6000;
/** At most this many toasts are on screen; the rest wait their turn. */
export const MAX_VISIBLE_TOASTS = 3;

// WCAG 2.2.1: errors and warnings carry information the viewer must not miss,
// so they stay until dismissed. Loading toasts are dismissed by their caller.
function durationFor(options: Omit<Toast, "id">): number {
  if (options.variant === "error" || options.variant === "warning") return 0;
  if (options.variant === "loading") return options.duration ?? 0;
  return options.duration ?? TOAST_DURATION_MS;
}

function isUrgent(variant: ToastVariant) {
  return variant === "error" || variant === "warning";
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return context;
}

let toastSequence = 0;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback((options: Omit<Toast, "id">) => {
    toastSequence += 1;
    const id = `toast-${toastSequence}-${Math.random().toString(36).slice(2, 7)}`;
    setToasts((prev) => [
      ...prev,
      { ...options, id, duration: durationFor(options) },
    ]);
    return id;
  }, []);

  const value = useMemo(
    () => ({ toast, dismiss, toasts }),
    [toast, dismiss, toasts],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastContainer toasts={toasts} dismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function ToastContainer({
  toasts,
  dismiss,
}: {
  toasts: Toast[];
  dismiss: (id: string) => void;
}) {
  // Queue beyond the cap so a burst never covers the page.
  const visible = toasts.slice(0, MAX_VISIBLE_TOASTS);
  const urgent = visible.filter((t) => isUrgent(t.variant));
  const calm = visible.filter((t) => !isUrgent(t.variant));
  return (
    <div
      className="fixed bottom-3 right-3 z-[200] flex w-[calc(100vw-1.5rem)] max-w-[420px] flex-col items-end gap-2 pointer-events-none sm:bottom-4 sm:right-4"
      style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
    >
      {/* Two sibling live regions (never nested): errors interrupt, the rest
          wait for a pause in speech. */}
      <div
        aria-live="assertive"
        aria-relevant="additions"
        className="flex w-full flex-col items-end gap-2"
      >
        <AnimatePresence initial={false}>
          {urgent.map((t) => (
            <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
          ))}
        </AnimatePresence>
      </div>
      <div
        aria-live="polite"
        aria-relevant="additions"
        className="flex w-full flex-col items-end gap-2"
      >
        <AnimatePresence initial={false}>
          {calm.map((t) => (
            <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}

/**
 * One toast. Its timer pauses while the pointer is over it or focus is inside
 * it (the remaining time is kept), and does not restart when other toasts
 * come and go: onDismiss is the provider's stable dismiss(id).
 */
function ToastItem({
  toast,
  onDismiss,
}: {
  toast: Toast;
  onDismiss: (id: string) => void;
}) {
  const { id, title, description, variant, duration = 0 } = toast;
  const remainingRef = useRef(duration);
  const startedAtRef = useRef<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hoverRef = useRef(false);
  const focusRef = useRef(false);

  const stop = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (startedAtRef.current !== null) {
      remainingRef.current = Math.max(
        0,
        remainingRef.current - (Date.now() - startedAtRef.current),
      );
      startedAtRef.current = null;
    }
  }, []);

  const start = useCallback(() => {
    if (duration <= 0 || timerRef.current !== null) return;
    if (hoverRef.current || focusRef.current) return;
    startedAtRef.current = Date.now();
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      onDismiss(id);
    }, remainingRef.current);
  }, [duration, id, onDismiss]);

  useEffect(() => {
    start();
    return stop;
  }, [start, stop]);

  const icons: Record<ToastVariant, React.ReactNode> = {
    success: <PixelIcon name="success" className="size-5 text-success" />,
    error: <PixelIcon name="warning" className="size-5 text-danger" />,
    warning: <PixelIcon name="warning" className="size-5 text-warning" />,
    info: <PixelIcon name="info" className="size-5 text-info" />,
    loading: (
      <span
        className="size-4 animate-spin rounded-full border-2 border-hairline border-t-primary"
        aria-hidden="true"
      />
    ),
  };

  return (
    <m.div
      initial={{ y: 16, scale: 0.98 }}
      animate={{ y: 0, scale: 1 }}
      exit={{ scale: 0.96 }}
      transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
      data-variant={variant}
      onMouseEnter={() => {
        hoverRef.current = true;
        stop();
      }}
      onMouseLeave={() => {
        hoverRef.current = false;
        start();
      }}
      onFocus={() => {
        focusRef.current = true;
        stop();
      }}
      onBlur={(event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node | null))
          return;
        focusRef.current = false;
        start();
      }}
      className="pointer-events-auto flex w-full min-w-0 max-w-[420px] items-start gap-3 rounded-neu-md bg-surface p-4 text-foreground shadow-neu-raised"
    >
      {/* Icon well */}
      <div className="flex size-8 shrink-0 items-center justify-center rounded-neu-sm shadow-neu-inset-sm">
        {icons[variant]}
      </div>

      {/* Content: a paragraph, not a heading, so the page outline is intact. */}
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-foreground leading-tight">
          {title}
        </p>
        {description && (
          <p className="mt-1.5 text-xs font-medium text-muted-foreground leading-relaxed">
            {description}
          </p>
        )}
      </div>

      {/* Dismiss */}
      <button
        type="button"
        onClick={() => onDismiss(id)}
        className="flex size-8 shrink-0 items-center justify-center rounded-neu-sm bg-surface text-muted-foreground shadow-neu-raised-sm transition-shadow hover:text-foreground active:shadow-neu-pressed"
        aria-label={`Dismiss: ${title}`}
      >
        <PixelIcon name="x" className="size-3.5" />
      </button>
    </m.div>
  );
}
