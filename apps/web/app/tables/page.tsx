"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

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

export default function TablesPage() {
  const queryClient = useQueryClient();
  const tables = useQuery({ queryKey: ["tables"], queryFn: api.tables });
  const createForm = useForm<TableForm>({ resolver: zodResolver(tableSchema) });
  const filterForm = useForm<FilterForm>({ resolver: zodResolver(filterSchema) });

  const createTable = useMutation({
    mutationFn: api.createTable,
    onSuccess: async () => {
      createForm.reset();
      toast.success("Table created");
      await queryClient.invalidateQueries({ queryKey: ["tables"] });
    },
    onError: (error) => toast.error(error.message),
  });

  const createFilteredTable = useMutation({
    mutationFn: api.createFilteredTable,
    onSuccess: async () => {
      filterForm.reset();
      toast.success("Filtered table created");
      await queryClient.invalidateQueries({ queryKey: ["tables"] });
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Extraction tables</h1>
        <p className="mt-1 text-slate-600">
          Tables are reusable views over the global paper library. Adding a paper here never
          reparses or re-embeds it.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
        <Card>
          <CardHeader>
            <CardTitle>Tables</CardTitle>
            <CardDescription>Open a table to inspect the AG Grid extraction matrix.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="divide-y divide-slate-100">
              {(tables.data ?? []).map((table) => (
                <Link
                  key={table.id}
                  href={`/tables/${table.id}`}
                  className="flex items-center justify-between gap-4 py-4 hover:bg-slate-50"
                >
                  <div>
                    <div className="font-medium text-slate-950">{table.name}</div>
                    <div className="text-sm text-slate-500">{table.description || "No description"}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    {table.source_filter_json ? <Badge tone="blue">filtered</Badge> : null}
                    <span className="text-sm text-slate-400">#{table.id}</span>
                  </div>
                </Link>
              ))}
              {!tables.isLoading && !tables.data?.length ? (
                <div className="py-8 text-sm text-slate-500">No tables yet.</div>
              ) : null}
            </div>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Create table</CardTitle>
            </CardHeader>
            <CardContent>
              <form
                className="space-y-3"
                onSubmit={createForm.handleSubmit((values) => createTable.mutate(values))}
              >
                <Input placeholder="Screening table" {...createForm.register("name")} />
                <Input placeholder="Description" {...createForm.register("description")} />
                <Button disabled={createTable.isPending} type="submit" className="w-full">
                  Create
                </Button>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Create from filter</CardTitle>
              <CardDescription>Use tags/metadata without reprocessing papers.</CardDescription>
            </CardHeader>
            <CardContent>
              <form
                className="space-y-3"
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
                <Input placeholder="Recent HFT papers" {...filterForm.register("name")} />
                <Input placeholder="Required tag, e.g. hft" {...filterForm.register("tag")} />
                <Input type="number" placeholder="Min year" {...filterForm.register("year_min")} />
                <Input type="number" placeholder="Min citations" {...filterForm.register("citations_min")} />
                <Button disabled={createFilteredTable.isPending} type="submit" className="w-full">
                  Create filtered table
                </Button>
              </form>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function optionalNumber(value: string | undefined) {
  return value ? Number.parseInt(value, 10) : null;
}
