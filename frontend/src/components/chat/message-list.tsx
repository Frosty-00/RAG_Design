import { Sparkles } from "lucide-react";
import { useEffect, useRef } from "react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import type { Citation } from "@/lib/types";
import { cn } from "@/lib/utils";

import type { ChatMessage } from "@/hooks/use-chat-stream";

interface Props {
  messages: ChatMessage[];
  onCitationClick?: (c: Citation) => void;
}

export function MessageList({ messages, onCitationClick }: Props) {
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <EmptyState
        icon={Sparkles}
        title="Ask anything"
        description={
          <>
            Your knowledge base is ready. Try a question — every answer
            comes with citations you can click through to the source.
          </>
        }
        className="h-full"
      />
    );
  }

  return (
    <div className="space-y-4">
      {messages.map((m) => (
        <div
          key={m.id}
          className={cn(
            "flex",
            m.role === "user" ? "justify-end" : "justify-start",
          )}
        >
          <div
            className={cn(
              "max-w-[80%] rounded-lg px-4 py-2 text-sm",
              m.role === "user"
                ? "bg-primary text-primary-foreground"
                : "border bg-card",
            )}
          >
            {m.role === "assistant" && m.phase && m.phase !== "done" && (
              <PhaseBadge phase={m.phase} />
            )}
            <p className="whitespace-pre-wrap leading-relaxed">
              {renderWithCitations(m.content, m.citations ?? [], onCitationClick)}
              {m.phase === "generating" && !m.content && (
                <span className="text-muted-foreground">…thinking</span>
              )}
              {m.phase === "generating" && m.content && (
                <span className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-muted-foreground" />
              )}
            </p>
            {m.error && (
              <p className="mt-1 text-xs text-destructive">{m.error}</p>
            )}
          </div>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

function PhaseBadge({ phase }: { phase: ChatMessage["phase"] }) {
  if (!phase || phase === "done") return null;
  const labels: Record<string, string> = {
    accepted: "Accepted",
    retrieving: "Retrieving",
    generating: "Generating",
    error: "Error",
  };
  return (
    <Badge
      variant={phase === "error" ? "destructive" : "secondary"}
      className="mb-1.5 text-[10px]"
    >
      {labels[phase] ?? phase}
    </Badge>
  );
}

/** Replace `[N]` markers with clickable badges that scroll the citation
 *  panel to the matching entry. */
function renderWithCitations(
  text: string,
  citations: Citation[],
  onClick?: (c: Citation) => void,
) {
  if (!citations.length) return text;
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((p, i) => {
    const m = /^\[(\d+)\]$/.exec(p);
    if (!m) return p;
    const idx = Number(m[1]);
    const c = citations.find((x) => x.index === idx);
    if (!c) return p;
    return (
      <button
        key={`${i}-${idx}`}
        onClick={() => onClick?.(c)}
        className="mx-0.5 inline-flex h-4 min-w-[1rem] items-center justify-center rounded bg-emerald-100 px-1 text-[10px] font-bold text-emerald-700 hover:bg-emerald-200"
        title={`${c.doc_id} · ${c.text_preview.slice(0, 80)}…`}
      >
        {idx}
      </button>
    );
  });
}
