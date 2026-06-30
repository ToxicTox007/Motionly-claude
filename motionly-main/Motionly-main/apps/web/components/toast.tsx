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
  CheckCircle2,
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
      className="fixed bottom-4 right-4 z-[200] flex flex-col gap-2 items-end pointer-events-none"
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
    success: (
      <CheckCircle2
        className="size-[18px] text-green-400 shrink-0 mt-px"
        strokeWidth={1.5}
      />
    ),
    error: (
      <XCircle
        className="size-[18px] text-red-400 shrink-0 mt-px"
        strokeWidth={1.5}
      />
    ),
    warning: (
      <AlertTriangle
        className="size-[18px] text-amber-400 shrink-0 mt-px"
        strokeWidth={1.5}
      />
    ),
    info: (
      <Info
        className="size-[18px] text-violet-400 shrink-0 mt-px"
        strokeWidth={1.5}
      />
    ),
    loading: (
      <Loader2
        className="size-[18px] animate-spin text-primary shrink-0 mt-px"
        strokeWidth={1.5}
      />
    ),
  };

  return (
    <div
      role={variant === "error" || variant === "warning" ? "alert" : "status"}
      className={cn(
        "pointer-events-auto min-w-[300px] max-w-[420px] bg-surface text-foreground border-2 border-foreground border-l-[6px] shadow-[4px_4px_0px_#000] dark:shadow-[4px_4px_0px_#fff] flex gap-3 p-4 items-start animate-slide-in-up rounded-md",
        variant === "success" && "border-l-green-500",
        variant === "error" && "border-l-red-500",
        variant === "warning" && "border-l-amber-500",
        (variant === "info" || variant === "loading") && "border-l-accent",
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
        className="shrink-0 size-6 border-2 border-foreground rounded bg-surface flex items-center justify-center text-foreground hover:bg-surface-elevated transition-colors shadow-[1px_1px_0px_#000] dark:shadow-[1px_1px_0px_#fff] active:translate-x-0.5 active:translate-y-0.5 active:shadow-none"
        aria-label="Dismiss toast"
      >
        <X className="size-3" strokeWidth={2} />
      </button>
    </div>
  );
}
