"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  BookOpen,
  FileText,
  FolderOpen,
  MessageCircleQuestion,
  Table2,
  Tag,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StatCard } from "@/components/stat-card";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const quickActions = [
  { href: "/library", label: "Upload papers", icon: FolderOpen },
  { href: "/tables", label: "Open tables", icon: Table2 },
  { href: "/ask", label: "Ask a question", icon: MessageCircleQuestion },
  { href: "/discover", label: "Explore tags", icon: Tag },
];

export default function DashboardPage() {
  const papers = useQuery({ queryKey: ["papers"], queryFn: () => api.papers({ limit: 1 }), staleTime: 10_000 });
  const tables = useQuery({ queryKey: ["tables"], queryFn: api.tables, staleTime: 10_000 });
  const tags = useQuery({ queryKey: ["tags"], queryFn: api.tags, staleTime: 30_000 });

  const paperCount = papers.data?.length ?? 0;
  const tableCount = tables.data?.length ?? 0;
  const tagCount = tags.data?.length ?? 0;
  const parsedCount = papers.data?.filter((p) => p.parse_status === "parsed").length ?? 0;

  return (
    <div className="space-y-8">
      <section className="rounded-2xl border border-border bg-surface p-5 shadow-sm sm:p-8">
        <h1 className="max-w-3xl font-serif text-3xl font-medium leading-tight tracking-tight text-ink sm:text-4xl md:text-5xl">
          Welcome back to your local research library.
        </h1>
        <p className="mt-4 max-w-2xl text-base leading-relaxed text-muted">
          Upload papers, build extraction tables, and ask evidence-backed questions. Everything
          stays local: parsing, embeddings, and the model gateway live in Python.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Button asChild>
            <Link href="/library">
              Upload papers <ArrowRight className="size-4" />
            </Link>
          </Button>
          <Button variant="outline" asChild>
            <Link href="/tables">Open tables</Link>
          </Button>
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Papers" value={paperCount} icon={FileText} trend={{ value: `${parsedCount} parsed`, positive: true }} />
        <StatCard label="Tables" value={tableCount} icon={Table2} />
        <StatCard label="Tags" value={tagCount} icon={Tag} />
        <StatCard label="Library sources" value="Local" icon={BookOpen} />
      </section>

      <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>Recent tables</CardTitle>
            <CardDescription>Quickly jump back into your extraction work.</CardDescription>
          </CardHeader>
          <CardContent>
            {(tables.data ?? []).length ? (
              <div className="divide-y divide-border">
                {(tables.data ?? []).map((table) => (
                  <Link
                    key={table.id}
                    href={`/tables/${table.id}`}
                    className="group flex items-center justify-between gap-4 py-4 transition hover:opacity-80"
                  >
                    <div className="min-w-0">
                      <div className="font-medium text-ink">{table.name}</div>
                      <div className="truncate text-sm text-muted">
                        {table.description || "No description"}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {table.source_filter_json ? (
                        <Badge variant="accent">filtered</Badge>
                      ) : null}
                      <ArrowRight className="size-4 text-subtle transition group-hover:translate-x-0.5 group-hover:text-ink" />
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <p className="py-8 text-center text-sm text-muted">No tables yet.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Quick actions</CardTitle>
            <CardDescription>Most used workflows from here.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 gap-2">
              {quickActions.map((action) => {
                const Icon = action.icon;
                return (
                  <Link
                    key={action.href}
                    href={action.href}
                    className={cn(
                      "flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3 text-sm font-medium transition",
                      "hover:border-border-strong hover:bg-raised",
                    )}
                  >
                    <div className="flex h-8 w-8 items-center justify-center rounded-md bg-raised text-ink">
                      <Icon className="size-4" />
                    </div>
                    <span className="flex-1 text-ink">{action.label}</span>
                    <ArrowRight className="size-4 text-subtle" />
                  </Link>
                );
              })}
            </div>
          </CardContent>
        </Card>
      </div>

      <ActivityCard />
    </div>
  );
}

function ActivityCard() {
  const papers = useQuery({ queryKey: ["papers"], queryFn: () => api.papers({ limit: 20, sort_by: "created", descending: true }), staleTime: 10_000 });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent activity</CardTitle>
        <CardDescription>Latest papers added to your global library.</CardDescription>
      </CardHeader>
      <CardContent>
        {(papers.data ?? []).length ? (
          <ul className="space-y-4">
            {(papers.data ?? []).slice(0, 8).map((paper) => (
              <li key={paper.id} className="flex items-start gap-3 text-sm">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-raised text-muted">
                  <FileText className="size-3.5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-ink">{paper.title}</div>
                  <div className="text-xs text-muted">
                    {paper.year ? `${paper.year} · ` : null}
                    {paper.venue || "No venue"} ·{" "}
                    <span className={cn(paper.parse_status === "parsed" ? "text-success" : "text-muted")}>
                      {paper.parse_status}
                    </span>
                  </div>
                </div>
                <Badge variant={paper.parse_status === "parsed" ? "success" : "default"}>
                  #{paper.id}
                </Badge>
              </li>
            ))}
          </ul>
        ) : (
          <p className="py-8 text-center text-sm text-muted">
            Upload your first paper to see activity here.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
