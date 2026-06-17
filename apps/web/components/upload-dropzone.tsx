"use client";

import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { FolderOpen, Upload, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface QueuedUploadFile {
  file: File;
  relativePath: string;
}

interface DirectoryInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  webkitdirectory?: string;
  directory?: string;
}

const directoryInputProps: Pick<DirectoryInputProps, "webkitdirectory" | "directory"> = {
  webkitdirectory: "",
  directory: "",
};

export function UploadDropzone({
  tables,
  onUploaded,
}: {
  tables: Array<{ id: number; name: string }>;
  onUploaded: () => Promise<void>;
}) {
  const fileInputRef = React.useRef<HTMLInputElement | null>(null);
  const folderInputRef = React.useRef<HTMLInputElement | null>(null);
  const [queuedFiles, setQueuedFiles] = React.useState<QueuedUploadFile[]>([]);
  const [dragging, setDragging] = React.useState(false);
  const [tableId, setTableId] = React.useState("");
  const [noTable, setNoTable] = React.useState(false);
  const [title, setTitle] = React.useState("");

  const addQueuedFiles = React.useCallback((items: QueuedUploadFile[]) => {
    const pdfs = items.filter(isPdfUploadFile);
    if (!pdfs.length) {
      toast.info("No PDF files found.");
      return;
    }
    setQueuedFiles((current) => {
      const next = new Map(current.map((item) => [queuedFileKey(item), item]));
      for (const item of pdfs) {
        next.set(queuedFileKey(item), item);
      }
      return Array.from(next.values()).sort((left, right) =>
        left.relativePath.localeCompare(right.relativePath),
      );
    });
  }, []);

  const uploadPapers = useMutation({
    mutationFn: async () => {
      if (!queuedFiles.length) {
        throw new Error("Choose or drop at least one PDF.");
      }
      const body = new FormData();
      if (tableId) {
        body.set("table_id", tableId);
      }
      body.set("no_table", String(noTable));
      if (queuedFiles.length === 1) {
        const item = queuedFiles[0];
        body.set("pdf", item.file, item.relativePath);
        if (title.trim()) {
          body.set("title", title.trim());
        }
        return [await api.uploadPaper(body)];
      }
      for (const item of queuedFiles) {
        body.append("pdfs", item.file, item.relativePath);
      }
      return api.uploadPapers(body);
    },
    onSuccess: async (uploaded) => {
      setQueuedFiles([]);
      setTitle("");
      toast.success(`Uploaded ${uploaded.length} paper${uploaded.length === 1 ? "" : "s"}`);
      await onUploaded();
    },
    onError: (error) => toast.error(error.message),
  });

  async function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    addQueuedFiles(await queuedFilesFromDataTransfer(event.dataTransfer));
  }

  function handleFileInput(files: FileList | null) {
    addQueuedFiles(queuedFilesFromFileList(files));
  }

  const queuedCount = queuedFiles.length;
  const previewFiles = queuedFiles.slice(0, 8);

  return (
    <div className="space-y-5">
      <div
        className={cn(
          "rounded-xl border-2 border-dashed p-6 text-center transition",
          dragging
            ? "border-accent bg-accent-subtle"
            : "border-border-strong bg-raised hover:border-border",
        )}
        onDragEnter={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => void handleDrop(event)}
      >
        <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-surface text-muted shadow-sm">
          <Upload className="size-5" />
        </div>
        <div className="font-serif font-medium text-ink">Drop PDFs or folders here</div>
        <p className="mt-1 text-sm text-muted">
          Folder drops are scanned recursively and only PDF files are queued.
        </p>
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <Button type="button" variant="outline" onClick={() => fileInputRef.current?.click()}>
            Choose PDFs
          </Button>
          <Button type="button" variant="outline" onClick={() => folderInputRef.current?.click()}>
            <FolderOpen className="size-4" /> Choose folder
          </Button>
        </div>
      </div>

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="application/pdf,.pdf"
        className="hidden"
        onChange={(event) => {
          handleFileInput(event.currentTarget.files);
          event.currentTarget.value = "";
        }}
      />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => {
          handleFileInput(event.currentTarget.files);
          event.currentTarget.value = "";
        }}
        {...directoryInputProps}
      />

      {queuedCount ? (
        <div className="rounded-xl border border-border bg-surface p-4">
          <div className="mb-2 flex items-center justify-between gap-3">
            <span className="text-sm font-medium text-ink">
              Queued PDFs: {queuedCount}
            </span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setQueuedFiles([])}
            >
              Clear
            </Button>
          </div>
          <div className="space-y-1 text-xs text-muted">
            {previewFiles.map((item) => (
              <div key={queuedFileKey(item)} className="flex items-center justify-between gap-2">
                <span className="truncate">{item.relativePath}</span>
                <button
                  type="button"
                  className="rounded p-1 text-subtle hover:bg-raised hover:text-ink"
                  onClick={() =>
                    setQueuedFiles((current) =>
                      current.filter((candidate) => queuedFileKey(candidate) !== queuedFileKey(item)),
                    )
                  }
                  aria-label={`Remove ${item.relativePath}`}
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
            {queuedCount > previewFiles.length ? (
              <div className="text-subtle">+ {queuedCount - previewFiles.length} more</div>
            ) : null}
          </div>
        </div>
      ) : null}

      {queuedCount === 1 ? (
        <Input
          placeholder="Optional title override for this PDF"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      ) : null}

      <Select value={tableId} onChange={(event) => setTableId(event.target.value)}>
        <option value="">Add to default table</option>
        {tables.map((table) => (
          <option key={table.id} value={table.id}>
            {table.name}
          </option>
        ))}
      </Select>

      <label className="flex items-center gap-2 text-sm text-muted">
        <input
          type="checkbox"
          checked={noTable}
          onChange={(event) => setNoTable(event.target.checked)}
          className="rounded border-border-strong text-accent focus:ring-accent"
        />
        Only add to the global library
      </label>

      <Button
        disabled={!queuedCount || uploadPapers.isPending}
        type="button"
        className="w-full"
        onClick={() => uploadPapers.mutate()}
      >
        <Upload className="size-4" /> Upload {queuedCount || ""} PDF
        {queuedCount === 1 ? "" : "s"}
      </Button>
    </div>
  );
}

