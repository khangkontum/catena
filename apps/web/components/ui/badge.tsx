import * as React from "react";

import { cn } from "@/lib/utils";

const tones = {
  slate: "bg-slate-100 text-slate-700",
  green: "bg-emerald-100 text-emerald-700",
  amber: "bg-amber-100 text-amber-800",
  red: "bg-red-100 text-red-700",
  blue: "bg-blue-100 text-blue-700",
};

export function Badge({
  className,
  tone = "slate",
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: keyof typeof tones }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}

export function statusTone(status: string): keyof typeof tones {
  if (["answered", "complete", "parsed", "indexed", "included"].includes(status)) {
    return "green";
  }
  if (["queued", "running", "new"].includes(status)) {
    return "amber";
  }
  if (["failed", "excluded"].includes(status)) {
    return "red";
  }
  return "slate";
}
