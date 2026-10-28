import type { Config } from "tailwindcss";

// Light-only neumorphic ("soft UI") design system. `darkMode` is pinned to an
// unused selector so any stray `dark:` class never applies (nothing sets
// data-theme="dark"). Neumorphic shadows pair a light highlight (--neu-light,
// top-left) with a dark shadow (--neu-dark, bottom-right).
const raised = (d: number) =>
  `${d}px ${d}px ${d * 2}px hsl(var(--neu-dark)), -${d}px -${d}px ${d * 2}px hsl(var(--neu-light))`;
const inset = (d: number) =>
  `inset ${d}px ${d}px ${d * 2}px hsl(var(--neu-dark)), inset -${d}px -${d}px ${d * 2}px hsl(var(--neu-light))`;

const config: Config = {
  darkMode: ["selector", '[data-theme="dark"]'],
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "hsl(var(--ink))",
        border: "hsl(var(--border))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: "hsl(var(--primary))",
        "primary-foreground": "hsl(var(--primary-foreground))",
        muted: "hsl(var(--muted))",
        "muted-foreground": "hsl(var(--muted-foreground))",
        surface: "hsl(var(--surface))",
        "surface-elevated": "hsl(var(--surface-elevated))",
        "surface-soft": "hsl(var(--surface-soft))",
        card: "hsl(var(--surface))",
        danger: "hsl(var(--danger))",
        "danger-foreground": "hsl(var(--danger-foreground))",
        warning: "hsl(var(--warning))",
        success: "hsl(var(--success))",
        info: "hsl(var(--info))",
        accent: "hsl(var(--accent))",
        "accent-foreground": "hsl(var(--accent-foreground))",
        // Neumorphic lines + focus
        hairline: "hsl(var(--hairline))",
        control: "hsl(var(--control-border))",
        focus: "hsl(var(--focus))",
        "neu-light": "hsl(var(--neu-light))",
        "neu-dark": "hsl(var(--neu-dark))",
      },
      // Brand and status colours used as text keep WCAG AA contrast.
      textColor: {
        primary: "hsl(var(--primary-text))",
        danger: "hsl(var(--danger-text))",
        warning: "hsl(var(--warning-text))",
        success: "hsl(var(--success-text))",
        info: "hsl(var(--info-text))",
        accent: "hsl(var(--accent-text))",
      },
      borderColor: {
        hairline: "hsl(var(--hairline))",
        control: "hsl(var(--control-border))",
        focus: "hsl(var(--focus))",
      },
      divideColor: {
        hairline: "hsl(var(--hairline))",
      },
      ringColor: {
        focus: "hsl(var(--focus))",
      },
      outlineColor: {
        focus: "hsl(var(--focus))",
      },
      fontFamily: {
        sans: [
          "var(--font-sans)",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
      },
      // One radius scale. Tailwind's own names that duplicated a neu-* value
      // pixel for pixel were removed; tests/design-system.test.mjs keeps them
      // out of the app so radii stay on this scale.
      borderRadius: {
        "2xl": "32px",
        "neu-sm": "10px",
        neu: "14px",
        "neu-md": "18px",
        "neu-lg": "22px",
        "neu-xl": "28px",
        "neu-2xl": "36px",
      },
      boxShadow: {
        // Canonical neumorphic names
        "neu-raised-sm": raised(3),
        "neu-raised": raised(6),
        "neu-raised-lg": raised(12),
        "neu-inset-sm": inset(2),
        "neu-inset": inset(4),
        "neu-pressed": inset(4),
        "neu-glow": `${raised(6)}, 0 0 16px hsl(var(--primary) / 0.35)`,
        // Tailwind's sm/DEFAULT/md/lg/xl duplicated neu-raised-* exactly and
        // were removed (see the radius note above).
        none: "none",
      },
      transitionTimingFunction: {
        spring: "cubic-bezier(0.16, 1, 0.3, 1)",
        "ease-out-expo": "cubic-bezier(0.16, 1, 0.3, 1)",
        smooth: "cubic-bezier(0, 0, 0.2, 1)",
      },
      transitionDuration: {
        fast: "120ms",
        base: "220ms",
        slow: "380ms",
      },
      // The small end of the type scale. 2xs and xxs set only the size (no
      // line height), so they replace the old text-[10px] / text-[11px]
      // arbitrary values without moving any text.
      fontSize: {
        "2xs": "0.625rem",
        xxs: "0.6875rem",
        xs: ["0.75rem", { lineHeight: "1.125rem" }],
      },
      animation: {
        "fade-in": "fade-in 220ms cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "fade-up": "fade-up 220ms cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "slide-in-right":
          "slide-in-right 220ms cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "slide-in-up":
          "slide-in-up 220ms cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "scale-in": "scale-in 200ms cubic-bezier(0.16, 1, 0.3, 1) forwards",
        shimmer: "shimmer 1.8s ease-in-out infinite",
        float: "float 3s ease-in-out infinite",
        pulse: "pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "spin-slow": "spin 3s linear infinite",
        glow: "glow-pulse 2s ease-in-out infinite",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "fade-up": {
          from: { transform: "translateY(12px)" },
          to: { transform: "translateY(0)" },
        },
        "slide-in-right": {
          from: { transform: "translateX(16px)" },
          to: { transform: "translateX(0)" },
        },
        "slide-in-up": {
          from: { transform: "translateY(16px)" },
          to: { transform: "translateY(0)" },
        },
        "scale-in": {
          from: { transform: "scale(0.96)" },
          to: { transform: "scale(1)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "200% 0" },
          "100%": { backgroundPosition: "-200% 0" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0px)" },
          "50%": { transform: "translateY(-8px)" },
        },
        "glow-pulse": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.6" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
