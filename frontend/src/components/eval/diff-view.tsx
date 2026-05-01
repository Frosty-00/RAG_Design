import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { EvalDiff } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  diff: EvalDiff | null;
  loading: boolean;
}

export function DiffView({ open, onOpenChange, diff, loading }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Run diff</DialogTitle>
          <DialogDescription>
            {diff
              ? `${diff.baseline_id.slice(0, 8)} → ${diff.candidate_id.slice(0, 8)}`
              : "Loading…"}
          </DialogDescription>
        </DialogHeader>

        {loading && <p className="text-sm text-muted-foreground">Computing diff…</p>}

        {diff && (
          <div className="space-y-4">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-muted-foreground">
                <tr className="border-b">
                  <th className="px-2 py-1">Metric</th>
                  <th className="px-2 py-1 text-right">Baseline</th>
                  <th className="px-2 py-1 text-right">Candidate</th>
                  <th className="px-2 py-1 text-right">Δ</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(diff.metric_diffs).map(([k, v]) => (
                  <tr key={k} className="border-b last:border-b-0">
                    <td className="px-2 py-1 font-mono text-xs">{k}</td>
                    <td className="px-2 py-1 text-right tabular-nums">
                      {v.baseline.toFixed(4)}
                    </td>
                    <td className="px-2 py-1 text-right tabular-nums">
                      {v.candidate.toFixed(4)}
                    </td>
                    <td
                      className={cn(
                        "px-2 py-1 text-right tabular-nums",
                        v.delta > 0 && "text-emerald-600",
                        v.delta < 0 && "text-rose-600",
                      )}
                    >
                      {v.delta >= 0 ? "+" : ""}
                      {v.delta.toFixed(4)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {Object.keys(diff.prompt_changes).length > 0 && (
              <div className="rounded-md border bg-muted/30 p-3 text-xs">
                <p className="mb-1 font-semibold">Prompt versions changed:</p>
                <ul className="ml-4 list-disc">
                  {Object.entries(diff.prompt_changes).map(([k, v]) => (
                    <li key={k}>
                      <code>{k}</code>: {String(v.baseline)} → {String(v.candidate)}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <BadCaseBlock title="Newly bad" ids={diff.newly_bad} tone="bad" />
              <BadCaseBlock title="Newly good" ids={diff.newly_good} tone="good" />
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function BadCaseBlock({
  title, ids, tone,
}: { title: string; ids: string[]; tone: "good" | "bad" }) {
  return (
    <div className={cn("rounded-md border p-3 text-xs", tone === "bad" && "bg-rose-50", tone === "good" && "bg-emerald-50")}>
      <p className="mb-1 font-semibold">
        {title} ({ids.length})
      </p>
      {ids.length === 0
        ? <p className="text-muted-foreground">—</p>
        : <ul className="ml-4 list-disc">{ids.slice(0, 12).map((id) => <li key={id}>{id}</li>)}</ul>}
    </div>
  );
}
