import { Loader2, Search } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/toast";
import { api, ApiError } from "@/lib/api";
import type { DebugRetrieveResponse } from "@/lib/types";

export default function DebugPage() {
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(20);
  const [rerankK, setRerankK] = useState(5);
  const [multiQuery, setMultiQuery] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DebugRetrieveResponse | null>(null);

  const submit = async () => {
    if (!query.trim()) return;
    setBusy(true);
    try {
      const res = await api.post<DebugRetrieveResponse>(
        "/api/v1/debug/retrieve",
        { query, top_k: topK, rerank_k: rerankK, multi_query: multiQuery },
      );
      setResult(res);
    } catch (e) {
      toast(
        e instanceof ApiError ? `Debug failed: HTTP ${e.status}` : "Debug failed",
        "error",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Retrieval debug</CardTitle>
          <CardDescription>
            Dev-only endpoint. Inspects query understanding output and retrieval
            results without invoking the LLM.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void submit(); }}
            placeholder="Enter query…"
          />
          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label className="text-xs">top_k (hybrid)</Label>
              <Input
                type="number" value={topK}
                onChange={(e) => setTopK(Number(e.target.value))}
              />
            </div>
            <div>
              <Label className="text-xs">rerank_k</Label>
              <Input
                type="number" value={rerankK}
                onChange={(e) => setRerankK(Number(e.target.value))}
              />
            </div>
            <div className="flex items-end gap-2">
              <input
                id="multi-query"
                type="checkbox"
                checked={multiQuery}
                onChange={(e) => setMultiQuery(e.target.checked)}
              />
              <Label htmlFor="multi-query">Multi-Query</Label>
            </div>
          </div>
          <Button onClick={submit} disabled={busy || !query.trim()} className="w-full">
            {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  : <Search className="mr-2 h-4 w-4" />}
            Retrieve
          </Button>
        </CardContent>
      </Card>

      {result && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Query Understanding</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div>
                <Badge variant={result.understanding.intent === "chitchat" ? "secondary" : "default"}>
                  {result.understanding.intent}
                </Badge>
              </div>
              <div>
                <span className="text-xs text-muted-foreground">resolved: </span>
                <code className="text-xs">{result.understanding.resolved_query}</code>
              </div>
              {result.understanding.rewrites.length > 0 && (
                <div>
                  <span className="text-xs text-muted-foreground">rewrites:</span>
                  <ul className="ml-4 list-disc text-xs">
                    {result.understanding.rewrites.map((r, i) => (
                      <li key={i}><code>{r}</code></li>
                    ))}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Reranked chunks ({result.chunks.length})
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs uppercase text-muted-foreground">
                    <tr className="border-b">
                      <th className="px-2 py-1">#</th>
                      <th className="px-2 py-1">chunk_id</th>
                      <th className="px-2 py-1">doc_id</th>
                      <th className="px-2 py-1 text-right">score</th>
                      <th className="px-2 py-1">preview</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.chunks.map((c, i) => (
                      <tr key={c.chunk_id} className="border-b last:border-b-0 align-top">
                        <td className="px-2 py-2 tabular-nums">{i + 1}</td>
                        <td className="px-2 py-2 font-mono text-[10px]">{c.chunk_id}</td>
                        <td className="px-2 py-2">{c.doc_id}</td>
                        <td className="px-2 py-2 text-right tabular-nums">
                          {c.score.toFixed(3)}
                        </td>
                        <td className="px-2 py-2 text-xs text-muted-foreground">
                          {c.text_preview}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
