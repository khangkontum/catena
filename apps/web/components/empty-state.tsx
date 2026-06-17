import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export function EmptyState({
  icon: Icon,
  title,
  description,
  children,
  className,
}: {
  icon: LucideIcon;
  title: string;
  description?: string;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-xl border border-dashed border-border-strong bg-surface px-6 py-12 text-center",
        className,
      )}
    >
      <div className="mb-4 rounded-full bg-raised p-3.5 text-muted ring-1 ring-border">
        <Icon className="size-6" />
      </div>
      <h3 className="font-serif text-lg font-semibold text-ink">{title}</h3>
      {description ? <p className="mt-1 max-w-xs text-sm font-medium text-muted">{description}</p> : null}
      {children ? <div className="mt-5">{children}</div> : null}
    </div>
  );
}
