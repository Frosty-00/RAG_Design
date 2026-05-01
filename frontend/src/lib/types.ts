/** Frontend-side mirror of backend pydantic shapes.
 *  Keep in sync manually for now; Layer 15 will wire openapi-typescript via
 *  `npm run gen:types`.
 */

export interface DocumentMeta {
  doc_id: string;
  filename: string;
  owner_id: string;
  latest_version: number;
  latest_status: string;
  n_chunks?: number | null;
  updated_at?: string | null;
  acl?: Record<string, unknown> | null;
  /** populated when latest_status === "failed" */
  error?: string | null;
}

export interface UploadResponse {
  doc_id: string;
  version: number;
  task_id: string;
  status: string;
  filename: string;
}

export interface TaskRef {
  task_id: string;
  doc_id: string;
}

export interface Citation {
  index: number;
  chunk_id: string;
  doc_id: string;
  page?: number | null;
  breadcrumbs: string[];
  text_preview: string;
}

export type ChatEventName = "ack" | "token" | "citations" | "error";

export interface ChatEvent {
  event: ChatEventName;
  phase?: "accepted" | "retrieving" | "generating";
  token?: string;
  citations?: Citation[];
  meta?: Record<string, unknown>;
  error?: string;
}

export interface EvalRunMeta {
  run_id: string;
  status: "pending" | "running" | "done" | "failed";
  dataset?: string | null;
  mode?: "retrieval_only" | "full" | null;
  started_at?: string | null;
  finished_at?: string | null;
  n_samples?: number | null;
  metrics?: Record<string, number> | null;
  prompt_versions?: Record<string, number> | null;
  judge_model?: string | null;
  n_bad_cases?: number | null;
  report_path?: string | null;
  error?: string | null;
}

export interface EvalDataset {
  path: string;
  name: string;
  samples: number;
  per_doc: Record<string, number>;       // doc_id → sample count
  preview_question?: string | null;
  size_bytes: number;
  modified: string;
}

export interface MetricDelta {
  baseline: number;
  candidate: number;
  delta: number;
  pct: number;
}

export interface EvalDiff {
  baseline_id: string;
  candidate_id: string;
  baseline_dataset: string;
  candidate_dataset: string;
  metric_diffs: Record<string, MetricDelta>;
  newly_bad: string[];
  newly_good: string[];
  prompt_changes: Record<string, { baseline: number | null; candidate: number | null }>;
}

export interface DebugChunk {
  chunk_id: string;
  doc_id: string;
  score: number;
  text_preview: string;
  metadata: Record<string, unknown>;
}

export interface DebugRetrieveResponse {
  understanding: {
    intent: "needs_retrieval" | "chitchat";
    resolved_query: string;
    rewrites: string[];
  };
  chunks: DebugChunk[];
}
