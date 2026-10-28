"use client";

import type { ReactNode } from "react";
import { logout } from "@/app/(auth)/actions";
import { announceSignOut } from "@/lib/session-storage";

/**
 * A log-out form. Before the server ends the session, it clears Motionly's
 * browser state and tells other open tabs (lib/session-storage.ts).
 */
export function LogoutForm({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <form
      action={logout}
      onSubmit={() => announceSignOut()}
      className={className}
    >
      {children}
    </form>
  );
}
