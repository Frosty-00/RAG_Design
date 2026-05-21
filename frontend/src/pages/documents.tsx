import { FileUp, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useMemo } from "react";

import { DocumentRow, InflightRow } from "@/components/documents/document-row";
import { UploadDropzone } from "@/components/documents/upload-dropzone";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { useDocuments } from "@/hooks/use-documents";
import { useUploadsStore } from "@/stores/uploads";

export default function DocumentsPage() {
  const docs = useDocuments();
  const inflight = useUploadsStore((s) => s.items);
  const reconcile = useUploadsStore((s) => s.reconcile);

  // Whenever the server returns a fresh list, retire optimistic rows whose
  // real server-side counterpart has arrived.
  useEffect(() => {
    if (!docs.data) return;
    const serverFilenames = new Set(docs.data.map((d) => d.filename));
    reconcile(serverFilenames);
  }, [docs.data, reconcile]);

  // Suppress the optimistic "uploading" row once the server row is visible
  // (avoids a 1-second double row right after upload succeeds).
  const visibleInflight = useMemo(() => {
    const serverFilenames = new Set((docs.data ?? []).map((d) => d.filename));
    return inflight.filter(
      (it) => it.status === "upload_failed" || !serverFilenames.has(it.filename),
    );
  }, [inflight, docs.data]);

  const totalRows = (docs.data?.length ?? 0) + visibleInflight.length;

  return (
    <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
      <Card>
        <CardHeader>
          <CardTitle>Upload</CardTitle>
          <CardDescription>
            Files are hashed; re-uploading the same content reuses the prior
            version (no duplicate ingest).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <UploadDropzone />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>Documents</CardTitle>
            <CardDescription>
              {docs.data?.length ?? 0} indexed
              {visibleInflight.length > 0 &&
                ` · ${visibleInflight.length} in flight`}
              {" "}· status updates every ~1.5s while pending.
            </CardDescription>
          </div>
          <Button
            variant="ghost" size="icon"
            onClick={() => docs.refetch()}
            aria-label="Refresh"
          >
            {docs.isFetching ? <Loader2 className="h-4 w-4 animate-spin" />
                             : <RefreshCw className="h-4 w-4" />}
          </Button>
        </CardHeader>
        <CardContent>
          {docs.isError && (
            <p className="text-sm text-destructive">Failed to load documents.</p>
          )}
          {totalRows === 0 && (
            <EmptyState
              icon={FileUp}
              title="Your library is empty"
              description={
                <>
                  Upload your first document to start building a smart
                  knowledge base. Supported formats include PDF, DOCX,
                  Markdown, HTML, XLSX, and images (OCR).
                </>
              }
            />
          )}
          {totalRows > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-muted-foreground">
                  <tr className="border-b">
                    <th className="px-4 py-2">Filename</th>
                    <th className="px-4 py-2">Owner</th>
                    <th className="px-4 py-2">Access</th>
                    <th className="px-4 py-2">Ver</th>
                    <th className="px-4 py-2">Status</th>
                    <th className="px-4 py-2">Chunks</th>
                    <th className="px-4 py-2">Updated</th>
                    <th className="px-4 py-2 text-right"></th>
                  </tr>
                </thead>
                <tbody>
                  {/* Optimistic / failed rows on top so they're impossible to
                      miss while real rows are still spinning up. */}
                  {visibleInflight.map((it) => (
                    <InflightRow
                      key={it.tempId}
                      tempId={it.tempId}
                      filename={it.filename}
                      status={it.status}
                      error={it.error}
                    />
                  ))}
                  {(docs.data ?? []).map((d) => (
                    <DocumentRow key={d.doc_id} doc={d} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
