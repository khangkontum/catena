"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { HelpCircle, Send } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
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
    <div className="space-y-8">
      <PageHeader
        title="Ask"
        subtitle="Stateless retrieval Q&A. The backend stores no chat history."
      />

      <div className="grid gap-6 lg:grid-cols-[420px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Compose question</CardTitle>
            <CardDescription>Choose papers directly or ask over a table.</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-3" onSubmit={form.handleSubmit((values) => ask.mutate(values))}>
              <Textarea
                placeholder="What is the primary technical contribution?"
                {...form.register("question")}
              />
              <Input placeholder="Paper IDs, comma separated" {...form.register("paper_ids")} />
              <Select {...form.register("table_id")}>
                <option value="">Or choose a table</option>
                {(tables.data ?? []).map((table) => (
                  <option key={table.id} value={table.id}>
                    {table.name}
                  </option>
                ))}
              </Select>
              <Input type="number" placeholder="Top K per paper" {...form.register("top_k")} />
              <Button disabled={ask.isPending} type="submit" className="w-full">
                <Send className="size-4" /> Ask question
              </Button>
            </form>
            <div className="mt-5 text-xs text-subtle">
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
  if (loading) {
    return (
      <Card className="flex min-h-[300px] items-center justify-center">
        <CardContent>
          <div className="flex flex-col items-center gap-3 text-muted">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
            <p className="text-sm">Retrieving and answering...</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!answer) {
    return (
      <Card className="flex min-h-[300px] items-center">
        <CardContent className="w-full">
          <EmptyState
            icon={HelpCircle}
            title="No answer yet"
            description="Ask a question to see evidence-backed results."
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Answer</CardTitle>
        <CardDescription>Evidence from BAML over retrieved context.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-5">
          <div className="rounded-xl bg-raised p-5 text-ink leading-relaxed">{answer.answer}</div>
          <div className="flex flex-wrap gap-2">
            {answer.confidence ? <Badge variant="success">confidence: {answer.confidence}</Badge> : null}
            <Badge variant="default">papers: {answer.paper_ids.join(", ")}</Badge>
            <Badge variant="default">chunks: {answer.retrieved_chunk_ids.join(", ")}</Badge>
          </div>
          {answer.rationale ? (
            <p className="text-sm text-muted">
              <span className="font-medium text-ink">Rationale:</span> {answer.rationale}
            </p>
          ) : null}
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-[0.08em] text-subtle">
              Evidence
            </h3>
            <div className="space-y-2">
              {answer.evidence.map((item, index) => (
                <div key={index} className="rounded-lg border border-border bg-surface p-3 text-sm">
                  <div className="mb-1 text-xs text-subtle">
                    paper {String(item.paper_id ?? "")} · page {String(item.page ?? "")} · chunk {String(item.chunk_id ?? "")}
                  </div>
                  {String(item.quote ?? "")}
                </div>
              ))}
              {!answer.evidence.length ? (
                <p className="text-sm text-muted">No evidence returned.</p>
              ) : null}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
