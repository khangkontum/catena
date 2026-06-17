import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  icon: Icon,
  trend,
  className,
}: {
  label: string;
  value: string | number;
  icon: LucideIcon;
  trend?: { value: string; positive?: boolean };
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col rounded-xl border border-border-strong bg-surface p-5 shadow-md",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-muted">{label}</p>
        <div className="rounded-md bg-raised p-2 text-subtle">
          <Icon className="size-4" />
        </div>
      </div>
      <p className="mt-2 font-serif text-3xl font-semibold text-ink">{value}</p>
      {trend ? (
        <p
          className={cn(
            "mt-1 text-xs font-semibold",
            trend.positive ? "text-success" : "text-muted",
          )}
        >
          {trend.value}
        </p>
      ) : null}
    </div>
  );
}
