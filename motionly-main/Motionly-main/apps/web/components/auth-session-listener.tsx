"use client";

import { useEffect } from "react";
import { isSignedInPath } from "@/lib/signed-in-routes.mjs";
import {
  clearMotionlyStorage,
  subscribeToSignOut,
} from "@/lib/session-storage";
import { createClient } from "@/lib/supabase/browser";

function leaveIfSignedInPage(navigate: (url: string) => void) {
  if (isSignedInPath(window.location.pathname)) {
    const next = `${window.location.pathname}${window.location.search}`;
    navigate(`/login?next=${encodeURIComponent(next)}`);
  }
}

/**
 * Mounted once in the root layout. When the viewer signs out — in this tab
 * (Auth reports SIGNED_OUT, e.g. a revoked session) or in another tab (which
 * announces it) — Motionly's local state is cleared and a signed-in page is
 * left for the login page instead of running on until its next 401.
 */
const assignLocation = (url: string) => window.location.assign(url);

export function AuthSessionListener({
  navigate = assignLocation,
}: {
  /** Full-page navigation (overridable in tests). */
  navigate?: (url: string) => void;
}) {
  useEffect(() => {
    const unsubscribeTabs = subscribeToSignOut(() => {
      clearMotionlyStorage();
      leaveIfSignedInPage(navigate);
    });
    let unsubscribeAuth = () => {};
    try {
      const {
        data: { subscription },
      } = createClient().auth.onAuthStateChange((event) => {
        if (event === "SIGNED_OUT") {
          clearMotionlyStorage();
          leaveIfSignedInPage(navigate);
        }
      });
      unsubscribeAuth = () => subscription.unsubscribe();
    } catch {
      // No public config on this page (e.g. an error document): nothing to do.
    }
    return () => {
      unsubscribeTabs();
      unsubscribeAuth();
    };
  }, [navigate]);
  return null;
}
