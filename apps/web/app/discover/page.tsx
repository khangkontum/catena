"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { GitCompare, Tag as TagIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { api, type SimilarityOut, type TagOut } from "@/lib/api";
import { cn, parseNumberList } from "@/lib/utils";

type Tab = "tags" | "similarity";

export default function DiscoverPage() {
  const [tab, setTab] = React.useState<Tab>("tags");

  return (
    <div className="space-y-8">
      <PageHeader
        title="Discover"
        subtitle="Manage tags and compute similarity scores from local embeddings."
      />

      <div className="flex gap-1 rounded-xl border border-border bg-surface p-1">
        <TabButton active={tab === "tags"} onClick={() => setTab("tags")} icon={TagIcon} label="Tags" />
        <TabButton active={tab === "similarity"} onClick={() => setTab("similarity")} icon={GitCompare} label="Similarity" />
      </div>

      {tab === "tags" ? <TagsPanel /> : <SimilarityPanel />}
    </div>
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
  icon: typeof TagIcon;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex flex-1 items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition",
        active ? "bg-accent text-accent-text shadow-sm" : "text-muted hover:bg-raised hover:text-ink",
      )}
    >
      <Icon className="size-4" /> {label}
    </button>
  );
}

function TagsPanel() {
  const queryClient = useQueryClient();
  const tags = useQuery({ queryKey: ["tags"], queryFn: api.tags });

  const tagSchema = z.object({
    name: z.string().min(1, "Name is required"),
    color: z.string().optional(),
    description: z.string().optional(),
  });

  type TagForm = z.infer<typeof tagSchema>;
  const form = useForm<TagForm>({ resolver: zodResolver(tagSchema) });

  const createTag = useMutation({
    mutationFn: api.createTag,
    onSuccess: async () => {
      form.reset();
      toast.success("Tag saved");
      await queryClient.invalidateQueries({ queryKey: ["tags"] });
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
      <Card className="min-w-0">
        <CardHeader>
          <CardTitle>Known tags</CardTitle>
          <CardDescription>{tags.data?.length ?? 0} tag{(tags.data?.length ?? 0) === 1 ? "" : "s"}</CardDescription>
        </CardHeader>
        <CardContent>
          {(tags.data ?? []).length ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {(tags.data ?? []).map((tag) => (
                <TagCard key={tag.id} tag={tag} />
              ))}
            </div>
          ) : (
            <EmptyState
              icon={TagIcon}
              title="No tags yet"
              description="Create a tag to start organizing your papers."
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Create tag</CardTitle>
          <CardDescription>Tags drive filtered table creation.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-3" onSubmit={form.handleSubmit((values) => createTag.mutate(values))}>
            <Input placeholder="hft" {...form.register("name")} />
            <Input placeholder="Color label, optional" {...form.register("color")} />
            <Input placeholder="Description" {...form.register("description")} />
            <Button disabled={createTag.isPending} type="submit" className="w-full">
              Save tag
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function TagCard({ tag }: { tag: TagOut }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4 shadow-sm transition hover:shadow-md">
      <div className="flex items-center justify-between gap-3">
        <Badge variant="accent">{tag.name}</Badge>
        <span className="font-mono text-xs text-subtle">#{tag.id}</span>
      </div>
      <div className="mt-2 text-xs text-subtle">{tag.normalized_name}</div>
      {tag.description ? <p className="mt-3 text-sm text-muted">{tag.description}</p> : null}
      {tag.color ? (
        <div className="mt-3 flex items-center gap-2 text-xs text-subtle">
          <span
            className="inline-block h-3 w-3 rounded-full"
            style={{ background: tag.color }}
          />
          {tag.color}
        </div>
      ) : null}
    </div>
  );
}

const similaritySchema = z.object({
  paper_ids: z.string().optional(),
  table_id: z.string().optional(),
});

type SimilarityForm = z.infer<typeof similaritySchema>;

function SimilarityPanel() {
  const tables = useQuery({ queryKey: ["tables"], queryFn: api.tables });
  const form = useForm<SimilarityForm>({ resolver: zodResolver(similaritySchema) });
  const compute = useMutation({
    mutationFn: (values: SimilarityForm) => {
      const paperIds = parseNumberList(values.paper_ids ?? "");
      return api.computeSimilarity({
        paper_ids: paperIds.length ? paperIds : null,
        table_id: paperIds.length || !values.table_id ? null : Number.parseInt(values.table_id, 10),
      });
    },
    onSuccess: (rows) =>
      toast.success(`Stored ${rows.length} similarity score${rows.length === 1 ? "" : "s"}`),
    onError: (error) => toast.error(error.message),
  });

  return (
    <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
      <Card>
        <CardHeader>
          <CardTitle>Compute scores</CardTitle>
          <CardDescription>No external recommender API is used.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-3" onSubmit={form.handleSubmit((values) => compute.mutate(values))}>
            <Input placeholder="Paper IDs, comma separated" {...form.register("paper_ids")} />
            <Select {...form.register("table_id")}>
              <option value="">All papers</option>
              {(tables.data ?? []).map((table) => (
                <option key={table.id} value={table.id}>
                  {table.name}
                </option>
              ))}
            </Select>
            <Button disabled={compute.isPending} type="submit" className="w-full">
              Compute
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Latest results</CardTitle>
          <CardDescription>Top scores from this compute run.</CardDescription>
        </CardHeader>
        <CardContent>
          {(compute.data ?? []).length ? (
            <div className="space-y-3">
              {(compute.data ?? []).map((row) => (
                <SimilarityRow key={row.id} row={row} />
              ))}
            </div>
          ) : (
            <EmptyState
              icon={GitCompare}
              title="No similarities yet"
              description="Compute similarities to see results."
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SimilarityRow({ row }: { row: SimilarityOut }) {
  const pct = Math.round(row.score * 100);
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-mono text-ink">#{row.paper_id_a}</span>
          <GitCompare className="size-3.5 text-subtle" />
          <span className="font-mono text-ink">#{row.paper_id_b}</span>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="success">{row.score.toFixed(3)}</Badge>
          <span className="text-xs text-subtle">{row.algorithm}</span>
        </div>
      </div>
      <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-raised">
        <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1 text-xs text-subtle">
        cosine: {row.cosine_similarity.toFixed(3)}
      </div>
    </div>
  );
}
