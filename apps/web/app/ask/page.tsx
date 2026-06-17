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
import { Textarea } from "@/components/ui/textarea";
import { api, type AskOut } from "@/lib/api";
import { parseNumberList } from "@/lib/utils";

const askSchema = z.object({
  question: z.string().min(1, "Question is required"),
  paper_ids: z.string().optional(),
  table_id: z.string().optional(),
  top_k: z.string().optional(),
});

type AskForm = z.infer<typeof askSchema>;

export default function AskPage() {
  const papers = useQuery({ queryKey: ["papers"], queryFn: () => api.papers() });
  const tables = useQuery({ queryKey: ["tables"], queryFn: api.tables });
  const form = useForm<AskForm>({ resolver: zodResolver(askSchema) });
  const ask = useMutation({
    mutationFn: (values: AskForm) => {
      const paperIds = parseNumberList(values.paper_ids ?? "");
      const tableId = values.table_id ? Number.parseInt(values.table_id, 10) : null;
      if (!paperIds.length && !tableId) {
        throw new Error("Select at least one paper or one table.");
      }
      return api.ask({
        question: values.question,
        paper_ids: paperIds.length ? paperIds : null,
        table_id: paperIds.length ? null : tableId,
        top_k: values.top_k ? Number.parseInt(values.top_k, 10) : null,
      });
    },
    onError: (error) => toast.error(error.message),
  });

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">One-off Q&A</h1>
        <p className="mt-1 text-slate-600">
          Stateless retrieval question answering. The backend stores no chat history.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[420px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Ask</CardTitle>
            <CardDescription>Choose papers directly or ask over a table.</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-3" onSubmit={form.handleSubmit((values) => ask.mutate(values))}>
              <Textarea placeholder="What is the primary technical contribution?" {...form.register("question")} />
              <Input placeholder="Paper IDs, comma separated" {...form.register("paper_ids")} />
              <select className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm" {...form.register("table_id")}>
                <option value="">Or choose a table</option>
                {(tables.data ?? []).map((table) => (
                  <option key={table.id} value={table.id}>
                    {table.name}
                  </option>
                ))}
              </select>
              <Input type="number" placeholder="Top K per paper" {...form.register("top_k")} />
              <Button disabled={ask.isPending} type="submit" className="w-full">
                Ask question
              </Button>
            </form>
            <div className="mt-5 text-xs text-slate-500">
              Available paper IDs: {(papers.data ?? []).map((paper) => paper.id).join(", ") || "none"}
            </div>
          </CardContent>
        </Card>

        <AnswerCard answer={ask.data} loading={ask.isPending} />
      </div>
    </div>
  );
}

function AnswerCard({ answer, loading }: { answer: AskOut | undefined; loading: boolean }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Answer</CardTitle>
        <CardDescription>Evidence is returned by BAML from retrieved context.</CardDescription>
      </CardHeader>
      <CardContent>
        {loading ? <p className="text-sm text-slate-500">Retrieving and answering…</p> : null}
        {!loading && !answer ? <p className="text-sm text-slate-500">Ask a question to see results.</p> : null}
        {answer ? (
          <div className="space-y-5">
            <div className="rounded-2xl bg-slate-50 p-5 text-slate-800">{answer.answer}</div>
            <div className="flex flex-wrap gap-2">
              {answer.confidence ? <Badge tone="green">confidence: {answer.confidence}</Badge> : null}
              <Badge tone="slate">papers: {answer.paper_ids.join(", ")}</Badge>
              <Badge tone="slate">chunks: {answer.retrieved_chunk_ids.join(", ")}</Badge>
            </div>
            {answer.rationale ? (
              <p className="text-sm text-slate-600">
                <span className="font-medium">Rationale:</span> {answer.rationale}
              </p>
            ) : null}
            <div>
              <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Evidence</h3>
              <div className="space-y-2">
                {answer.evidence.map((item, index) => (
                  <div key={index} className="rounded-xl border border-slate-200 p-3 text-sm">
                    <div className="mb-1 text-xs text-slate-500">
                      paper {String(item.paper_id ?? "")} · page {String(item.page ?? "")} · chunk {String(item.chunk_id ?? "")}
                    </div>
                    {String(item.quote ?? "")}
                  </div>
                ))}
                {!answer.evidence.length ? <p className="text-sm text-slate-500">No evidence returned.</p> : null}
              </div>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
