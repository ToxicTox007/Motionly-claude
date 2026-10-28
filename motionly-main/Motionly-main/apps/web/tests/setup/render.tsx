import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MotionProvider } from "@/components/motion-provider";
import { ToastProvider } from "@/components/toast";

function AppProviders({ children }: { children: ReactNode }) {
  return (
    <MotionProvider>
      <ToastProvider>{children}</ToastProvider>
    </MotionProvider>
  );
}

/** Renders inside the same providers the root layout mounts. */
export function renderWithProviders(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
) {
  return render(ui, { wrapper: AppProviders, ...options });
}
