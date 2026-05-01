/** Synthesize an eval dataset from already-indexed Milvus chunks.
 *  Calls POST /api/v1/eval/datasets/generate; result auto-appears in the
 *  Start-evaluation dataset dropdown via React Query invalidation.
 *
 *  Anti-mistake design:
 *    - default output is fixed `synthetic-latest.jsonl` — every generate
 *      overwrites it, so an accidental double-click never accumulates
 *      near-duplicate datasets.
 *    - confirm dialog before generation, with a clear "WILL OVERWRITE"
 *      banner if the chosen filename already exists on disk.
 *    - result toast breaks down samples per source doc so you know what
 *      the dataset actually contains.
 */
import { Loader2, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/toast";
import { useDocuments } from "@/hooks/use-documents";
import { useEvalDatasets, useGenerateDataset } from "@/hooks/use-eval";

const DEFAULT_NAME = "synthetic-latest.jsonl";

export function GenerateDatasetForm() {
  const docs = useDocuments();
  const datasets = useEvalDatasets();
  const gen = useGenerateDataset();

  const [count, setCount] = useState("10");
  const [outputName, setOutputName] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);

  const doneDocs = (docs.data ?? []).filter(
    (d) => d.latest_status === "done",
  );

  const effectiveName = outputName.trim() || DEFAULT_NAME;

  const willOverwrite = useMemo(() => {
    if (!datasets.data) return false;
    return datasets.data.some((d) => d.name === effectiveName);
  }, [datasets.data, effectiveName]);

  const docFilenameById: Record<string, string> = useMemo(() => {
    const m: Record<string, string> = {};
    for (const d of doneDocs) m[d.doc_id] = d.filename;
    return m;
  }, [doneDocs]);

  const toggleDoc = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const openConfirm = () => {
    const n = Number(count);
    if (!n || n < 1) {
      toast("Count must be ≥ 1", "error");
      return;
    }
    setConfirmOpen(true);
  };

  const doGenerate = async () => {
    setConfirmOpen(false);
    const n = Number(count);
    try {
      const res = await gen.mutateAsync({
        count: n,
        doc_ids: selected.size > 0 ? [...selected] : null,
        output_name: outputName.trim() || null,
      });
      const breakdown = Object.entries(res.per_doc)
        .map(([id, c]) =>
          `${docFilenameById[id]?.slice(0, 24) ?? id}: ${c}`,
        )
        .join(" · ");
      toast(
        `${res.path.split("/").pop()}: ${res.samples} samples` +
          (breakdown ? ` (${breakdown})` : "") +
          (res.failures > 0 ? ` · ${res.failures} failed` : "") +
          (res.overwrote_existing ? " · overwrote" : ""),
        "success",
      );
    } catch (e) {
      toast(`Generate failed: ${(e as Error)?.message ?? "?"}`, "error");
    }
  };

  return (
    <>
      <div className="space-y-3">
        <div>
          <Label htmlFor="count" className="text-xs">
            Number of (Q, A) pairs
          </Label>
          <Input
            id="count"
            type="number"
            value={count}
            onChange={(e) => setCount(e.target.value)}
            min={1}
            max={200}
          />
        </div>

        <div>
          <Label htmlFor="outname" className="text-xs">
            Output file name (default: <code>{DEFAULT_NAME}</code>)
          </Label>
          <Input
            id="outname"
            value={outputName}
            onChange={(e) => setOutputName(e.target.value)}
            placeholder={DEFAULT_NAME}
          />
        </div>

        <div>
          <Label className="text-xs">
            Source documents (leave empty = all)
          </Label>
          {doneDocs.length === 0 ? (
            <p className="rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground">
              Upload documents first.
            </p>
          ) : (
            <div className="max-h-32 space-y-1 overflow-y-auto rounded-md border p-2 text-xs">
              {doneDocs.map((d) => (
                <label
                  key={d.doc_id}
                  className="flex cursor-pointer items-center gap-2"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(d.doc_id)}
                    onChange={() => toggleDoc(d.doc_id)}
                  />
                  <span className="truncate" title={d.filename}>
                    {d.filename}
                  </span>
                  <span className="text-muted-foreground">
                    ({d.n_chunks ?? 0})
                  </span>
                </label>
              ))}
            </div>
          )}
        </div>

        <Button
          onClick={openConfirm}
          disabled={gen.isPending || doneDocs.length === 0}
          className="w-full"
          variant="secondary"
        >
          {gen.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Sparkles className="mr-2 h-4 w-4" />
          )}
          {gen.isPending ? "Generating (uses LLM)…" : "Generate dataset"}
        </Button>

        <p className="text-[11px] text-muted-foreground">
          Each sample = one LLM call. Output overwrites the same file by
          default — accidental double-click won't pile up duplicate datasets.
        </p>
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Generate dataset?</DialogTitle>
            <DialogDescription asChild>
              <div>
                <p>
                  Will write <code>{count}</code> (Q, A) pair(s) sampled from{" "}
                  {selected.size > 0
                    ? `${selected.size} selected document(s)`
                    : `all ${doneDocs.length} document(s)`}{" "}
                  to <code className="font-mono">eval/datasets/{effectiveName}</code>.
                </p>
                {willOverwrite && (
                  <p className="mt-2 rounded-md border border-amber-300 bg-amber-50 p-2 text-amber-900">
                    ⚠ <strong>{effectiveName}</strong> already exists — it will
                    be <strong>overwritten</strong>. Save a copy under a
                    different name first if you want to keep it.
                  </p>
                )}
              </div>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              variant={willOverwrite ? "destructive" : "default"}
              onClick={doGenerate}
            >
              {willOverwrite ? "Overwrite" : "Generate"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
