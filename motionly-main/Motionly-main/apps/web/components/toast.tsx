"use client";

import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
} from "react";
import { AnimatePresence, m } from "motion/react";
import { PixelIcon } from "@/components/pixel-icons";

export type ToastVariant = "success" | "error" | "warning" | "info" | "loading";

export interface Toast {
  id: string;
  title: string;
  description?: string;
  variant: ToastVariant;
  duration?: number;
}

interface ToastContextType {
  toast: (options: Omit<Toast, "id">) => string;
  dismiss: (id: string) => void;
  toasts: Toast[];
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return context;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback((options: Omit<Toast, "id">) => {
    const id = Math.random().toString(36).substring(2, 9);
    const newToast: Toast = {
      ...options,
      id,
      duration: options.duration ?? (options.variant === "loading" ? 0 : 4500),
    };

    setToasts((prev) => [...prev, newToast]);
    return id;
  }, []);

  return (
    <ToastContext.Provider value={{ toast, dismiss, toasts }}>
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
  return (
    <div
      aria-live="polite"
      aria-relevant="additions"
      className="fixed bottom-3 right-3 z-[200] flex w-[calc(100vw-1.5rem)] max-w-[420px] flex-col items-end gap-2 pointer-events-none sm:bottom-4 sm:right-4"
    >
      <AnimatePresence initial={false}>
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </AnimatePresence>
    </div>
  );
}

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: Toast;
  onDismiss: () => void;
}) {
  const { title, description, variant, duration } = toast;

  useEffect(() => {
    if (duration && duration > 0) {
      const timer = setTimeout(() => {
        onDismiss();
      }, duration);
      return () => clearTimeout(timer);
    }
  }, [duration, onDismiss]);

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
      layout
      initial={{ y: 16, scale: 0.98 }}
      animate={{ y: 0, scale: 1 }}
      exit={{ scale: 0.96 }}
      transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
      role={variant === "error" || variant === "warning" ? "alert" : "status"}
      className="pointer-events-auto flex w-full min-w-0 max-w-[420px] items-start gap-3 rounded-neu-md bg-surface p-4 text-foreground shadow-neu-raised"
    >
      {/* Icon well */}
      <div className="flex size-8 shrink-0 items-center justify-center rounded-neu-sm shadow-neu-inset-sm">
        {icons[variant]}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <h3 className="text-sm font-semibold text-foreground leading-tight">
          {title}
        </h3>
        {description && (
          <p className="mt-1.5 text-xs font-medium text-muted-foreground leading-relaxed">
            {description}
          </p>
        )}
      </div>

      {/* Dismiss */}
      <button
        onClick={onDismiss}
        className="flex size-8 shrink-0 items-center justify-center rounded-neu-sm bg-surface text-muted-foreground shadow-neu-raised-sm transition-shadow hover:text-foreground active:shadow-neu-pressed"
        aria-label="Dismiss toast"
      >
        <PixelIcon name="x" className="size-3.5" />
      </button>
    </m.div>
  );
}
