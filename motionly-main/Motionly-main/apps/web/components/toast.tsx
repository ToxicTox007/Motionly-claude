"use client";

import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
} from "react";
import { cn } from "@/lib/utils";
import {
  X,
  CheckCircle,
  AlertTriangle,
  Info,
  Loader2,
  XCircle,
} from "lucide-react";

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
      duration: options.duration ?? (options.variant === "loading" ? 0 : 5000),
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

function ToastContainer({
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
      className="fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-3 px-4 sm:bottom-6 sm:right-6 sm:px-0"
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

  const icons = {
    success: <CheckCircle className="size-5 text-success" />,
    error: <XCircle className="size-5 text-danger" />,
    warning: <AlertTriangle className="size-5 text-warning" />,
    info: <Info className="size-5 text-info" />,
    loading: <Loader2 className="size-5 animate-spin text-primary" />,
  };

  return (
    <div
      role={variant === "error" || variant === "warning" ? "alert" : "status"}
      className={cn(
        "pixel-panel pixel-corners flex w-full gap-3 p-4 shadow-lg border-2 bg-surface",
        variant === "success" && "border-success/60",
        variant === "error" && "border-danger/60",
        variant === "warning" && "border-warning/60",
        variant === "info" && "border-info/60",
        variant === "loading" && "border-primary/60",
      )}
    >
      <div className="flex size-6 shrink-0 items-center justify-center">
        {icons[variant]}
      </div>
      <div className="flex-1 min-w-0">
        <h3 className="text-sm font-black text-foreground uppercase tracking-wide leading-tight">
          {title}
        </h3>
        {description && (
          <p className="mt-1 text-xs font-bold text-muted-foreground leading-relaxed">
            {description}
          </p>
        )}
      </div>
      <button
        onClick={onDismiss}
        className="flex size-6 shrink-0 items-center justify-center border border-border bg-muted/30 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        aria-label="Dismiss toast"
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}
