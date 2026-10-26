import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";

import { useToast } from "../lib/toast";
import { Button, Card, EmptyState, ErrorNote, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { formatDateTime } from "../lib/format";
import type { Job } from "../lib/types";

export function Jobs() {
  const client = useQueryClient();
  const toast = useToast();
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => api<Job[]>("/api/jobs"), refetchInterval: 15_000 });
  const retry = useMutation({
    mutationFn: (id: number) => api(`/api/jobs/${id}/retry`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Queued again. The worker will retry it now.");
      void client.invalidateQueries({ queryKey: ["jobs"] });
      void client.invalidateQueries({ queryKey: ["summary"] });
    },
    onError: (e) => toast.error(e.message),
  });

  return (
    <>
      <PageHeader
        title="Failed jobs"
        subtitle="Messages the worker gave up on after three tries. Fix the cause, then retry."
      />
      <Card>
        {jobs.isPending ? (
          <Spinner />
        ) : jobs.error ? (
          <div className="p-5">
            <ErrorNote error={jobs.error} />
          </div>
        ) : jobs.data.length === 0 ? (
          <EmptyState title="No failed jobs" hint="Every message was processed." />
        ) : (
          <ul className="divide-y divide-ink-100">
            {jobs.data.map((job) => (
              <li key={job.id} className="flex flex-wrap items-start gap-4 px-5 py-4">
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-ink-900">“{job.message_preview}”</p>
                  <p className="mt-1 font-mono text-xs break-all text-red-700">{job.last_error}</p>
                  <p className="mt-1 text-xs text-ink-500">
                    {job.attempts} attempts · last {formatDateTime(job.updated_at)}
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  loading={retry.isPending && retry.variables === job.id}
                  onClick={() => retry.mutate(job.id)}
                >
                  <RotateCcw className="size-3.5" aria-hidden /> Retry
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
