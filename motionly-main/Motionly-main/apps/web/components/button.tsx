import Link, { type LinkProps } from "next/link";
import type { AnchorHTMLAttributes, ComponentPropsWithoutRef } from "react";
import { cn } from "@/lib/utils";

type ButtonProps = ComponentPropsWithoutRef<"button"> & {
  variant?: "primary" | "secondary" | "ghost";
};

export function Button({
  className,
  variant = "primary",
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        "pixel-button inline-flex h-10 items-center justify-center px-4 text-sm font-bold transition focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 focus:ring-offset-background disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0",
        variant === "primary" &&
          "bg-primary text-primary-foreground hover:brightness-105",
        variant === "secondary" &&
          "bg-surface text-foreground hover:border-primary/50 hover:bg-muted",
        variant === "ghost" &&
          "border-transparent bg-transparent text-muted-foreground shadow-none hover:bg-muted hover:text-foreground",
        className,
      )}
      {...props}
    />
  );
}

type ButtonLinkProps = LinkProps &
  Omit<AnchorHTMLAttributes<HTMLAnchorElement>, keyof LinkProps> & {
  variant?: "primary" | "secondary" | "ghost";
};

export function ButtonLink({
  className,
  variant = "primary",
  ...props
}: ButtonLinkProps) {
  return (
    <Link
      className={cn(
        "pixel-button inline-flex h-10 items-center justify-center px-4 text-sm font-bold transition focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 focus:ring-offset-background aria-disabled:cursor-not-allowed aria-disabled:opacity-60",
        variant === "primary" &&
          "bg-primary text-primary-foreground hover:brightness-105",
        variant === "secondary" &&
          "bg-surface text-foreground hover:border-primary/50 hover:bg-muted",
        variant === "ghost" &&
          "border-transparent bg-transparent text-muted-foreground shadow-none hover:bg-muted hover:text-foreground",
        className,
      )}
      {...props}
    />
  );
}
