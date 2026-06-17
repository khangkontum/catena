"use client";

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { FileText, Plus, Sparkles, Tag as TagIcon } from "lucide-react";
import { toast } from "sonner";

import { Badge, statusVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/empty-state";
import { UploadDropzone } from "@/components/upload-dropzone";
import { PageHeader } from "@/components/page-header";
import { api, type PaperOut } from "@/lib/api";
import { compactNumber } from "@/lib/utils";

export default function LibraryPage() {
  const queryClient = useQueryClient();
  const papers = useQuery({ queryKey: ["papers"], queryFn: () => api.papers() });
  const tables = useQuery({ queryKey: ["tables"], queryFn: api.tables });
  const [search, setSearch] = React.useState("");

  const filtered = React.useMemo(() => {
    const term = search.toLowerCase().trim();
    if (!term) return papers.data ?? [];
    return (papers.data ?? []).filter(
      (p) =>
        p.title.toLowerCase().includes(term) ||
        p.tags.some((t) => t.toLowerCase().includes(term)) ||
        (p.venue ?? "").toLowerCase().includes(term),
    );
  }, [papers.data, search]);

  return (
    <div className="space-y-8">
      <PageHeader
        title="Library"
        subtitle="Your global collection of papers. Parse, tag, and enrich metadata here."
      />

      <div className="grid gap-6 xl:grid-cols-[1fr_400px]">
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <Input
              placeholder="Search by title, tag, venue..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="max-w-sm"
            />
            <span className="text-sm text-muted">
              {filtered.length} paper{filtered.length === 1 ? "" : "s"}
            </span>
          </div>

          {filtered.length ? (
            <div className="space-y-3">
              {filtered.map((paper) => (
                <PaperCard key={paper.id} paper={paper} />
              ))}
            </div>
          ) : (
            <EmptyState
              icon={FileText}
              title="No papers found"
              description="Upload PDFs to start building your library."
            />
          )}
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Upload</CardTitle>
              <CardDescription>Drag in PDFs, multiple files, or an entire folder.</CardDescription>
            </CardHeader>
            <CardContent>
              <UploadDropzone
                tables={tables.data ?? []}
                onUploaded={async () => {
                  await Promise.all([
                    queryClient.invalidateQueries({ queryKey: ["papers"] }),
                    queryClient.invalidateQueries({ queryKey: ["matrix"] }),
                  ]);
                }}
              />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function PaperCard({ paper }: { paper: PaperOut }) {
  const queryClient = useQueryClient();
  const [tagText, setTagText] = React.useState("");
  const [showTagInput, setShowTagInput] = React.useState(false);

  const addTag = useMutation({
    mutationFn: () =>
      api.addTags(
        paper.id,
        tagText
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
      ),
    onSuccess: async () => {
      setTagText("");
      setShowTagInput(false);
      toast.success("Tags added");
      await queryClient.invalidateQueries({ queryKey: ["papers"] });
    },
    onError: (error) => toast.error(error.message),
  });

  const enrich = useMutation({
    mutationFn: () => api.enrichPaper(paper.id),
    onSuccess: async () => {
      toast.success("Metadata enriched");
      await queryClient.invalidateQueries({ queryKey: ["papers"] });
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-xs text-subtle">
            <span className="font-mono">#{paper.id}</span>
            {paper.year ? <span>· {paper.year}</span> : null}
            {paper.venue ? <span>· {paper.venue}</span> : null}
            {paper.citation_count ? <span>· {compactNumber(paper.citation_count)} citations</span> : null}
          </div>
          <h3 className="mt-1 font-serif text-lg font-medium leading-snug text-ink">
            {paper.title}
          </h3>
          {paper.authors_json?.length ? (
            <p className="mt-1 text-sm text-muted">
              {paper.authors_json.slice(0, 3).join(", ")}
              {paper.authors_json.length > 3 ? ` +${paper.authors_json.length - 3} more` : ""}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-col gap-1.5">
          <Badge variant={statusVariant(paper.parse_status)}>{paper.parse_status}</Badge>
          <Badge variant={statusVariant(paper.index_status)}>{paper.index_status}</Badge>
        </div>
      </div>

      {paper.abstract ? (
        <p className="mt-3 line-clamp-2 text-sm leading-relaxed text-muted">{paper.abstract}</p>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {paper.tags.map((tag) => (
          <Badge key={tag} variant="accent">
            {tag}
          </Badge>
        ))}
        {!paper.tags.length ? <span className="text-xs text-subtle">No tags yet</span> : null}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="ghost" onClick={() => enrich.mutate()} disabled={enrich.isPending}>
          <Sparkles className="size-3.5" /> Enrich
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setShowTagInput((v) => !v)}>
          <TagIcon className="size-3.5" /> Add tag
        </Button>
        {showTagInput ? (
          <div className="flex items-center gap-2">
            <Input
              value={tagText}
              onChange={(e) => setTagText(e.target.value)}
              placeholder="tag, tag"
              className="h-8 w-40 text-xs"
              onKeyDown={(e) => {
                if (e.key === "Enter" && tagText.trim()) {
                  e.preventDefault();
                  addTag.mutate();
                }
              }}
            />
            <Button size="sm" variant="outline" disabled={!tagText || addTag.isPending} onClick={() => addTag.mutate()}>
              <Plus className="size-3.5" />
            </Button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

