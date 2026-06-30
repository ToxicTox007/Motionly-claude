import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: "hsl(var(--primary))",
        "primary-foreground": "hsl(var(--primary-foreground))",
        muted: "hsl(var(--muted))",
        "muted-foreground": "hsl(var(--muted-foreground))",
        surface: "hsl(var(--surface))",
        "surface-elevated": "hsl(var(--surface-elevated))",
        danger: "hsl(var(--danger))",
        "danger-foreground": "hsl(var(--danger-foreground))",
        warning: "hsl(var(--warning))",
        success: "hsl(var(--success))",
        info: "hsl(var(--info))",
        accent: "hsl(var(--accent))",
        "accent-foreground": "hsl(var(--accent-foreground))",
        // Legacy aliases kept so no TSX needs changing
        "pixel-border": "hsl(var(--border-strong))",
        "focus-ring": "hsl(var(--focus))",
      },
      borderRadius: {
        sm: "4px",
        DEFAULT: "6px",
        md: "8px",
        lg: "12px",
        xl: "16px",
        "2xl": "20px",
        "3xl": "28px",
      },
      boxShadow: {
        sm: "0 1px 2px hsl(var(--shadow-color)/0.4), 0 0 0 1px hsl(var(--border)/0.5)",
        DEFAULT:
          "0 4px 16px hsl(var(--shadow-color)/0.5), 0 1px 3px hsl(var(--shadow-color)/0.3)",
        md: "0 8px 24px hsl(var(--shadow-color)/0.55), 0 2px 6px hsl(var(--shadow-color)/0.3)",
        lg: "0 16px 48px hsl(var(--shadow-color)/0.6), 0 4px 12px hsl(var(--shadow-color)/0.35)",
        xl: "0 24px 72px hsl(var(--shadow-color)/0.65), 0 8px 24px hsl(var(--shadow-color)/0.4)",
        glow: "0 0 24px hsl(var(--primary)/0.25), 0 0 8px hsl(var(--primary)/0.15)",
        "glow-sm": "0 0 12px hsl(var(--primary)/0.2), 0 0 4px hsl(var(--primary)/0.1)",
        "glow-accent": "0 0 20px hsl(var(--accent)/0.25), 0 0 6px hsl(var(--accent)/0.15)",
        soft: "0 2px 8px hsl(var(--shadow-color)/0.25)",
        inner: "inset 0 1px 3px hsl(var(--shadow-color)/0.2)",
        none: "none",
        // Neo-brutalist solid offsets
        brutal: "4px 4px 0px 0px rgba(0,0,0,1)",
        "brutal-sm": "2px 2px 0px 0px rgba(0,0,0,1)",
        "brutal-lg": "6px 6px 0px 0px rgba(0,0,0,1)",
        "brutal-white": "4px 4px 0px 0px rgba(255,255,255,1)",
        "brutal-white-sm": "2px 2px 0px 0px rgba(255,255,255,1)",
        "brutal-white-lg": "6px 6px 0px 0px rgba(255,255,255,1)",
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
      fontSize: {
        "2xs": ["0.625rem", { lineHeight: "1rem" }],
        xs: ["0.75rem", { lineHeight: "1.125rem" }],
      },
      animation: {
        "fade-in": "fade-in 220ms cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "fade-up": "fade-up 280ms cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "slide-in-right":
          "slide-in-right 320ms cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "slide-in-up":
          "slide-in-up 300ms cubic-bezier(0.16, 1, 0.3, 1) forwards",
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
          from: { opacity: "0", transform: "translateY(12px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "slide-in-right": {
          from: { opacity: "0", transform: "translateX(100%)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
        "slide-in-up": {
          from: { opacity: "0", transform: "translateY(100%)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "scale(0.92)" },
          to: { opacity: "1", transform: "scale(1)" },
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
