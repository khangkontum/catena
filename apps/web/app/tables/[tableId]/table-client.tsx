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
import {
  Play,
  RefreshCw,
  Plus,
  Paperclip,
  Search,
  X,
  Columns3,
  Maximize2,
  Eye,
  EyeOff,
} from "lucide-react";

import { Badge, statusVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/page-header";
import { api, type CellOut, type ColumnOut, type MatrixOut } from "@/lib/api";
import { cn, compactNumber } from "@/lib/utils";

ModuleRegistry.registerModules([AllCommunityModule]);

// --- Cell detail modal event bus ---
// AG Grid cell renderers can't manage parent state, so we use a
// lightweight pub/sub to open a modal from any cell.
type CellDetail = {
  title: string;
  subtitle?: string;
  status: string;
  answer?: string | null;
  error?: string | null;
  confidence?: string | null;
  evidence?: Array<Record<string, unknown>> | null;
};

const cellModalListeners = new Set<(cell: CellDetail) => void>();

function openCellModal(cell: CellDetail) {
  cellModalListeners.forEach((fn) => fn(cell));
}

function onOpenCellModal(fn: (cell: CellDetail) => void) {
  cellModalListeners.add(fn);
  return () => {
    cellModalListeners.delete(fn);
  };
}

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
  [key: `cell_${number}`]: CellOut | string | null;
}

type DrawerKind = "column" | "attach" | null;

