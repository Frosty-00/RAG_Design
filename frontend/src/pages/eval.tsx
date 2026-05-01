import { Loader2, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";

import { DatasetsTable } from "@/components/eval/datasets-table";
import { DiffView } from "@/components/eval/diff-view";
import { GenerateDatasetForm } from "@/components/eval/generate-dataset-form";
import { StartRunForm } from "@/components/eval/start-run-form";
import { Badge } from "@/components/ui/badge";
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
import {
  useDeleteEvalRun,
  useDiffRuns,
  useEvalRuns,
} from "@/hooks/use-eval";
import type { EvalRunMeta } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_VARIANT: Record<
  string,
  "default" | "secondary" | "destructive" | "success" | "warning"
> = {
  done: "success",
  running: "warning",
  pending: "secondary",
  failed: "destructive",
};

export default function EvalPage() {
  const runs = useEvalRuns();
  const diff = useDiffRuns();
  const del = useDeleteEvalRun();
  const [selected, setSelected] = useState<string[]>([]);
  const [diffOpen, setDiffOpen] = useState(false);
  const [confirm, setConfirm] = useState<EvalRunMeta | null>(null);

  const toggle = (rid: string) => {
    setSelected((prev) => {
      if (prev.includes(rid)) return prev.filter((x) => x !== rid);
      if (prev.length >= 2) return [prev[1], rid];
      return [...prev, rid];
    });
  };

  const runDiff = async () => {
    if (selected.length !== 2) {
      toast("Select exactly 2 runs to diff", "info");
      return;
    }
    try {
      await diff.mutateAsync({
        baseline_id: selected[0], candidate_id: selected[1],
      });
      setDiffOpen(true);
    } catch (e) {
      toast(`Diff failed: ${(e as Error)?.message ?? "?"}`, "error");
    }
  };

  const onConfirmDelete = async () => {
    if (!confirm) return;
    const id = confirm.run_id;
    try {
      const res = await del.mutateAsync(id);
      setSelected((prev) => prev.filter((x) => x !== id));
      toast(`Deleted ${id} (${res.deleted_files.length} file(s))`, "success");
      setConfirm(null);
    } catch (e) {
      toast(`Delete failed: ${(e as Error)?.message ?? "?"}`, "error");
    }
  };

  return (
    <>
      <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>New evaluation</CardTitle>
              <CardDescription>
                Admin token required. Reports persist on disk + Redis index.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <StartRunForm />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Generate dataset</CardTitle>
              <CardDescription>
                Synthesize (Q, A) pairs from chunks of your already-uploaded
                documents — so evaluation actually tests your real corpus.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <GenerateDatasetForm />
            </CardContent>
          </Card>

          <DatasetsTable />
        </div>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle>Run history</CardTitle>
              <CardDescription>
                {runs.data?.length ?? 0} runs · select two rows to diff
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline" size="sm"
                disabled={selected.length !== 2 || diff.isPending}
                onClick={runDiff}
              >
                {diff.isPending && (
                  <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                )}
                Diff selected
              </Button>
              <Button
                variant="ghost" size="icon"
                onClick={() => runs.refetch()}
                aria-label="Refresh"
              >
                {runs.isFetching
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <RefreshCw className="h-4 w-4" />}
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {runs.isError && (
              <p className="text-sm text-destructive">Failed to load runs.</p>
            )}
            {runs.data && runs.data.length === 0 && (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No runs yet. Start one on the left.
              </p>
            )}
            {runs.data && runs.data.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs uppercase text-muted-foreground">
                    <tr className="border-b">
                      <th className="w-8"></th>
                      <th className="px-3 py-2">Run ID</th>
                      <th className="px-3 py-2">Status</th>
                      <th className="px-3 py-2">Mode</th>
                      <th className="px-3 py-2">Samples</th>
                      <th className="px-3 py-2">hit@5</th>
                      <th className="px-3 py-2">faithful</th>
                      <th className="px-3 py-2">started</th>
                      <th className="w-8"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.data.map((r) => {
                      const checked = selected.includes(r.run_id);
                      const v = STATUS_VARIANT[r.status] ?? "secondary";
                      return (
                        <tr
                          key={r.run_id}
                          className={cn(
                            "border-b hover:bg-muted/40",
                            checked && "bg-emerald-50",
                          )}
                        >
                          <td
                            className="cursor-pointer px-3 py-2"
                            onClick={() => toggle(r.run_id)}
                          >
                            <input
                              type="checkbox" readOnly checked={checked}
                            />
                          </td>
                          <td
                            className="cursor-pointer px-3 py-2 font-mono text-xs"
                            onClick={() => toggle(r.run_id)}
                          >
                            {r.run_id}
                          </td>
                          <td className="px-3 py-2">
                            <Badge variant={v}>{r.status}</Badge>
                          </td>
                          <td className="px-3 py-2">{r.mode ?? "—"}</td>
                          <td className="px-3 py-2 tabular-nums">
                            {r.n_samples ?? "—"}
                          </td>
                          <td className="px-3 py-2 tabular-nums">
                            {r.metrics?.hit_at_5?.toFixed(3) ?? "—"}
                          </td>
                          <td className="px-3 py-2 tabular-nums">
                            {r.metrics?.faithfulness?.toFixed(3) ?? "—"}
                          </td>
                          <td className="px-3 py-2 text-xs text-muted-foreground">
                            {r.started_at ?? "—"}
                          </td>
                          <td className="px-3 py-2 text-right">
                            <Button
                              variant="ghost" size="icon"
                              onClick={(e) => {
                                e.stopPropagation();
                                setConfirm(r);
                              }}
                              title="Delete run"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <DiffView
        open={diffOpen}
        onOpenChange={setDiffOpen}
        diff={diff.data ?? null}
        loading={diff.isPending}
      />

      {/* Delete confirmation */}
      <Dialog
        open={confirm !== null}
        onOpenChange={(o) => !o && setConfirm(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete run?</DialogTitle>
            <DialogDescription>
              Permanently removes <code>{confirm?.run_id}</code>:
              <ul className="ml-5 mt-2 list-disc">
                <li>Redis index entry</li>
                <li>JSON + Markdown report files on disk</li>
              </ul>
              Cannot be undone.
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
