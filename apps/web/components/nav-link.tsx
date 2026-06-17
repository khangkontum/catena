"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export function NavLink({
  href,
  label,
  icon: Icon,
  exact = false,
  collapsed = false,
}: {
  href: string;
  label: string;
  icon: LucideIcon;
  exact?: boolean;
  collapsed?: boolean;
}) {
  const pathname = usePathname();
  const isActive = exact ? pathname === href : pathname.startsWith(href);

  return (
    <Link
      href={href}
      title={collapsed ? label : undefined}
      className={cn(
        "group flex items-center rounded-lg text-sm font-semibold transition",
        collapsed ? "justify-center px-0 py-2" : "gap-3 px-3 py-2",
        isActive
          ? "bg-accent-subtle text-accent ring-1 ring-accent/30"
          : "text-muted hover:bg-raised hover:text-ink",
      )}
    >
      <Icon
        className={cn(
          "size-4 shrink-0 transition",
          isActive ? "text-accent" : "text-muted group-hover:text-ink",
        )}
      />
      {collapsed ? null : <span>{label}</span>}
    </Link>
  );
}
