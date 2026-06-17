"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

const tagSchema = z.object({
  name: z.string().min(1, "Name is required"),
  color: z.string().optional(),
  description: z.string().optional(),
});

type TagForm = z.infer<typeof tagSchema>;

export default function TagsPage() {
  const queryClient = useQueryClient();
  const tags = useQuery({ queryKey: ["tags"], queryFn: api.tags });
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
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Tags</h1>
        <p className="mt-1 text-slate-600">
          Tags can be attached to papers and used to create filtered tables.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
        <Card>
          <CardHeader>
            <CardTitle>Known tags</CardTitle>
            <CardDescription>{tags.data?.length ?? 0} tags</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 md:grid-cols-2">
              {(tags.data ?? []).map((tag) => (
                <div key={tag.id} className="rounded-xl border border-slate-200 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <Badge tone="blue">{tag.name}</Badge>
                    <span className="font-mono text-xs text-slate-400">#{tag.id}</span>
                  </div>
                  <div className="mt-2 text-xs text-slate-500">{tag.normalized_name}</div>
                  {tag.description ? <p className="mt-3 text-sm text-slate-600">{tag.description}</p> : null}
                </div>
              ))}
              {!tags.isLoading && !tags.data?.length ? (
                <div className="text-sm text-slate-500">No tags yet.</div>
              ) : null}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Create or update tag</CardTitle>
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
    </div>
  );
}