export function TableClient({ tableId }: { tableId: number }) {
  const queryClient = useQueryClient();
  const matrix = useQuery({
    queryKey: ["matrix", tableId],
    queryFn: () => api.matrix(tableId),
    refetchInterval: 5_000,
  });
  const columnForm = useForm<ColumnForm>({ resolver: zodResolver(columnSchema) });
  const attachForm = useForm<AttachForm>({ resolver: zodResolver(attachSchema) });

  const [drawer, setDrawer] = React.useState<DrawerKind>(null);
  const [quickFilter, setQuickFilter] = React.useState("");
  const hiddenColumnSnapshot = React.useSyncExternalStore(
    React.useCallback(
      (onStoreChange) => subscribeHiddenColumnIds(tableId, onStoreChange),
      [tableId],
    ),
    React.useCallback(() => readHiddenColumnIds(tableId).join(","), [tableId]),
    () => "",
  );
  const hiddenColumnIds = React.useMemo(
    () => parseHiddenColumnIds(hiddenColumnSnapshot),
    [hiddenColumnSnapshot],
  );

  const updateHiddenColumnIds = React.useCallback(
    (updater: (current: number[]) => number[]) => {
      const next = updater(readHiddenColumnIds(tableId));
      writeHiddenColumnIds(tableId, next);
    },
    [tableId],
  );

  const hideColumn = React.useCallback(
    (columnId: number) => {
      updateHiddenColumnIds((current) => (current.includes(columnId) ? current : [...current, columnId]));
    },
    [updateHiddenColumnIds],
  );

  const showColumn = React.useCallback(
    (columnId: number) => {
      updateHiddenColumnIds((current) => current.filter((id) => id !== columnId));
    },
    [updateHiddenColumnIds],
  );

  // Cell detail modal
  const [cellModal, setCellModal] = React.useState<CellDetail | null>(null);
  React.useEffect(() => {
    return onOpenCellModal(setCellModal);
  }, []);

  const addColumn = useMutation({
    mutationFn: api.createColumn,
    onSuccess: async () => {
      columnForm.reset();
      toast.success("Column created");
      setDrawer(null);
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
      setDrawer(null);
      toast.success("Paper attached");
      await queryClient.invalidateQueries({ queryKey: ["matrix", tableId] });
    },
    onError: (error) => toast.error(error.message),
  });

  const hiddenColumnIdSet = React.useMemo(() => new Set(hiddenColumnIds), [hiddenColumnIds]);
  const allExtractionColumns = React.useMemo(() => matrix.data?.columns ?? [], [matrix.data?.columns]);
  const visibleExtractionColumns = React.useMemo(
    () => allExtractionColumns.filter((col) => !hiddenColumnIdSet.has(col.id)),
    [allExtractionColumns, hiddenColumnIdSet],
  );
  const hiddenExtractionColumns = React.useMemo(
    () => allExtractionColumns.filter((col) => hiddenColumnIdSet.has(col.id)),
    [allExtractionColumns, hiddenColumnIdSet],
  );
  const rowData = React.useMemo(
    () => buildRows(matrix.data, visibleExtractionColumns),
    [matrix.data, visibleExtractionColumns],
  );
  const columnDefs = React.useMemo(() => buildColumns(visibleExtractionColumns), [visibleExtractionColumns]);
  const rowCount = matrix.data?.rows.length ?? 0;
  const colCount = matrix.data?.columns.length ?? 0;

  const pendingCount = React.useMemo(() => {
    if (!matrix.data) return 0;
    return matrix.data.rows.reduce((acc, row) => {
      return (
        acc +
        matrix.data!.columns.filter((col) => {
          const cell = row.cells[String(col.id)];
          return cell && (cell.status === "queued" || cell.status === "running");
        }).length
      );
    }, 0);
  }, [matrix.data]);

  const answeredCount = React.useMemo(() => {
    if (!matrix.data) return 0;
    return matrix.data.rows.reduce((acc, row) => {
      return (
        acc +
        matrix.data!.columns.filter((col) => {
          const cell = row.cells[String(col.id)];
          return cell && cell.status === "answered";
        }).length
      );
    }, 0);
  }, [matrix.data]);

  const totalCells = rowCount * colCount;
  const progressPct = totalCells > 0 ? Math.round((answeredCount / totalCells) * 100) : 0;

  const gridRef = React.useRef<AgGridReact<GridRow>>(null);

  return (
    <div className="space-y-6">
      {/* Header */}
      <PageHeader
        title={matrix.data?.table.name ?? `Table ${tableId}`}
        subtitle={matrix.data?.table.description || "Dynamic extraction matrix over selected papers."}
        actions={
          <>
            <Button size="sm" variant="outline" onClick={() => setDrawer("attach")}>
              <Paperclip className="size-4" /> Attach
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => refresh.mutate()}
              disabled={!matrix.data?.table.source_filter_json || refresh.isPending}
            >
              <RefreshCw className="size-4" /> Refresh
            </Button>
            <Button size="sm" onClick={() => runPending.mutate()} disabled={runPending.isPending}>
              <Play className="size-4" /> Run queued
              {pendingCount > 0 ? (
                <span className="ml-1 rounded-full bg-accent-text/20 px-1.5 py-0.5 text-[10px] font-bold text-accent-text">
                  {pendingCount}
                </span>
              ) : null}
            </Button>
          </>
        }
      />

      {/* Stat bar */}
      <div className="flex flex-wrap items-center gap-4">
        <StatChip label="Papers" value={rowCount} />
        <StatChip label="Columns" value={colCount} />
        <StatChip label="Pending" value={pendingCount} highlight={pendingCount > 0} />
        <StatChip label="Answered" value={answeredCount} />
        <div className="flex min-w-[200px] flex-1 items-center gap-3">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-raised ring-1 ring-border">
            <div
              className="h-full rounded-full bg-accent transition-all duration-500"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <span className="text-xs font-semibold text-muted">{progressPct}%</span>
        </div>
      </div>

      {/* Column chips */}
      {matrix.data?.columns.length ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
            <Columns3 className="size-3.5" /> Columns
          </span>
          {visibleExtractionColumns.map((col) => (
            <ColumnChip key={col.id} column={col} onHide={() => hideColumn(col.id)} />
          ))}
          <Button size="sm" variant="ghost" onClick={() => setDrawer("column")}>
            <Plus className="size-3.5" /> Add
          </Button>
          {hiddenExtractionColumns.length ? (
            <>
              <span className="ml-2 text-xs font-semibold uppercase tracking-wide text-muted">
                Hidden ({hiddenExtractionColumns.length})
              </span>
              {hiddenExtractionColumns.map((col) => (
                <HiddenColumnChip key={col.id} column={col} onShow={() => showColumn(col.id)} />
              ))}
            </>
          ) : null}
        </div>
      ) : (
        <div className="flex items-center justify-between rounded-xl border border-dashed border-border-strong bg-surface px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-raised text-muted">
              <Columns3 className="size-4" />
            </div>
            <div>
              <p className="text-sm font-semibold text-ink">No extraction columns yet</p>
              <p className="text-xs text-muted">Add a column to start extracting data from papers.</p>
            </div>
          </div>
          <Button size="sm" onClick={() => setDrawer("column")}>
            <Plus className="size-4" /> Add column
          </Button>
        </div>
      )}

      {/* Grid with toolbar */}
      <div className="overflow-hidden rounded-xl border border-border-strong bg-surface shadow-md">
        <div className="flex items-center gap-3 border-b border-border-strong bg-background px-4 py-2.5">
          <Search className="size-4 shrink-0 text-muted" />
          <input
            type="text"
            placeholder="Search in table..."
            value={quickFilter}
            onChange={(e) => {
              setQuickFilter(e.target.value);
              gridRef.current?.api?.setGridOption("quickFilterText", e.target.value);
            }}
            className="h-7 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-subtle"
          />
          {quickFilter ? (
            <button
              onClick={() => {
                setQuickFilter("");
                gridRef.current?.api?.setGridOption("quickFilterText", "");
              }}
              className="rounded p-1 text-muted hover:bg-raised hover:text-ink"
            >
              <X className="size-3.5" />
            </button>
          ) : null}
        </div>
        <div className="ag-theme-quartz h-[460px] w-full md:h-[600px]" style={{ borderBottom: "none" }}>
          <AgGridReact<GridRow>
            ref={gridRef}
            rowData={rowData}
            columnDefs={columnDefs}
            defaultColDef={{ sortable: true, filter: true, resizable: true }}
            rowSelection={{ mode: "multiRow" }}
            theme="legacy"
            animateRows
          />
        </div>
      </div>

      {/* Drawer */}
      {drawer ? (
        <Drawer onClose={() => setDrawer(null)} title={drawer === "column" ? "Add extraction column" : "Attach paper"}>
          {drawer === "column" ? (
            <form
              className="space-y-4"
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
              <FormField label="Name" hint="Short label for this column">
                <Input placeholder="Core method" {...columnForm.register("name")} />
              </FormField>
              <FormField label="Prompt" hint="What should be extracted from each paper?">
                <Textarea placeholder="What is the core method?" {...columnForm.register("prompt")} />
              </FormField>
              <FormField label="Retrieval query" hint="Optional, for RAG-based extraction">
                <Input placeholder="Leave empty for full-paper extraction" {...columnForm.register("retrieval_query")} />
              </FormField>
              <FormField label="Top K" hint="Number of chunks to retrieve">
                <Input type="number" placeholder="5" {...columnForm.register("top_k")} />
              </FormField>
              <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-muted">
                <input type="checkbox" {...columnForm.register("run")} className="rounded border-border-strong text-accent" />
                Run immediately after creating
              </label>
              <div className="flex gap-2 pt-2">
                <Button disabled={addColumn.isPending} type="submit" className="flex-1">
                  {addColumn.isPending ? "Creating..." : "Create column"}
                </Button>
                <Button type="button" variant="outline" onClick={() => setDrawer(null)}>
                  Cancel
                </Button>
              </div>
            </form>
          ) : (
            <form
              className="space-y-4"
              onSubmit={attachForm.handleSubmit((values) =>
                attach.mutate(Number.parseInt(values.paper_id, 10)),
              )}
            >
              <FormField label="Paper ID" hint="Enter the ID of an existing paper from your library">
                <Input type="number" placeholder="42" {...attachForm.register("paper_id")} />
              </FormField>
              <div className="rounded-lg bg-raised p-3 text-xs text-muted">
                The paper will be added to this table. No re-parsing or re-embedding will occur.
              </div>
              <div className="flex gap-2 pt-2">
                <Button disabled={attach.isPending} type="submit" className="flex-1">
                  {attach.isPending ? "Attaching..." : "Attach paper"}
                </Button>
                <Button type="button" variant="outline" onClick={() => setDrawer(null)}>
                  Cancel
                </Button>
              </div>
            </form>
          )}
        </Drawer>
      ) : null}

      {/* Cell detail modal */}
      {cellModal ? <CellModal detail={cellModal} onClose={() => setCellModal(null)} /> : null}
    </div>
  );
}

