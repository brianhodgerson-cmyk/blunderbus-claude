import * as React from "react";
import { cn } from "@/lib/utils";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: "default" | "outline" | "secondary";
}

export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  const variantStyles = {
    default: "bg-slate-900 text-slate-50 dark:bg-slate-50 dark:text-slate-900",
    outline: "border border-slate-200 text-slate-700 dark:border-slate-700 dark:text-slate-300",
    secondary: "bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-slate-100",
  }[variant];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium",
        variantStyles,
        className
      )}
      {...props}
    />
  );
}
