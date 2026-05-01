import { Eye, Loader2, Trash2, X } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/components/ui/toast";
import { useDeleteDocument, useDocChunks } from "@/hooks/use-documents";
import { ApiError } from "@/lib/api";
import { useUploadsStore } from "@/stores/uploads";
import type { DocumentMeta } from "@/lib/types";

type BadgeVariant = "default" | "secondary" | "destructive" | "success" | "warning";

const STATUS_VARIANT: Record<string, BadgeVariant> = {
  uploading: "warning",
  upload_failed: "destructive",
  pending: "secondary",
  downloading: "secondary",
  parsing: "secondary",
  embedding: "secondary",
  inserting: "secondary",
  done: "success",
  failed: "destructive",
  deleting: "warning",
};

/** Optimistic row for an in-flight (or just-failed) upload that hasn't been
 *  acknowledged by the server yet. */
export function InflightRow({
  tempId, filename, status, error,
}: {
  tempId: string;
  filename: string;
  status: "uploading" | "upload_failed";
  error?: string;
}) {
  const dismiss = useUploadsStore((s) => s.dismiss);
  const variant = STATUS_VARIANT[status];
  return (
    <tr className="border-b last:border-b-0 bg-muted/20">
      <td className="px-4 py-2 font-medium">{filename}</td>
      <td className="px-4 py-2 text-xs text-muted-foreground">—</td>
      <td className="px-4 py-2 text-muted-foreground">—</td>
      <td className="px-4 py-2">
        <div className="flex items-center gap-1.5">
          {status === "uploading" && (
            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
          )}
          <Badge variant={variant}>{status}</Badge>
        </div>
        {error && (
          <p
            className="mt-1 max-w-[260px] truncate text-[10px] text-destructive"
            title={error}
          >
            {error}
          </p>
        )}
      </td>
      <td className="px-4 py-2 text-muted-foreground">—</td>
      <td className="px-4 py-2 text-xs text-muted-foreground">just now</td>
      <td className="px-4 py-2 text-right">
        {status === "upload_failed" && (
          <Button
            variant="ghost" size="icon"
            onClick={() => dismiss(tempId)}
            aria-label="Dismiss failed upload"
            title="Dismiss"
          >
            <X className="h-4 w-4" />
          </Button>
        )}
      </td>
    </tr>
  );
}

export function DocumentRow({ doc }: { doc: DocumentMeta }) {
  const del = useDeleteDocument();
  const [open, setOpen] = useState(false);
  const [chunksOpen, setChunksOpen] = useState(false);

  const variant: BadgeVariant = STATUS_VARIANT[doc.latest_status] ?? "secondary";

  const onDelete = async () => {
    try {
      await del.mutateAsync(doc.doc_id);
      toast(`Delete queued for ${doc.filename}`, "info");
      setOpen(false);
    } catch (e) {
      toast(
        e instanceof ApiError ? `Delete failed: ${e.status}` : "Delete failed",
        "error",
      );
    }
  };

  return (
    <tr className="border-b last:border-b-0 hover:bg-muted/40">
      <td className="px-4 py-2 font-medium">{doc.filename}</td>
      <td className="px-4 py-2 text-xs text-muted-foreground">{doc.doc_id}</td>
      <td className="px-4 py-2">v{doc.latest_version}</td>
      <td className="px-4 py-2">
        <Badge variant={variant} title={doc.error ?? undefined}>
          {doc.latest_status}
        </Badge>
        {doc.error && (
          <p className="mt-1 max-w-[260px] truncate text-[10px] text-destructive"
             title={doc.error}>
            {doc.error}
          </p>
        )}
      </td>
      <td className="px-4 py-2">{doc.n_chunks ?? "—"}</td>
      <td className="px-4 py-2 text-xs text-muted-foreground">
        {doc.updated_at ? new Date(doc.updated_at).toLocaleString() : "—"}
      </td>
      <td className="px-4 py-2 text-right">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setChunksOpen(true)}
          aria-label="View chunks"
          title="View parsed chunks"
          disabled={doc.latest_status !== "done" || (doc.n_chunks ?? 0) === 0}
        >
          <Eye className="h-4 w-4" />
        </Button>
        <Dialog open={open} onOpenChange={setOpen}>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setOpen(true)}
            aria-label="Delete"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete {doc.filename}?</DialogTitle>
              <DialogDescription>
                This will <b>permanently</b> remove the document from:
                <ul className="ml-5 mt-2 list-disc">
                  <li>vector index (Milvus, all versions)</li>
                  <li>object storage (MinIO)</li>
                  <li>retrieval cache &amp; task records</li>
                </ul>
                The action cannot be undone.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={onDelete}
                disabled={del.isPending}
              >
                {del.isPending ? "Queuing…" : "Delete"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <ChunksDialog
          docId={chunksOpen ? doc.doc_id : null}
          filename={doc.filename}
          open={chunksOpen}
          onOpenChange={setChunksOpen}
        />
      </td>
    </tr>
  );
}

/** Inspect what the parser actually captured for a document. Loads chunks
 *  on demand (only when the dialog is open) so the page list isn't slowed
 *  down by N concurrent fetches. */
function ChunksDialog({
  docId, filename, open, onOpenChange,
}: {
  docId: string | null;
  filename: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const q = useDocChunks(docId);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Chunks of {filename}</DialogTitle>
          <DialogDescription>
            What the parser captured. If text you can see in the original file
            is missing here, the issue is at parsing — not retrieval.
          </DialogDescription>
        </DialogHeader>
        {q.isLoading && (
          <p className="py-6 text-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
            Loading…
          </p>
        )}
        {q.isError && (
          <p className="text-sm text-destructive">Failed to load chunks.</p>
        )}
        {q.data && (
          <div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
            <p className="text-xs text-muted-foreground">
              {q.data.n_chunks} chunk(s) total
            </p>
            {q.data.chunks.map((c) => (
              <div
                key={c.chunk_id}
                className="rounded-md border bg-muted/30 p-3 text-xs"
              >
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {c.chunk_id}
                    {c.page != null && ` · page ${c.page}`}
                  </span>
                  {c.breadcrumbs.length > 0 && (
                    <span className="ml-2 truncate text-[10px] text-muted-foreground">
                      {c.breadcrumbs.join(" › ")}
                    </span>
                  )}
                </div>
                {(c.prev_chunk_id || c.next_chunk_id) && (
                  <div className="mb-1.5 flex gap-3 text-[10px] text-muted-foreground">
                    <span>← {c.prev_chunk_id ? "prev linked" : "—"}</span>
                    <span>{c.next_chunk_id ? "next linked" : "—"} →</span>
                  </div>
                )}
                <pre className="whitespace-pre-wrap break-words font-sans text-xs leading-relaxed">
                  {c.text}
                </pre>
              </div>
            ))}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
