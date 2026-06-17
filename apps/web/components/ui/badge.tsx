import * as React from "react";

import { cn } from "@/lib/utils";

const badgeVariants = {
  default: "bg-raised text-ink ring-1 ring-border-strong",
  success: "bg-success-bg text-success ring-1 ring-success/30",
  warning: "bg-warning-bg text-warning ring-1 ring-warning/30",
  error: "bg-error-bg text-error ring-1 ring-error/30",
  accent: "bg-accent-subtle text-accent ring-1 ring-accent/30",
  info: "bg-info-bg text-info ring-1 ring-info/30",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: keyof typeof badgeVariants;
}

export function Badge({ className, variant = "default", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        badgeVariants[variant],
        className,
      )}
      {...props}
    />
  );
}

const statusMap: Record<string, keyof typeof badgeVariants> = {
  answered: "success",
  complete: "success",
  parsed: "success",
  indexed: "success",
  included: "success",
  queued: "warning",
  running: "warning",
  new: "warning",
  failed: "error",
  excluded: "error",
};

export function statusVariant(status: string): BadgeProps["variant"] {
  return statusMap[status] ?? "default";
}
