import Link from "next/link";
import { BookOpen, GitCompare, HelpCircle, Library, Tags } from "lucide-react";

const items = [
  { href: "/tables", label: "Tables", icon: Library },
  { href: "/papers", label: "Papers", icon: BookOpen },
  { href: "/tags", label: "Tags", icon: Tags },
  { href: "/ask", label: "Ask", icon: HelpCircle },
  { href: "/similarity", label: "Similarity", icon: GitCompare },
];

export function AppNav() {
  return (
    <aside className="hidden w-64 shrink-0 border-r border-slate-200 bg-white/80 p-5 md:block">
      <Link href="/" className="mb-8 block">
        <div className="text-xl font-bold tracking-tight text-slate-950">catena</div>
        <div className="text-xs text-slate-500">local extraction tables</div>
      </Link>
      <nav className="space-y-1">
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className="flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 hover:text-slate-950"
            >
              <Icon className="size-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
