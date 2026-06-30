import Link, { type LinkProps } from "next/link";
import type { AnchorHTMLAttributes, ComponentPropsWithoutRef } from "react";
import { cn } from "@/lib/utils";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "accent";
type ButtonSize = "sm" | "md" | "lg" | "icon";

const baseButtonClasses =
  "inline-flex items-center justify-center gap-2 rounded border-2 border-foreground font-black uppercase transition-all duration-[100ms] focus:outline-none disabled:cursor-not-allowed disabled:opacity-50 disabled:translate-x-0 disabled:translate-y-0 disabled:shadow-none aria-disabled:cursor-not-allowed aria-disabled:opacity-50 aria-disabled:translate-x-0 aria-disabled:translate-y-0 aria-disabled:shadow-none";

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "bg-primary text-primary-foreground shadow-brutal hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-brutal-lg active:translate-x-0 active:translate-y-0 active:shadow-none",
  secondary:
    "bg-surface text-foreground shadow-brutal-sm hover:-translate-x-0.5 hover:-translate-y-0.5 hover:bg-surface-elevated hover:shadow-brutal active:translate-x-0 active:translate-y-0 active:shadow-none",
  ghost:
    "border-transparent bg-transparent text-muted-foreground shadow-none hover:border-foreground hover:bg-surface-elevated hover:text-foreground hover:shadow-brutal-sm active:translate-x-0 active:translate-y-0 active:shadow-none",
  danger:
    "bg-danger text-danger-foreground shadow-brutal hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-brutal-lg active:translate-x-0 active:translate-y-0 active:shadow-none",
  accent:
    "bg-accent text-accent-foreground shadow-brutal hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-brutal-lg active:translate-x-0 active:translate-y-0 active:shadow-none",
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: "min-h-10 px-3 text-xs",
  md: "min-h-11 px-4 text-sm",
  lg: "min-h-12 px-6 text-base",
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
