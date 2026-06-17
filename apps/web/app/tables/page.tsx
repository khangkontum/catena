"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { ArrowRight, Plus, Table2, X, Filter, FileText } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const tableSchema = z.object({
  name: z.string().min(1, "Name is required"),
  description: z.string().optional(),
});
type TableForm = z.infer<typeof tableSchema>;

const filterSchema = z.object({
  name: z.string().min(1, "Name is required"),
  tag: z.string().optional(),
  year_min: z.string().optional(),
  citations_min: z.string().optional(),
});
type FilterForm = z.infer<typeof filterSchema>;

function optionalNumber(value: string | undefined) {
  return value ? Number.parseInt(value, 10) : null;
}

type DrawerTab = "blank" | "filter";

export default function TablesPage() {
  const queryClient = useQueryClient();
  const tables = useQuery({ queryKey: ["tables"], queryFn: api.tables });
  const createForm = useForm<TableForm>({ resolver: zodResolver(tableSchema) });
  const filterForm = useForm<FilterForm>({ resolver: zodResolver(filterSchema) });
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [tab, setTab] = React.useState<DrawerTab>("blank");

  const createTable = useMutation({
    mutationFn: api.createTable,
    onSuccess: async () => {
      createForm.reset();
      toast.success("Table created");
      setDrawerOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["tables"] });
    },
    onError: (error) => toast.error(error.message),
  });

  const createFilteredTable = useMutation({
    mutationFn: api.createFilteredTable,
    onSuccess: async () => {
      filterForm.reset();
      toast.success("Filtered table created");
      setDrawerOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["tables"] });
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tables"
        subtitle="Extraction views over your paper library. Adding papers never reparses or re-embeds."
        actions={
          <Button size="sm" onClick={() => setDrawerOpen(true)}>
            <Plus className="size-4" /> New table
          </Button>
        }
      />

      {(tables.data ?? []).length ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(tables.data ?? []).map((table) => (
            <Link key={table.id} href={`/tables/${table.id}`}>
              <Card className="group h-full p-5 transition hover:border-accent/40 hover:shadow-lg">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-raised text-muted ring-1 ring-border group-hover:bg-accent-subtle group-hover:text-accent">
                    <Table2 className="size-5" />
                  </div>
                  {table.source_filter_json ? (
                    <Badge variant="accent">filtered</Badge>
                  ) : null}
                </div>
                <h3 className="mt-3 font-serif text-lg font-semibold text-ink">{table.name}</h3>
                <p className="mt-1 line-clamp-2 text-sm text-muted">
                  {table.description || "No description"}
                </p>
                <div className="mt-4 flex items-center justify-between">
                  <span className="font-mono text-xs text-subtle">#{table.id}</span>
                  <ArrowRight className="size-4 text-subtle transition group-hover:translate-x-0.5 group-hover:text-ink" />
                </div>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={Table2}
          title="No tables yet"
          description="Create a table to start building extraction columns from your papers."
        >
          <Button size="sm" onClick={() => setDrawerOpen(true)}>
            <Plus className="size-4" /> Create your first table
          </Button>
        </EmptyState>
      )}

      {/* Drawer */}
      {drawerOpen ? (
        <TableDrawer onClose={() => setDrawerOpen(false)} tab={tab} onTabChange={setTab}>
          {tab === "blank" ? (
            <form
              className="space-y-4"
              onSubmit={createForm.handleSubmit((values) => createTable.mutate(values))}
            >
              <FormField label="Name" hint="A short label for this table">
                <Input placeholder="Screening table" {...createForm.register("name")} />
              </FormField>
              <FormField label="Description" hint="Optional, for context">
                <Input placeholder="Papers from 2024 onwards" {...createForm.register("description")} />
              </FormField>
              <div className="flex gap-2 pt-2">
                <Button disabled={createTable.isPending} type="submit" className="flex-1">
                  {createTable.isPending ? "Creating..." : "Create table"}
                </Button>
                <Button type="button" variant="outline" onClick={() => setDrawerOpen(false)}>
                  Cancel
                </Button>
              </div>
            </form>
          ) : (
            <form
              className="space-y-4"
              onSubmit={filterForm.handleSubmit((values) =>
                createFilteredTable.mutate({
                  name: values.name,
                  paper_filter: {
                    tags_all: values.tag ? [values.tag] : [],
                    year_min: optionalNumber(values.year_min),
                    citations_min: optionalNumber(values.citations_min),
                    sort_by: "created",
                  },
                }),
              )}
            >
              <FormField label="Name" hint="A short label for this filtered table">
                <Input placeholder="Recent HFT papers" {...filterForm.register("name")} />
              </FormField>
              <FormField label="Required tag" hint="Only include papers with this tag">
                <Input placeholder="hft" {...filterForm.register("tag")} />
              </FormField>
              <div className="grid grid-cols-2 gap-3">
                <FormField label="Min year">
                  <Input type="number" placeholder="2020" {...filterForm.register("year_min")} />
                </FormField>
                <FormField label="Min citations">
                  <Input type="number" placeholder="10" {...filterForm.register("citations_min")} />
                </FormField>
              </div>
              <div className="rounded-lg bg-raised p-3 text-xs text-muted">
                Papers are selected from your global library without reprocessing.
              </div>
              <div className="flex gap-2 pt-2">
                <Button disabled={createFilteredTable.isPending} type="submit" className="flex-1">
                  {createFilteredTable.isPending ? "Creating..." : "Create filtered table"}
                </Button>
                <Button type="button" variant="outline" onClick={() => setDrawerOpen(false)}>
                  Cancel
                </Button>
              </div>
            </form>
          )}
        </TableDrawer>
      ) : null}
    </div>
  );
}

function TableDrawer({
  onClose,
  tab,
  onTabChange,
  children,
}: {
  onClose: () => void;
  tab: DrawerTab;
  onTabChange: (tab: DrawerTab) => void;
  children: React.ReactNode;
}) {
  return (
    <>
      <div className="fixed inset-0 z-50 bg-ink/20 backdrop-blur-[2px]" onClick={onClose} />
      <div className="fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-border-strong bg-surface shadow-2xl sm:w-[440px]">
        <div className="flex items-center justify-between border-b border-border-strong px-5 py-4">
          <h2 className="font-serif text-lg font-semibold text-ink">New table</h2>
          <button
            onClick={onClose}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-ink sm:h-8 sm:w-8"
          >
            <X className="size-4" />
          </button>
        </div>

        {/* Tab switcher */}
        <div className="flex gap-1 border-b border-border-strong p-2">
          <TabButton active={tab === "blank"} onClick={() => onTabChange("blank")} icon={FileText} label="Blank" />
          <TabButton active={tab === "filter"} onClick={() => onTabChange("filter")} icon={Filter} label="From filter" />
        </div>

        <div className="flex-1 overflow-y-auto p-5">{children}</div>
      </div>
    </>
  );
}

function TabButton({
  active,
  onClick,
  icon: Icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof FileText;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex flex-1 items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition",
        active ? "bg-accent text-accent-text shadow-md" : "text-muted hover:bg-raised hover:text-ink",
      )}
    >
      <Icon className="size-4" /> {label}
    </button>
  );
}

function FormField({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="text-sm font-semibold text-ink">{label}</label>
      {children}
      {hint ? <p className="text-xs text-subtle">{hint}</p> : null}
    </div>
  );
}