function queuedFilesFromFileList(files: FileList | null): QueuedUploadFile[] {
  return Array.from(files ?? []).map((file) => ({
    file,
    relativePath: file.webkitRelativePath || file.name,
  }));
}

async function queuedFilesFromDataTransfer(dataTransfer: DataTransfer): Promise<QueuedUploadFile[]> {
  const entries = Array.from(dataTransfer.items)
    .map((item) => item.webkitGetAsEntry?.())
    .filter((entry): entry is FileSystemEntry => Boolean(entry));

  if (entries.length) {
    const nested = await Promise.all(entries.map((entry) => queuedFilesFromEntry(entry)));
    return nested.flat();
  }
  return queuedFilesFromFileList(dataTransfer.files);
}

async function queuedFilesFromEntry(
  entry: FileSystemEntry,
  parentPath = "",
): Promise<QueuedUploadFile[]> {
  if (entry.isFile) {
    const file = await fileFromEntry(entry as FileSystemFileEntry);
    return [
      {
        file,
        relativePath: [parentPath, file.name].filter(Boolean).join("/"),
      },
    ];
  }
  if (entry.isDirectory) {
    const directory = entry as FileSystemDirectoryEntry;
    const directoryPath = [parentPath, directory.name].filter(Boolean).join("/");
    const children = await readAllDirectoryEntries(directory);
    const nested = await Promise.all(
      children.map((child) => queuedFilesFromEntry(child, directoryPath)),
    );
    return nested.flat();
  }
  return [];
}

function fileFromEntry(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

async function readAllDirectoryEntries(directory: FileSystemDirectoryEntry) {
  const reader = directory.createReader();
  const entries: FileSystemEntry[] = [];
  while (true) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    );
    if (!batch.length) {
      break;
    }
    entries.push(...batch);
  }
  return entries;
}

function isPdfUploadFile(item: QueuedUploadFile) {
  return item.relativePath.toLowerCase().endsWith(".pdf") || item.file.type === "application/pdf";
}

function queuedFileKey(item: QueuedUploadFile) {
  return `${item.relativePath}:${item.file.size}:${item.file.lastModified}`;
}
