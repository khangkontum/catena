import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const iconButtonVariants = cva(
  "inline-flex items-center justify-center rounded-lg transition disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-surface text-ink ring-1 ring-border-strong hover:bg-raised shadow-sm",
        primary: "bg-accent text-accent-text hover:bg-accent-hover shadow-md",
        ghost: "text-muted hover:bg-raised hover:text-ink",
        danger: "bg-error-bg text-error hover:bg-error hover:text-white shadow-sm",
      },
      size: {
        default: "h-10 w-10 sm:h-9 sm:w-9",
        sm: "h-9 w-9 sm:h-7 sm:w-7",
        lg: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface IconButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof iconButtonVariants> {}

export function IconButton({ className, variant, size, ...props }: IconButtonProps) {
  return <button className={cn(iconButtonVariants({ variant, size, className }))} {...props} />;
}
