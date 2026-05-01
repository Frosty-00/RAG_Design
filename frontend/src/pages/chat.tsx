import { Send, Square, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { CitationPanel } from "@/components/chat/citation-panel";
import { MessageList } from "@/components/chat/message-list";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { useChatStream } from "@/hooks/use-chat-stream";
import type { Citation } from "@/lib/types";

export default function ChatPage() {
  const [draft, setDraft] = useState("");
  const { sessionId, messages, send, stop, reset, streaming } = useChatStream();
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  // The latest assistant message's citations drive the right panel.
  const latestCitations: Citation[] =
    [...messages].reverse().find((m) => m.role === "assistant" && m.citations)
      ?.citations ?? [];

  useEffect(() => { taRef.current?.focus(); }, []);

  const submit = async () => {
    const q = draft.trim();
    if (!q) return;
    setDraft("");
    await send(q);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  };

  const onCitationClick = (c: Citation) => {
    document.getElementById(`citation-${c.index}`)?.scrollIntoView({
      behavior: "smooth", block: "center",
    });
  };

  const newSession = () => {
    reset(); // store handles new session id + clears messages
  };

  return (
    <div
      className="grid gap-4 lg:grid-cols-[1fr_320px]"
      style={{ minHeight: "calc(100vh - 12rem)" }}
    >
      <Card className="flex flex-col">
        <div className="flex items-center justify-between border-b px-4 py-2">
          <p className="text-xs text-muted-foreground">
            session <code className="font-mono">{sessionId.slice(0, 8)}</code>
          </p>
          <Button
            variant="ghost" size="sm" onClick={newSession} disabled={streaming}
          >
            <Trash2 className="mr-2 h-3 w-3" /> New chat
          </Button>
        </div>
        <CardContent className="flex flex-1 flex-col p-0">
          <div className="flex-1 overflow-y-auto p-4">
            <MessageList messages={messages} onCitationClick={onCitationClick} />
          </div>
          <div className="border-t p-3">
            <div className="flex gap-2">
              <Textarea
                ref={taRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                placeholder="Ask anything (Shift+Enter for newline)…"
                className="min-h-[44px] resize-none"
                disabled={streaming}
              />
              {streaming ? (
                <Button
                  onClick={stop} variant="destructive" size="icon" title="Stop"
                >
                  <Square className="h-4 w-4" />
                </Button>
              ) : (
                <Button
                  onClick={submit} disabled={!draft.trim()} size="icon"
                  title="Send"
                >
                  <Send className="h-4 w-4" />
                </Button>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      <CitationPanel citations={latestCitations} />
    </div>
  );
}
