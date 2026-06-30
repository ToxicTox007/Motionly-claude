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
        "rounded-md min-h-[44px] px-4 text-sm font-bold inline-flex items-center justify-center gap-2 transition-all duration-[100ms] focus:outline-none border-2 border-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[4px_4px_0px_0px_#000] dark:hover:shadow-[4px_4px_0px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none disabled:opacity-50 disabled:cursor-not-allowed disabled:transform-none disabled:shadow-none",
        variant === "primary" && "bg-primary text-primary-foreground",
        variant === "secondary" && "bg-surface text-foreground",
        variant === "ghost" &&
          "border-transparent bg-transparent text-muted-foreground hover:border-foreground hover:bg-surface-elevated hover:text-foreground",
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
        "rounded-md min-h-[44px] px-4 text-sm font-bold inline-flex items-center justify-center gap-2 transition-all duration-[100ms] focus:outline-none border-2 border-foreground hover:-translate-x-0.5 hover:-translate-y-0.5 hover:shadow-[4px_4px_0px_0px_#000] dark:hover:shadow-[4px_4px_0px_0px_#fff] active:translate-x-0 active:translate-y-0 active:shadow-none aria-disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:transform-none aria-disabled:shadow-none",
        variant === "primary" && "bg-primary text-primary-foreground",
        variant === "secondary" && "bg-surface text-foreground",
        variant === "ghost" &&
          "border-transparent bg-transparent text-muted-foreground hover:border-foreground hover:bg-surface-elevated hover:text-foreground",
        className,
      )}
      {...props}
    />
  );
}
