"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, ServerOff } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { API_BASE_URL, api } from "@/lib/api";

const features = [
  "Global paper library with multi-table membership",
  "Tags and metadata filters for table creation",
  "Dynamic extraction matrix with AG Grid",
  "One-off stateless Q&A over selected papers",
  "Similarity scores from local indexed embeddings",
];

export default function HomePage() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, retry: false });

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <section className="rounded-3xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="mb-5 flex flex-wrap items-center gap-3">
          <Badge tone={health.data?.ok ? "green" : "amber"}>
            {health.data?.ok ? "API connected" : "API pending"}
          </Badge>
          <span className="text-sm text-slate-500">{API_BASE_URL}</span>
        </div>
        <h1 className="max-w-3xl text-4xl font-bold tracking-tight text-slate-950 md:text-5xl">
          Evidence-backed extraction tables for your local paper library.
        </h1>
        <p className="mt-4 max-w-2xl text-base text-slate-600">
          This frontend talks to the local catena FastAPI server. Keep parsing, embeddings,
          BAML calls, SQLite, LanceDB, and secrets in Python; use the web app as the product UI.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <Link
            href="/tables"
            className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-800"
          >
            Open tables <ArrowRight className="size-4" />
          </Link>
          <Link
            href="/papers"
            className="inline-flex h-10 items-center justify-center rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium transition hover:bg-slate-50"
          >
            Manage papers
          </Link>
        </div>
      </section>

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Local server</CardTitle>
            <CardDescription>Start all services through mise.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <code className="block rounded-xl bg-slate-950 p-3 text-slate-50">mise run api:dev</code>
            <code className="block rounded-xl bg-slate-950 p-3 text-slate-50">mise run web:dev</code>
            {health.isError ? (
              <div className="flex items-center gap-2 text-amber-700">
                <ServerOff className="size-4" /> API is not reachable yet.
              </div>
            ) : null}
            {health.data ? (
              <div className="rounded-xl bg-slate-50 p-3 text-slate-600">
                data dir: {health.data.data_dir}
                <br />
                gateway ready: {health.data.gateway_ready ? "yes" : "no"}
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Included UI surface</CardTitle>
            <CardDescription>Frontend routes for the current catena features.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm text-slate-700">
              {features.map((feature) => (
                <li key={feature} className="flex gap-2">
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                  {feature}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
