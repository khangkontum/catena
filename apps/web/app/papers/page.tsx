"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Upload } from "lucide-react";

import { Badge, statusTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, type PaperOut } from "@/lib/api";
import { compactNumber } from "@/lib/utils";

const uploadSchema = z.object({
  pdf: z.custom<FileList>((value) => value instanceof FileList && value.length > 0, "PDF is required"),
  title: z.string().optional(),
  doi: z.string().optional(),
  url: z.string().optional(),
  table_id: z.string().optional(),
  no_table: z.boolean().optional(),
});

type UploadForm = z.infer<typeof uploadSchema>;

export default function PapersPage() {
  const queryClient = useQueryClient();
  const papers = useQuery({ queryKey: ["papers"], queryFn: () => api.papers() });
  const tables = useQuery({ queryKey: ["tables"], queryFn: api.tables });
  const form = useForm<UploadForm>({ resolver: zodResolver(uploadSchema) });

  const uploadPaper = useMutation({
    mutationFn: (values: UploadForm) => {
      const body = new FormData();
      body.set("pdf", values.pdf[0]);
      if (values.title) body.set("title", values.title);
      if (values.doi) body.set("doi", values.doi);
      if (values.url) body.set("url", values.url);
      if (values.table_id) body.set("table_id", values.table_id);
      body.set("no_table", String(values.no_table ?? false));
      return api.uploadPaper(body);
    },
    onSuccess: async () => {
      form.reset();
      toast.success("Paper uploaded and indexed");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["papers"] }),
        queryClient.invalidateQueries({ queryKey: ["matrix"] }),
      ]);
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Papers</h1>
        <p className="mt-1 text-slate-600">
          Papers are stored globally. Tags and metadata drive filtered tables without reparsing.
        </p>
      </div>

      <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>Global library</CardTitle>
            <CardDescription>{papers.data?.length ?? 0} papers in SQLite</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-left text-sm">
                <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="py-3 pr-4">ID</th>
                    <th className="py-3 pr-4">Title</th>
                    <th className="py-3 pr-4">Year</th>
                    <th className="py-3 pr-4">Cites</th>
                    <th className="py-3 pr-4">Venue</th>
                    <th className="py-3 pr-4">Tags</th>
                    <th className="py-3 pr-4">Status</th>
                    <th className="py-3 pr-4">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {(papers.data ?? []).map((paper) => (
                    <PaperRow key={paper.id} paper={paper} />
                  ))}
                  {!papers.isLoading && !papers.data?.length ? (
                    <tr>
                      <td colSpan={8} className="py-8 text-center text-slate-500">
                        No papers yet. Upload a PDF to start.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Upload PDF</CardTitle>
            <CardDescription>Docling parsing and embeddings run once per global paper.</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-3" onSubmit={form.handleSubmit((values) => uploadPaper.mutate(values))}>
              <Input type="file" accept="application/pdf" {...form.register("pdf")} />
              <Input placeholder="Optional title" {...form.register("title")} />
              <Input placeholder="Optional DOI" {...form.register("doi")} />
              <Input placeholder="Optional URL" {...form.register("url")} />
              <select
                className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                {...form.register("table_id")}
              >
                <option value="">Default table</option>
                {(tables.data ?? []).map((table) => (
                  <option key={table.id} value={table.id}>
                    {table.name}
                  </option>
                ))}
              </select>
              <label className="flex items-center gap-2 text-sm text-slate-600">
                <input type="checkbox" {...form.register("no_table")} /> Only add globally
              </label>
              <Button disabled={uploadPaper.isPending} type="submit" className="w-full">
                <Upload className="size-4" /> Upload and index
              </Button>
              {form.formState.errors.pdf ? (
                <p className="text-sm text-red-600">{form.formState.errors.pdf.message}</p>
              ) : null}
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function PaperRow({ paper }: { paper: PaperOut }) {
  const queryClient = useQueryClient();
  const [tagText, setTagText] = React.useState("");
  const addTag = useMutation({
    mutationFn: () => api.addTags(paper.id, tagText.split(",").map((tag) => tag.trim()).filter(Boolean)),
    onSuccess: async () => {
      setTagText("");
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
    <tr className="align-top">
      <td className="py-4 pr-4 font-mono text-xs text-slate-500">{paper.id}</td>
      <td className="max-w-sm py-4 pr-4 font-medium text-slate-950">{paper.title}</td>
      <td className="py-4 pr-4">{paper.year ?? ""}</td>
      <td className="py-4 pr-4">{compactNumber(paper.citation_count)}</td>
      <td className="max-w-40 py-4 pr-4 text-slate-600">{paper.venue ?? ""}</td>
      <td className="max-w-48 py-4 pr-4">
        <div className="mb-2 flex flex-wrap gap-1">
          {paper.tags.map((tag) => (
            <Badge key={tag} tone="blue">
              {tag}
            </Badge>
          ))}
        </div>
        <div className="flex gap-1">
          <Input
            value={tagText}
            onChange={(event) => setTagText(event.target.value)}
            placeholder="tag, tag"
            className="h-8"
          />
          <Button size="sm" variant="outline" disabled={!tagText || addTag.isPending} onClick={() => addTag.mutate()}>
            Add
          </Button>
        </div>
      </td>
      <td className="space-y-1 py-4 pr-4">
        <Badge tone={statusTone(paper.parse_status)}>{paper.parse_status}</Badge>
        <br />
        <Badge tone={statusTone(paper.index_status)}>{paper.index_status}</Badge>
      </td>
      <td className="py-4 pr-4">
        <Button size="sm" variant="outline" disabled={enrich.isPending} onClick={() => enrich.mutate()}>
          Enrich
        </Button>
      </td>
    </tr>
  );
}
