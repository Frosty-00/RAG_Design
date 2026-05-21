import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import type { DocumentMeta, TaskRef, UploadResponse } from "@/lib/types";

export function useDocuments() {
  const q = useQuery({
    queryKey: ["documents"],
    queryFn: () => api.get<DocumentMeta[]>("/api/v1/documents"),
    refetchInterval: (query) => {
      const data = query.state.data as DocumentMeta[] | undefined;
      const pending = data?.some(
        (d) => d.latest_status !== "done" && d.latest_status !== "failed",
      );
      return pending ? 1500 : 8000;
    },
  });

  // Surface fresh failures as a one-time toast — without this the user
  // sees only a static red badge in the table and could miss the reason.
  const seenFailures = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!q.data) return;
    for (const d of q.data) {
      if (d.latest_status !== "failed") continue;
      const key = `${d.doc_id}:${d.latest_version}`;
      if (seenFailures.current.has(key)) continue;
      seenFailures.current.add(key);
      const reason =
        (d as DocumentMeta & { error?: string }).error ?? "ingest failed";
      toast(`${d.filename}: ${reason}`, "error");
    }
  }, [q.data]);

  return q;
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (params: {
      file: File;
      isPublic: boolean;
      users: string;
      groups: string;
    }) => {
      const fd = new FormData();
      fd.append("file", params.file);
      fd.append("public", params.isPublic ? "true" : "false");
      fd.append("users", params.users);
      fd.append("groups", params.groups);
      return api.upload<UploadResponse>("/api/v1/documents", fd);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (docId: string) =>
      api.del<TaskRef>(`/api/v1/documents/${encodeURIComponent(docId)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}

export interface AclPayload {
  public: boolean;
  users: string[];
  groups: string[];
}

/** Admin-only: replace a doc's ACL wholesale.
 *  Use cases: enable HR + IT cross-department access, narrow over-broad
 *  grants, revoke a private grant. Owner-only delete is the only stricter
 *  gate; everything else flows through this. */
export function useUpdateDocAcl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ docId, acl }: { docId: string; acl: AclPayload }) =>
      api.patch<DocumentMeta>(
        `/api/v1/documents/${encodeURIComponent(docId)}/acl`,
        acl,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}

export interface ChunkPreview {
  chunk_id: string;
  text: string;
  page?: number | null;
  breadcrumbs: string[];
  chunk_index?: number | null;
  prev_chunk_id?: string | null;
  next_chunk_id?: string | null;
}

export interface ChunksResponse {
  doc_id: string;
  filename: string;
  n_chunks: number;
  chunks: ChunkPreview[];
}

/** Fetch the chunks of a single document on demand. Used by the
 *  "View chunks" diagnostic dialog so the user can see exactly what the
 *  parser captured (helpful when retrieval misses content they expect). */
export function useDocChunks(docId: string | null) {
  return useQuery({
    queryKey: ["doc-chunks", docId],
    queryFn: () => api.get<ChunksResponse>(
      `/api/v1/documents/${encodeURIComponent(docId!)}/chunks`,
    ),
    enabled: !!docId,
    staleTime: 30_000,
  });
}

/** Poll a single delete task. */
export function useTaskStatus(taskId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["task", taskId],
    queryFn: () =>
      api.get<Record<string, unknown>>(
        `/api/v1/admin/dlq/${encodeURIComponent(taskId!)}`,
      ).catch(() => ({})),  // /admin/dlq is admin-only; fall back gracefully
    enabled: enabled && !!taskId,
    refetchInterval: 1000,
  });
}
