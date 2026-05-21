import { Loader2, Play } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/toast";
import { useEvalDatasets, useStartEval } from "@/hooks/use-eval";
import { ApiError } from "@/lib/api";

export function StartRunForm() {
  const start = useStartEval();
  const datasets = useEvalDatasets();
  const [dataset, setDataset] = useState<string>("");
  const [mode, setMode] = useState<"retrieval_only" | "full">("full");
  const [limit, setLimit] = useState<string>("");

  // Keep the selected dataset in sync with what actually exists on disk.
  // Two cases handled:
  //   * first load → no selection yet → pick the first available
  //   * stale selection → user previously chose foo.jsonl, then deleted /
  //     regenerated it; current state still points at the deleted path and
  //     submitting would 404 on the server side. Reset to the newest
  //     available file (datasets are returned newest-first by the API).
  useEffect(() => {
    if (!datasets.data || datasets.data.length === 0) return;
    const stillExists = datasets.data.some((d) => d.path === dataset);
    if (!dataset || !stillExists) {
      setDataset(datasets.data[0].path);
    }
  }, [datasets.data, dataset]);

  const submit = async () => {
    try {
      const body = {
        dataset,
        mode,
        ...(limit ? { limit: Number(limit) } : {}),
      };
      const res = await start.mutateAsync(body);
      toast(`Eval started: ${res.run_id}`, "success");
    } catch (e) {
      // Surface backend detail (e.g. "dataset_not_found") so the user can
      // act — generic "Eval failed to start" alone is uninformative.
      let msg = (e as Error)?.message ?? "?";
      if (e instanceof ApiError) {
        const detail =
          typeof e.body === "object" && e.body && "detail" in e.body
            ? String((e.body as { detail: unknown }).detail)
            : null;
        msg = detail ? `${e.status} ${detail}` : `HTTP ${e.status}`;
      }
      toast(`Eval failed to start: ${msg}`, "error");
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <Label htmlFor="dataset" className="text-xs">Dataset</Label>
        {datasets.isLoading ? (
          <Input disabled placeholder="loading…" />
        ) : datasets.data && datasets.data.length > 0 ? (
          <select
            id="dataset"
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
          >
            {datasets.data.map((d) => (
              <option key={d.path} value={d.path}>
                {d.name} ({d.samples} samples)
              </option>
            ))}
          </select>
        ) : (
          <Input
            id="dataset"
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            placeholder="No datasets — generate one from the panel below"
          />
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label className="text-xs">Mode</Label>
          <select
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            value={mode}
            onChange={(e) =>
              setMode(e.target.value as "retrieval_only" | "full")
            }
          >
            <option value="full">full (retrieval + judge)</option>
            <option value="retrieval_only">retrieval_only</option>
          </select>
        </div>
        <div>
          <Label htmlFor="limit" className="text-xs">Limit (optional)</Label>
          <Input
            id="limit"
            type="number"
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
            placeholder="all"
          />
        </div>
      </div>
      <Button
        onClick={submit}
        disabled={start.isPending || !dataset}
        className="w-full"
      >
        {start.isPending ? (
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        ) : (
          <Play className="mr-2 h-4 w-4" />
        )}
        {start.isPending ? "Starting…" : "Start evaluation"}
      </Button>
    </div>
  );
}
