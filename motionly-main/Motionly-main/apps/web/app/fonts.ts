import { Plus_Jakarta_Sans } from "next/font/google";

// The app font, shared by the root layout and the global error page (which
// replaces the layout when it renders). Plus Jakarta Sans is a variable font:
// one file covers every weight we use (400–800) instead of five static ones.
export const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});
