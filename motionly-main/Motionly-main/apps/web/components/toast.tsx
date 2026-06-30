"use client";

import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
} from "react";
import { cn } from "@/lib/utils";
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
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
      ))}
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
    success: <PixelIcon name="success" className="mt-px size-5 text-success" />,
    error: <PixelIcon name="warning" className="mt-px size-5 text-danger" />,
    warning: <PixelIcon name="warning" className="mt-px size-5 text-warning" />,
    info: <PixelIcon name="info" className="mt-px size-5 text-info" />,
    loading: (
      <span
        className="mt-1 size-4 animate-spin border-2 border-foreground border-t-primary"
        aria-hidden="true"
      />
    ),
  };

  return (
    <div
      role={variant === "error" || variant === "warning" ? "alert" : "status"}
      className={cn(
        "pointer-events-auto flex w-full min-w-0 max-w-[420px] animate-slide-in-up items-start gap-3 rounded border-2 border-foreground border-l-[10px] bg-surface p-4 text-foreground shadow-brutal",
        variant === "success" && "border-l-success",
        variant === "error" && "border-l-danger",
        variant === "warning" && "border-l-warning",
        (variant === "info" || variant === "loading") && "border-l-primary",
      )}
    >
      {/* Icon */}
      <div className="shrink-0">{icons[variant]}</div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <h3 className="text-sm font-bold text-foreground leading-tight">
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
        className="flex size-7 shrink-0 items-center justify-center rounded border-2 border-foreground bg-surface text-foreground shadow-brutal-sm transition-colors hover:bg-surface-elevated active:translate-x-0.5 active:translate-y-0.5 active:shadow-none"
        aria-label="Dismiss toast"
      >
        <PixelIcon name="x" className="size-3.5" />
      </button>
    </div>
  );
}
