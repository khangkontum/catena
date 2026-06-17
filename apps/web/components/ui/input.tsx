import * as React from "react";

import { cn } from "@/lib/utils";

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-11 w-full rounded-lg border border-border-strong bg-surface px-3 text-base text-ink outline-none transition placeholder:text-subtle hover:border-border focus:border-accent focus:ring-[3px] focus:ring-accent/20 sm:h-10 sm:text-sm",
        className,
      )}
      {...props}
    />
  );
}
