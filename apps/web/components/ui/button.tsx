import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-[10px] text-sm font-medium transition disabled:pointer-events-none disabled:opacity-50 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary:
          "bg-accent text-accent-text shadow-md hover:bg-accent-hover focus-visible:ring-2 focus-visible:ring-accent/40",
        secondary:
          "bg-raised text-ink hover:bg-border-strong focus-visible:ring-2 focus-visible:ring-accent/30",
        outline:
          "border border-border-strong bg-surface text-ink hover:bg-raised focus-visible:ring-2 focus-visible:ring-accent/30",
        ghost: "text-muted hover:bg-raised hover:text-ink",
        danger: "bg-error-bg text-error hover:bg-error hover:text-white",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 px-3 text-xs rounded-lg sm:h-8",
        lg: "h-11 px-5 text-base",
        icon: "h-10 w-10 rounded-lg sm:h-9 sm:w-9",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "className">,
    VariantProps<typeof buttonVariants> {
  className?: string;
  asChild?: boolean;
}

export function Button({ className, variant, size, asChild, children, ...props }: ButtonProps) {
  const classes = cn(buttonVariants({ variant, size, className }));

  if (asChild && React.isValidElement(children)) {
    const child = children as React.ReactElement<{ className?: string; children?: React.ReactNode }>;
    return React.cloneElement(child, {
      ...props,
      className: cn(classes, child.props.className),
      children: child.props.children,
    });
  }

  return (
    <button className={classes} {...props}>
      {children}
    </button>
  );
}
