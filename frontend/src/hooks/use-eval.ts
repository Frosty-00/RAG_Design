import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { EvalDataset, EvalDiff, EvalRunMeta } from "@/lib/types";

export function useEvalRuns() {
  return useQuery({
    queryKey: ["eval", "runs"],
    queryFn: () => api.get<EvalRunMeta[]>("/api/v1/eval/runs"),
    refetchInterval: (q) => {
      const data = q.state.data as EvalRunMeta[] | undefined;
      const inflight = data?.some(
        (r) => r.status === "running" || r.status === "pending",
      );
      return inflight ? 2000 : 10_000;
    },
  });
}

export function useEvalRun(runId: string | null) {
  return useQuery({
    queryKey: ["eval", "run", runId],
    queryFn: () =>
      api.get<{ meta: EvalRunMeta; report: unknown }>(
        `/api/v1/eval/runs/${encodeURIComponent(runId!)}`,
      ),
    enabled: !!runId,
  });
}

export function useStartEval() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      dataset: string;
      mode: "retrieval_only" | "full";
      limit?: number;
    }) => api.post<{ run_id: string; status: string }>("/api/v1/eval/runs", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval", "runs"] }),
  });
}

export function useDiffRuns() {
  return useMutation({
    mutationFn: (body: { baseline_id: string; candidate_id: string }) =>
      api.post<EvalDiff>("/api/v1/eval/diff", body),
  });
}

export function useDeleteEvalRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      api.del<{ run_id: string; deleted_files: string[] }>(
        `/api/v1/eval/runs/${encodeURIComponent(runId)}`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval", "runs"] }),
  });
}

export function useEvalDatasets() {
  return useQuery({
    queryKey: ["eval", "datasets"],
    queryFn: () => api.get<EvalDataset[]>("/api/v1/eval/datasets"),
    staleTime: 30_000,
  });
}

export function useDeleteDataset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api.del<{ deleted: string }>(
        `/api/v1/eval/datasets/${encodeURIComponent(name)}`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval", "datasets"] }),
  });
}

export interface GenerateDatasetResult {
  path: string;
  samples: number;
  failures: number;
  per_doc: Record<string, number>;
  overwrote_existing: boolean;
}

export function useGenerateDataset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      count: number;
      doc_ids?: string[] | null;
      output_name?: string | null;
    }) =>
      api.post<GenerateDatasetResult>(
        "/api/v1/eval/datasets/generate", body,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval", "datasets"] }),
  });
}
