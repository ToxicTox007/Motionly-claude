import type { Metadata } from "next";
import { headers } from "next/headers";
import { ToastProvider } from "@/components/toast";
import { getPublicConfig } from "@/lib/env";
import {
  PUBLIC_CONFIG_ELEMENT_ID,
  serializePublicConfig,
} from "@/lib/runtime-config.mjs";
import "./globals.css";

// Each page sets its own title with PageTitle (components/page-title.tsx).
export const metadata: Metadata = {
  description: "AI reel creation for creators, founders, and agencies.",
  icons: {
    icon: "/icon.svg",
  },
};

const themeScript = `(() => {
  try {
    const stored = window.localStorage.getItem("motionly-theme");
    const theme = stored === "dark" || stored === "light"
      ? stored
      : window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
    document.documentElement.dataset.theme = theme;
  } catch {}
})();`;

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // proxy.ts issues a CSP nonce per request. Reading it also renders every
  // page per request, which the nonce and the runtime config both need.
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Data, not code: the CSP does not apply to JSON script elements. */}
        <script
          id={PUBLIC_CONFIG_ELEMENT_ID}
          type="application/json"
          dangerouslySetInnerHTML={{
            __html: serializePublicConfig(getPublicConfig()),
          }}
        />
        <script
          nonce={nonce}
          dangerouslySetInnerHTML={{ __html: themeScript }}
          suppressHydrationWarning
        />
      </head>
      <body>
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
