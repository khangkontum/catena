import * as React from "react";

import { cn } from "@/lib/utils";

export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "min-h-24 w-full resize-y rounded-lg border border-border-strong bg-surface px-3 py-2.5 text-base leading-relaxed text-ink outline-none transition placeholder:text-subtle hover:border-border focus:border-accent focus:ring-[3px] focus:ring-accent/20 sm:text-sm",
        className,
      )}
      {...props}
    />
  );
}
