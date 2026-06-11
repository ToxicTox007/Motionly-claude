import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Motionly",
  description: "AI reel creation for creators, founders, and agencies.",
  icons: {
    icon: "/icon.svg",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
