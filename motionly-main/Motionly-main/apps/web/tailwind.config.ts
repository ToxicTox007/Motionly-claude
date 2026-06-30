import type { Config } from "tailwindcss";

const config: Config = {
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
        // Legacy aliases kept so no TSX needs changing
        "pixel-border": "hsl(var(--border-strong))",
        "focus-ring": "hsl(var(--focus))",
      },
      borderRadius: {
        sm: "4px",
        DEFAULT: "4px",
        md: "4px",
        lg: "6px",
        xl: "8px",
        "2xl": "8px",
        "3xl": "8px",
      },
      boxShadow: {
        sm: "2px 2px 0px 0px hsl(var(--border-strong))",
        DEFAULT: "4px 4px 0px 0px hsl(var(--border-strong))",
        md: "5px 5px 0px 0px hsl(var(--border-strong))",
        lg: "7px 7px 0px 0px hsl(var(--border-strong))",
        xl: "9px 9px 0px 0px hsl(var(--border-strong))",
        glow: "5px 5px 0px 0px hsl(var(--border-strong))",
        "glow-sm": "3px 3px 0px 0px hsl(var(--border-strong))",
        "glow-accent": "5px 5px 0px 0px hsl(var(--border-strong))",
        soft: "3px 3px 0px 0px hsl(var(--border-strong))",
        inner: "inset 0 1px 3px hsl(var(--shadow-color)/0.2)",
        none: "none",
        // Neo-brutalist solid offsets
        brutal: "4px 4px 0px 0px hsl(var(--border-strong))",
        "brutal-sm": "2px 2px 0px 0px hsl(var(--border-strong))",
        "brutal-lg": "6px 6px 0px 0px hsl(var(--border-strong))",
        "brutal-white": "4px 4px 0px 0px hsl(var(--border-strong))",
        "brutal-white-sm": "2px 2px 0px 0px hsl(var(--border-strong))",
        "brutal-white-lg": "6px 6px 0px 0px hsl(var(--border-strong))",
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