function StatChip({ label, value, highlight }: { label: string; value: number; highlight?: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border-strong bg-surface px-3 py-1.5">
      <span className="text-xs font-semibold text-muted">{label}</span>
      <span className={cn("font-serif text-lg font-bold", highlight ? "text-accent" : "text-ink")}>
        {value}
      </span>
    </div>
  );
}

function ColumnChip({ column, onHide }: { column: ColumnOut; onHide: () => void }) {
  return (
    <div
      className="group flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-ink transition hover:border-border-strong hover:bg-raised"
      title={column.prompt}
    >
      {column.name}
      <span className="text-subtle">#{column.id}</span>
      <button
        type="button"
        onClick={onHide}
        className="rounded p-0.5 text-subtle opacity-0 transition hover:bg-surface hover:text-ink group-hover:opacity-100"
        title={`Hide ${column.name}`}
      >
        <EyeOff className="size-3.5" />
      </button>
    </div>
  );
}

function HiddenColumnChip({ column, onShow }: { column: ColumnOut; onShow: () => void }) {
  return (
    <div
      className="group flex items-center gap-2 rounded-lg border border-dashed border-border bg-raised px-3 py-1.5 text-xs font-medium text-muted transition hover:border-border-strong hover:text-ink"
      title={column.prompt}
    >
      {column.name}
      <span className="text-subtle">#{column.id}</span>
      <button
        type="button"
        onClick={onShow}
        className="rounded p-0.5 text-subtle transition hover:bg-surface hover:text-ink"
        title={`Show ${column.name}`}
      >
        <Eye className="size-3.5" />
      </button>
    </div>
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

function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <>
      <div className="fixed inset-0 z-50 bg-ink/20 backdrop-blur-[2px]" onClick={onClose} />
      <div className="fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-border-strong bg-surface shadow-2xl sm:w-[400px]">
        <div className="flex items-center justify-between border-b border-border-strong px-5 py-4">
          <h2 className="font-serif text-lg font-semibold text-ink">{title}</h2>
          <button
            onClick={onClose}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-ink sm:h-8 sm:w-8"
          >
            <X className="size-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
      </div>
    </>
  );
}

