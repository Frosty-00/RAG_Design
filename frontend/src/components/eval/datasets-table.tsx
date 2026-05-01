/** Lists generated / built-in evaluation datasets with per-doc breakdown.
 *  Each row can be deleted (twice-confirmed) — the file is hard-removed
 *  from `eval/datasets/`. */
import { Loader2, RefreshCw, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/components/ui/toast";
import { useDocuments } from "@/hooks/use-documents";
import { useDeleteDataset, useEvalDatasets } from "@/hooks/use-eval";
import type { EvalDataset } from "@/lib/types";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function DatasetsTable() {
  const datasets = useEvalDatasets();
  const docs = useDocuments();
  const del = useDeleteDataset();
  const [confirm, setConfirm] = useState<EvalDataset | null>(null);

  const docFilenameById: Record<string, string> = useMemo(() => {
    const m: Record<string, string> = {};
    for (const d of docs.data ?? []) m[d.doc_id] = d.filename;
    return m;
  }, [docs.data]);

  const onConfirmDelete = async () => {
    if (!confirm) return;
    try {
      await del.mutateAsync(confirm.name);
      toast(`Deleted ${confirm.name}`, "success");
      setConfirm(null);
    } catch (e) {
      toast(`Delete failed: ${(e as Error)?.message ?? "?"}`, "error");
    }
  };

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>Datasets</CardTitle>
            <CardDescription>
              {datasets.data?.length ?? 0} on disk · breakdown shows which
              docs each was synthesized from
            </CardDescription>
          </div>
          <Button
            variant="ghost" size="icon"
            onClick={() => datasets.refetch()}
            aria-label="Refresh"
          >
            {datasets.isFetching
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <RefreshCw className="h-4 w-4" />}
          </Button>
        </CardHeader>
        <CardContent>
          {datasets.isError && (
            <p className="text-sm text-destructive">Failed to load datasets.</p>
          )}
          {datasets.data && datasets.data.length === 0 && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              No datasets yet. Use "Generate dataset" to create one.
            </p>
          )}
          {datasets.data && datasets.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-muted-foreground">
                  <tr className="border-b">
                    <th className="px-3 py-2">Name</th>
                    <th className="px-3 py-2">Samples</th>
                    <th className="px-3 py-2">Source breakdown</th>
                    <th className="px-3 py-2">Size</th>
                    <th className="px-3 py-2">Modified</th>
                    <th className="w-8"></th>
                  </tr>
                </thead>
                <tbody>
                  {datasets.data.map((d) => (
                    <tr key={d.path} className="border-b hover:bg-muted/40">
                      <td className="px-3 py-2 font-mono text-xs">
                        {d.name}
                        {d.preview_question && (
                          <p
                            className="mt-1 truncate text-[10px] text-muted-foreground"
                            title={d.preview_question}
                          >
                            e.g. {d.preview_question}
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-2 tabular-nums">{d.samples}</td>
                      <td className="px-3 py-2 text-xs">
                        {Object.entries(d.per_doc).length === 0 ? (
                          <span className="text-muted-foreground">—</span>
                        ) : (
                          <div className="space-y-0.5">
                            {Object.entries(d.per_doc).map(([id, n]) => (
                              <div key={id} className="flex gap-2">
                                <span
                                  className="max-w-[220px] truncate"
                                  title={docFilenameById[id] ?? id}
                                >
                                  {docFilenameById[id] ?? id}
                                </span>
                                <span className="text-muted-foreground">
                                  {n}
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">
                        {formatBytes(d.size_bytes)}
                      </td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">
                        {d.modified.replace("T", " ").replace("Z", "")}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Button
                          variant="ghost" size="icon"
                          onClick={() => setConfirm(d)}
                          title="Delete dataset"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={confirm !== null}
        onOpenChange={(o) => !o && setConfirm(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete dataset?</DialogTitle>
            <DialogDescription asChild>
              <div>
                <p>
                  Permanently removes <code>{confirm?.name}</code> ({" "}
                  {confirm?.samples} sample(s)) from disk. Past run results
                  that referenced this dataset are unaffected.
                </p>
                <p className="mt-2 text-amber-700">
                  Cannot be undone.
                </p>
              </div>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={onConfirmDelete}
              disabled={del.isPending}
            >
              {del.isPending ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
