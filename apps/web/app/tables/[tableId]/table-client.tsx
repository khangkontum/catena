"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule,
  ModuleRegistry,
  type ColDef,
  type ICellRendererParams,
} from "ag-grid-community";
import { Play, RefreshCw } from "lucide-react";

import { Badge, statusTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, type MatrixOut } from "@/lib/api";
import { compactNumber } from "@/lib/utils";

ModuleRegistry.registerModules([AllCommunityModule]);

const columnSchema = z.object({
  name: z.string().min(1, "Name is required"),
  prompt: z.string().min(1, "Prompt is required"),
  retrieval_query: z.string().optional(),
  top_k: z.string().optional(),
  run: z.boolean().optional(),
});

type ColumnForm = z.infer<typeof columnSchema>;

const attachSchema = z.object({ paper_id: z.string().min(1, "Paper ID is required") });
type AttachForm = z.infer<typeof attachSchema>;

interface GridRow {
  id: number;
  title: string;
  year: number | null | undefined;
  citations: number | null | undefined;
  venue: string;
  tags: string;
  parse_status: string;
  index_status: string;
  [key: `cell_${number}`]: string | number | null | undefined;
}

export function TableClient({ tableId }: { tableId: number }) {
  const queryClient = useQueryClient();
  const matrix = useQuery({
    queryKey: ["matrix", tableId],
    queryFn: () => api.matrix(tableId),
    refetchInterval: 5_000,
  });
  const columnForm = useForm<ColumnForm>({ resolver: zodResolver(columnSchema) });
  const attachForm = useForm<AttachForm>({ resolver: zodResolver(attachSchema) });

  const addColumn = useMutation({
    mutationFn: api.createColumn,
    onSuccess: async () => {
      columnForm.reset();
      toast.success("Column created");
      await queryClient.invalidateQueries({ queryKey: ["matrix", tableId] });
    },
    onError: (error) => toast.error(error.message),
  });

  const runPending = useMutation({
    mutationFn: () => api.run({ table_id: tableId, retry_failed: false }),
    onSuccess: async (cells) => {
      toast.success(`Ran ${cells.length} cell${cells.length === 1 ? "" : "s"}`);
      await queryClient.invalidateQueries({ queryKey: ["matrix", tableId] });
    },
    onError: (error) => toast.error(error.message),
  });

  const refresh = useMutation({
    mutationFn: () => api.refreshTable(tableId, false),
    onSuccess: async () => {
      toast.success("Table refreshed");
      await queryClient.invalidateQueries({ queryKey: ["matrix", tableId] });
    },
    onError: (error) => toast.error(error.message),
  });

  const attach = useMutation({
    mutationFn: (paperId: number) => api.attachPaperToTable(tableId, paperId),
    onSuccess: async () => {
      attachForm.reset();
      toast.success("Paper attached");
      await queryClient.invalidateQueries({ queryKey: ["matrix", tableId] });
    },
    onError: (error) => toast.error(error.message),
  });

  const rowData = React.useMemo(() => buildRows(matrix.data), [matrix.data]);
  const columnDefs = React.useMemo(() => buildColumns(matrix.data), [matrix.data]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">
            {matrix.data?.table.name ?? `Table ${tableId}`}
          </h1>
          <p className="mt-1 text-slate-600">
            {matrix.data?.table.description || "Dynamic extraction matrix over selected papers."}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => runPending.mutate()} disabled={runPending.isPending}>
            <Play className="size-4" /> Run queued
          </Button>
          <Button
            variant="outline"
            onClick={() => refresh.mutate()}
            disabled={!matrix.data?.table.source_filter_json || refresh.isPending}
          >
            <RefreshCw className="size-4" /> Refresh filter
          </Button>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>Matrix</CardTitle>
            <CardDescription>
              {matrix.data?.rows.length ?? 0} papers · {matrix.data?.columns.length ?? 0} extraction columns
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="ag-theme-quartz h-[640px] w-full">
              <AgGridReact<GridRow>
                rowData={rowData}
                columnDefs={columnDefs}
                defaultColDef={{ sortable: true, filter: true, resizable: true }}
                rowSelection="multiple"
                animateRows
              />
            </div>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Add extraction column</CardTitle>
              <CardDescription>Creates queued cells for every paper in this table.</CardDescription>
            </CardHeader>
            <CardContent>
              <form
                className="space-y-3"
                onSubmit={columnForm.handleSubmit((values) =>
                  addColumn.mutate({
                    table_id: tableId,
                    name: values.name,
                    prompt: values.prompt,
                    retrieval_query: values.retrieval_query || null,
                    top_k: values.top_k ? Number.parseInt(values.top_k, 10) : null,
                    run: values.run,
                  }),
                )}
              >
                <Input placeholder="Core method" {...columnForm.register("name")} />
                <Textarea placeholder="What is the core method?" {...columnForm.register("prompt")} />
                <Input
                  placeholder="Optional retrieval query"
                  {...columnForm.register("retrieval_query")}
                />
                <Input type="number" placeholder="Top K" {...columnForm.register("top_k")} />
                <label className="flex items-center gap-2 text-sm text-slate-600">
                  <input type="checkbox" {...columnForm.register("run")} /> Run after creating
                </label>
                <Button disabled={addColumn.isPending} type="submit" className="w-full">
                  Add column
                </Button>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Attach existing paper</CardTitle>
              <CardDescription>No parsing, chunking, or embeddings are repeated.</CardDescription>
            </CardHeader>
            <CardContent>
              <form
                className="flex gap-2"
                onSubmit={attachForm.handleSubmit((values) =>
                  attach.mutate(Number.parseInt(values.paper_id, 10)),
                )}
              >
                <Input type="number" placeholder="Paper ID" {...attachForm.register("paper_id")} />
                <Button disabled={attach.isPending} type="submit">
                  Attach
                </Button>
              </form>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function buildRows(matrix: MatrixOut | undefined): GridRow[] {
  if (!matrix) {
    return [];
  }
  return matrix.rows.map((row) => {
    const gridRow: GridRow = {
      id: row.paper.id,
      title: row.paper.title,
      year: row.paper.year,
      citations: row.paper.citation_count,
      venue: row.paper.venue ?? "",
      tags: row.paper.tags.join(", "),
      parse_status: row.paper.parse_status,
      index_status: row.paper.index_status,
    };
    for (const column of matrix.columns) {
      const cell = row.cells[String(column.id)];
      gridRow[`cell_${column.id}`] = cell?.answer_text || cell?.error || cell?.status || "";
    }
    return gridRow;
  });
}

function buildColumns(matrix: MatrixOut | undefined): ColDef<GridRow>[] {
  const staticColumns: ColDef<GridRow>[] = [
    { field: "id", headerName: "ID", width: 90, pinned: "left" },
    { field: "title", headerName: "Paper", minWidth: 280, pinned: "left" },
    {
      field: "year",
      headerName: "Year",
      width: 100,
      valueFormatter: (params) => String(params.value ?? ""),
    },
    {
      field: "citations",
      headerName: "Cites",
      width: 110,
      valueFormatter: (params) => compactNumber(params.value),
    },
    { field: "venue", headerName: "Venue", minWidth: 160 },
    { field: "tags", headerName: "Tags", minWidth: 160 },
    { field: "parse_status", headerName: "Parse", width: 130, cellRenderer: StatusCell },
    { field: "index_status", headerName: "Index", width: 130, cellRenderer: StatusCell },
  ];
  const extractionColumns =
    matrix?.columns.map<ColDef<GridRow>>((column) => ({
      field: `cell_${column.id}`,
      headerName: column.name,
      minWidth: 220,
      tooltipValueGetter: () => column.prompt,
      wrapText: true,
      autoHeight: true,
    })) ?? [];
  return [...staticColumns, ...extractionColumns];
}

function StatusCell(params: ICellRendererParams<GridRow, string>) {
  const value = String(params.value ?? "");
  return value ? <Badge tone={statusTone(value)}>{value}</Badge> : null;
}
