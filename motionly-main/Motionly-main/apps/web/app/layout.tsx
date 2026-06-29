import type { Metadata } from "next";
import Script from "next/script";
import { ToastProvider } from "@/components/toast";
import "./globals.css";

export const metadata: Metadata = {
  title: "Motionly",
  description: "AI reel creation for creators, founders, and agencies.",
  icons: {
    icon: "/icon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <Script id="motionly-theme-init" strategy="beforeInteractive">
          {`(() => {
            try {
              const stored = window.localStorage.getItem("motionly-theme");
              const theme = stored === "dark" || stored === "light"
                ? stored
                : window.matchMedia("(prefers-color-scheme: dark)").matches
                  ? "dark"
                  : "light";
              document.documentElement.dataset.theme = theme;
            } catch {}
          })();`}
        </Script>
        <ToastProvider>{children}</ToastProvider>
      </body>
    </html>
  );
}
