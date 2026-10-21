import type { Metadata } from "next";
import { Plus_Jakarta_Sans } from "next/font/google";
import { headers } from "next/headers";
import { CommandPaletteProvider } from "@/components/command-palette";
import { MotionProvider } from "@/components/motion-provider";
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

const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
  variable: "--font-sans",
});

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  // proxy.ts issues a CSP nonce per request. Reading it also opts this layout
  // into dynamic rendering, which the runtime config needs.
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  // Source compatibility: the JSON data script intentionally does not render
  // nonce={nonce}; it is data, not executable code. Next adds the nonce to its
  // executable scripts from the request CSP automatically.
  void nonce;
  return (
    <html
      lang="en"
      data-theme="light"
      className={jakarta.variable}
      suppressHydrationWarning
    >
      <head>
        {/* Data, not code: the CSP does not apply to JSON script elements. */}
        <script
          id={PUBLIC_CONFIG_ELEMENT_ID}
          type="application/json"
          dangerouslySetInnerHTML={{
            __html: serializePublicConfig(getPublicConfig()),
          }}
        />
      </head>
      <body>
        <MotionProvider>
          <ToastProvider>
            <CommandPaletteProvider>{children}</CommandPaletteProvider>
          </ToastProvider>
        </MotionProvider>
      </body>
    </html>
  );
}
