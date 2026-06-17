"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { parseNumberList } from "@/lib/utils";

const similaritySchema = z.object({
  paper_ids: z.string().optional(),
  table_id: z.string().optional(),
});

type SimilarityForm = z.infer<typeof similaritySchema>;

export default function SimilarityPage() {
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
    onSuccess: (rows) => toast.success(`Stored ${rows.length} similarity score${rows.length === 1 ? "" : "s"}`),
    onError: (error) => toast.error(error.message),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Similarity</h1>
        <p className="mt-1 text-slate-600">
          Compute deterministic local paper-pair scores from already indexed chunk embeddings.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Compute scores</CardTitle>
            <CardDescription>No external recommender API is used.</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-3" onSubmit={form.handleSubmit((values) => compute.mutate(values))}>
              <Input placeholder="Paper IDs, comma separated" {...form.register("paper_ids")} />
              <select className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm" {...form.register("table_id")}>
                <option value="">All papers</option>
                {(tables.data ?? []).map((table) => (
                  <option key={table.id} value={table.id}>
                    {table.name}
                  </option>
                ))}
              </select>
              <Button disabled={compute.isPending} type="submit" className="w-full">
                Compute
              </Button>
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Latest results</CardTitle>
            <CardDescription>Top scores returned from this compute run.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="py-3 pr-4">Paper A</th>
                    <th className="py-3 pr-4">Paper B</th>
                    <th className="py-3 pr-4">Score</th>
                    <th className="py-3 pr-4">Cosine</th>
                    <th className="py-3 pr-4">Algorithm</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {(compute.data ?? []).map((row) => (
                    <tr key={row.id}>
                      <td className="py-3 pr-4">{row.paper_id_a}</td>
                      <td className="py-3 pr-4">{row.paper_id_b}</td>
                      <td className="py-3 pr-4">
                        <Badge tone="green">{row.score.toFixed(3)}</Badge>
                      </td>
                      <td className="py-3 pr-4">{row.cosine_similarity.toFixed(3)}</td>
                      <td className="py-3 pr-4 text-slate-500">{row.algorithm}</td>
                    </tr>
                  ))}
                  {!compute.data?.length ? (
                    <tr>
                      <td colSpan={5} className="py-8 text-center text-slate-500">
                        Compute similarities to see results.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
