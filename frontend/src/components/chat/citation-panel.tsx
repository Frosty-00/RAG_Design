import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Citation } from "@/lib/types";

export function CitationPanel({ citations }: { citations: Citation[] }) {
  if (!citations || citations.length === 0) {
    return (
      <Card className="h-full">
        <CardHeader>
          <CardTitle className="text-sm">References</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted-foreground">
            Send a message to see citations here.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="h-full overflow-y-auto">
      <CardHeader>
        <CardTitle className="text-sm">References ({citations.length})</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {citations.map((c) => (
          <div
            key={c.chunk_id}
            id={`citation-${c.index}`}
            className="rounded-md border border-input p-3 text-xs"
          >
            <div className="mb-1 flex items-center justify-between">
              <span className="font-mono text-muted-foreground">
                [{c.index}] {c.doc_id}
                {c.page != null && ` · p.${c.page}`}
              </span>
            </div>
            {c.breadcrumbs.length > 0 && (
              <p className="mb-1 truncate text-[10px] text-muted-foreground">
                {c.breadcrumbs.join(" › ")}
              </p>
            )}
            <p className="whitespace-pre-wrap leading-relaxed">{c.text_preview}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
