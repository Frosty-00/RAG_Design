/** In-flight & failed uploads tracker.
 *
 * Without this the user gets *no* table feedback during the window between
 * dropping a file and the first GET /documents that includes it. The dropzone
 * pushes a row here the instant the upload starts, so the table shows
 *
 *     filename   —   uploading   —   ⏳
 *
 * immediately. After the POST resolves we drop the entry and let the real
 * server-side row (status=pending → parsing → embedding → done) take over,
 * matched by filename + version. On failure the entry is *kept* with status
 * `upload_failed` so the user can see why and click Retry / Dismiss — instead
 * of the previous "toast disappears, table stays empty, ¯\_(ツ)_/¯" UX.
 */
import { create } from "zustand";

export type InflightStatus = "uploading" | "upload_failed";

export interface InflightUpload {
  /** Client-side id; survives until either the server-side row arrives
   *  (matched by filename) or the user dismisses a failed row. */
  tempId: string;
  filename: string;
  size: number;
  status: InflightStatus;
  startedAt: number;
  /** Populated only when status === "upload_failed". */
  error?: string;
  /** Set on success so we can match against the GET /documents response and
   *  graduate the row (avoid a flicker where the optimistic row vanishes
   *  before the real one shows up). */
  doc_id?: string;
  version?: number;
}

interface UploadsState {
  items: InflightUpload[];

  start: (filename: string, size: number) => string;
  succeed: (tempId: string, doc_id: string, version: number) => void;
  fail: (tempId: string, error: string) => void;
  dismiss: (tempId: string) => void;
  /** Drop entries that match a server-side document already present in
   *  GET /documents. Called from the documents page on every refetch. */
  reconcile: (serverFilenames: Set<string>) => void;
}

let counter = 0;
const nextId = () => `up-${Date.now()}-${counter++}`;

export const useUploadsStore = create<UploadsState>((set) => ({
  items: [],

  start: (filename, size) => {
    const tempId = nextId();
    set((s) => ({
      items: [
        ...s.items,
        {
          tempId, filename, size,
          status: "uploading",
          startedAt: Date.now(),
        },
      ],
    }));
    return tempId;
  },

  succeed: (tempId, doc_id, version) =>
    set((s) => ({
      items: s.items.map((it) =>
        it.tempId === tempId ? { ...it, doc_id, version } : it,
      ),
    })),

  fail: (tempId, error) =>
    set((s) => ({
      items: s.items.map((it) =>
        it.tempId === tempId
          ? { ...it, status: "upload_failed", error }
          : it,
      ),
    })),

  dismiss: (tempId) =>
    set((s) => ({ items: s.items.filter((it) => it.tempId !== tempId) })),

  reconcile: (serverFilenames) =>
    set((s) => ({
      // Drop succeeded uploads whose server row has arrived. Keep
      // upload_failed entries (user must dismiss explicitly) and uploading
      // entries (POST still in flight).
      items: s.items.filter((it) => {
        if (it.status === "upload_failed") return true;
        if (it.status === "uploading" && !it.doc_id) return true;
        // succeeded: graduate once server confirms
        return !serverFilenames.has(it.filename);
      }),
    })),
}));
