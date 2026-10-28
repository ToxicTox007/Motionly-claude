import Link, { type LinkProps } from "next/link";
import type { AnchorHTMLAttributes, ComponentPropsWithoutRef } from "react";
import { cn } from "@/lib/utils";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "accent";
type ButtonSize = "sm" | "md" | "lg" | "icon";

const baseButtonClasses =
  "inline-flex items-center justify-center gap-2 rounded-neu font-semibold transition-all duration-[140ms] ease-spring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-neu-raised-sm aria-disabled:cursor-not-allowed aria-disabled:opacity-50 aria-disabled:shadow-neu-raised-sm focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-background";

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "bg-primary text-primary-foreground shadow-neu-glow hover:brightness-105 active:shadow-neu-pressed active:brightness-100",
  secondary:
    "bg-surface text-foreground shadow-neu-raised hover:shadow-neu-raised-lg active:shadow-neu-pressed",
  ghost:
    "bg-transparent text-muted-foreground shadow-none hover:bg-surface hover:text-foreground hover:shadow-neu-raised-sm active:shadow-neu-pressed",
  danger:
    "bg-danger text-danger-foreground shadow-neu-raised hover:brightness-105 active:shadow-neu-pressed active:brightness-100",
  accent:
    "bg-accent text-accent-foreground shadow-neu-raised hover:brightness-105 active:shadow-neu-pressed active:brightness-100",
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: "min-h-10 px-4 text-xs",
  md: "min-h-11 px-5 text-sm",
  lg: "min-h-12 px-7 text-base",
  icon: "size-11 p-0 text-sm",
};

type ButtonProps = ComponentPropsWithoutRef<"button"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
};

export function Button({
  className,
  variant = "primary",
  size = "md",
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        baseButtonClasses,
        sizeClasses[size],
        variantClasses[variant],
        className,
      )}
      {...props}
    />
  );
}

type ButtonLinkProps = LinkProps &
  Omit<AnchorHTMLAttributes<HTMLAnchorElement>, keyof LinkProps> & {
    variant?: ButtonVariant;
    size?: ButtonSize;
  };

export function ButtonLink({
  className,
  variant = "primary",
  size = "md",
  ...props
}: ButtonLinkProps) {
  return (
    <Link
      className={cn(
        baseButtonClasses,
        sizeClasses[size],
        variantClasses[variant],
        className,
      )}
      {...props}
    />
  );
}

type IconButtonProps = ButtonProps & {
  label: string;
};

export function IconButton({
  label,
  children,
  size = "icon",
  ...props
}: IconButtonProps) {
  return (
    <Button aria-label={label} title={label} size={size} {...props}>
      {children}
      <span className="sr-only">{label}</span>
    </Button>
  );
}