function CellModal({ detail, onClose }: { detail: CellDetail; onClose: () => void }) {
  return (
    <>
      <div className="fixed inset-0 z-50 bg-ink/20 backdrop-blur-[2px]" onClick={onClose} />
      <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4">
        <div className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-border-strong bg-surface shadow-2xl">
          <div className="flex items-start justify-between gap-4 border-b border-border-strong p-5">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h2 className="font-serif text-xl font-semibold text-ink">{detail.title}</h2>
                <Badge variant={statusVariant(detail.status)}>{detail.status}</Badge>
              </div>
              {detail.subtitle ? (
                <p className="mt-1 truncate text-sm font-medium text-muted">{detail.subtitle}</p>
              ) : null}
            </div>
            <button
              onClick={onClose}
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-ink sm:h-8 sm:w-8"
            >
              <X className="size-4" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-5">
            {detail.error ? (
              <div className="mb-4 rounded-lg border border-error/30 bg-error-bg p-4">
                <p className="text-sm font-semibold text-error">Error</p>
                <p className="mt-1 text-sm text-error">{detail.error}</p>
              </div>
            ) : null}
            {detail.confidence ? (
              <div className="mb-4">
                <Badge variant="accent">confidence: {detail.confidence}</Badge>
              </div>
            ) : null}
            {detail.answer ? (
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Answer</h3>
                <div className="rounded-lg bg-raised p-4 text-sm leading-relaxed text-ink whitespace-pre-wrap">
                  {detail.answer}
                </div>
              </div>
            ) : null}
            {detail.evidence?.length ? (
              <div className="mt-5">
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
                  Evidence ({detail.evidence.length})
                </h3>
                <div className="space-y-2">
                  {detail.evidence.map((item, index) => (
                    <div key={index} className="rounded-lg border border-border-strong bg-surface p-3 text-sm">
                      <div className="mb-1 text-xs font-medium text-muted">
                        paper {String(item.paper_id ?? "")}
                        {String(item.page ?? "") !== "undefined" ? ` · page ${item.page}` : ""}
                        {String(item.chunk_id ?? "") !== "undefined" ? ` · chunk ${item.chunk_id}` : ""}
                      </div>
                      <p className="text-ink">{String(item.quote ?? "")}</p>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </>
  );
}

function buildRows(matrix: MatrixOut | undefined, columns?: ColumnOut[]): GridRow[] {
  if (!matrix) return [];
  const extractionColumns = columns ?? matrix.columns;
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
    for (const column of extractionColumns) {
      const cell = row.cells[String(column.id)];
      gridRow[`cell_${column.id}`] = cell ?? null;
    }
    return gridRow;
  });
}

function buildColumns(columns: ColumnOut[]): ColDef<GridRow>[] {
  const staticColumns: ColDef<GridRow>[] = [
    { field: "title", headerName: "Paper", minWidth: 320, pinned: "left", cellRenderer: TitleCell },
    {
      field: "year",
      headerName: "Year",
      width: 90,
      filter: "agNumberColumnFilter",
      valueFormatter: (p) => String(p.value ?? ""),
    },
    {
      field: "citations",
      headerName: "Cites",
      width: 100,
      filter: "agNumberColumnFilter",
      valueFormatter: (p) => compactNumber(p.value),
    },
    { field: "venue", headerName: "Venue", minWidth: 140 },
    { field: "tags", headerName: "Tags", minWidth: 140 },
    { field: "parse_status", headerName: "Parse", width: 120, cellRenderer: StatusCell },
    { field: "index_status", headerName: "Index", width: 120, cellRenderer: StatusCell },
  ];
  const extractionColumns = columns.map<ColDef<GridRow>>((column) => ({
    field: `cell_${column.id}`,
    headerName: column.name,
    minWidth: 240,
    tooltipValueGetter: () => column.prompt,
    cellRenderer: ExtractionCell,
    wrapText: true,
    autoHeight: true,
  }));
  return [...staticColumns, ...extractionColumns];
}

function hiddenColumnsStorageKey(tableId: number) {
  return `catena.table.${tableId}.hiddenColumns`;
}

function hiddenColumnsChangeEvent(tableId: number) {
  return `catena:hidden-columns:${tableId}`;
}

function subscribeHiddenColumnIds(tableId: number, onStoreChange: () => void) {
  if (typeof window === "undefined") return () => {};

  const storageKey = hiddenColumnsStorageKey(tableId);
  const changeEvent = hiddenColumnsChangeEvent(tableId);
  const handleStorage = (event: StorageEvent) => {
    if (event.key === storageKey) onStoreChange();
  };

  window.addEventListener("storage", handleStorage);
  window.addEventListener(changeEvent, onStoreChange);
  return () => {
    window.removeEventListener("storage", handleStorage);
    window.removeEventListener(changeEvent, onStoreChange);
  };
}

function parseHiddenColumnIds(value: string): number[] {
  if (!value) return [];
  return value
    .split(",")
    .map((id) => Number.parseInt(id, 10))
    .filter((id) => Number.isInteger(id));
}

function readHiddenColumnIds(tableId: number): number[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(hiddenColumnsStorageKey(tableId));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const ids = parsed
      .map((id) => (typeof id === "number" ? id : Number.parseInt(String(id), 10)))
      .filter((id): id is number => Number.isInteger(id));
    return Array.from(new Set(ids));
  } catch {
    return [];
  }
}

function writeHiddenColumnIds(tableId: number, columnIds: number[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(hiddenColumnsStorageKey(tableId), JSON.stringify(columnIds));
    window.dispatchEvent(new Event(hiddenColumnsChangeEvent(tableId)));
  } catch {
    // Ignore storage failures so column toggles still work for the current session.
  }
}

function TitleCell(params: ICellRendererParams<GridRow, string>) {
  const id = params.data?.id;
  return (
    <div className="flex flex-col py-0.5">
      <span className="font-medium text-ink" title={params.value ?? ""}>
        {params.value}
      </span>
      <span className="font-mono text-[11px] text-subtle">#{id}</span>
    </div>
  );
}

function StatusCell(params: ICellRendererParams<GridRow, string>) {
  const value = String(params.value ?? "");
  return value ? <Badge variant={statusVariant(value)}>{value}</Badge> : null;
}

function ExtractionCell(params: ICellRendererParams<GridRow, CellOut | string | null>) {
  const cell = params.value;
  if (!cell || typeof cell === "string") {
    return <span className="text-subtle">{cell || "—"}</span>;
  }

  const c = cell as CellOut;
  const cellTitle = params.colDef?.headerName ?? "Cell";
  const cellSubtitle = params.data?.title ?? `Paper #${params.data?.id ?? ""}`;

  const handleOpenModal = () => {
    openCellModal({
      title: cellTitle,
      subtitle: cellSubtitle,
      status: c.status,
      answer: c.answer_text,
      error: c.error,
      confidence: c.confidence,
      evidence: c.evidence_json,
    });
  };

  if (c.error) {
    return (
      <div className="py-1">
        <Badge variant="error">error</Badge>
        <p className="mt-1 truncate text-xs text-error">{c.error}</p>
      </div>
    );
  }

  if (c.answer_text) {
    return (
      <div className="py-1">
        <div className="flex items-center gap-1.5">
          <Badge variant={statusVariant(c.status)}>{c.status}</Badge>
          {c.confidence ? (
            <span className="text-[10px] font-medium text-subtle">{c.confidence}</span>
          ) : null}
        </div>
        <p className="mt-1 line-clamp-3 text-sm text-ink">{c.answer_text}</p>
        <button
          onClick={handleOpenModal}
          className="mt-1 inline-flex items-center gap-1 text-xs font-semibold text-accent hover:underline"
        >
          <Maximize2 className="size-3" /> Read more
        </button>
      </div>
    );
  }

  return <Badge variant={statusVariant(c.status)}>{c.status}</Badge>;
}
