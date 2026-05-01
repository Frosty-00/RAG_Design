/** Chat streaming hook (state lives in `useChatStore`).
 *
 * Owns:
 *   - one in-flight POST /api/v1/chat per session
 *   - parsed SSE event reducer → updates store messages
 *   - AbortController for stop()
 *
 * Re-exports ChatMessage type from the store so existing imports keep working.
 */
import { useCallback, useRef } from "react";

import { toast } from "@/components/ui/toast";
import { ApiError, streamPost } from "@/lib/api";
import { readSSE } from "@/lib/sse";
import type { ChatEvent } from "@/lib/types";
import { useChatStore } from "@/stores/chat";
import type { ChatMessage } from "@/stores/chat";

export type { ChatMessage };

export function useChatStream() {
  const sessionId = useChatStore((s) => s.sessionId);
  const messages = useChatStore((s) => s.messages);
  const streaming = useChatStore((s) => s.streaming);
  const appendMessage = useChatStore((s) => s.appendMessage);
  const updateMessage = useChatStore((s) => s.updateMessage);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const resetSession = useChatStore((s) => s.resetSession);

  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(async (query: string) => {
    if (!query.trim() || streaming) return;

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(), role: "user", content: query,
    };
    const assistantId = crypto.randomUUID();
    const assistantMsg: ChatMessage = {
      id: assistantId, role: "assistant", content: "", phase: "accepted",
    };
    appendMessage(userMsg);
    appendMessage(assistantMsg);
    setStreaming(true);

    const ac = new AbortController();
    abortRef.current = ac;

    try {
      const resp = await streamPost("/api/v1/chat", {
        query, session_id: sessionId,
      });

      for await (const raw of readSSE(resp, ac.signal)) {
        let data: ChatEvent;
        try { data = JSON.parse(raw.data) as ChatEvent; }
        catch { continue; }

        if (data.event === "ack") {
          updateMessage(assistantId, { phase: data.phase ?? undefined });
        } else if (data.event === "token" && data.token) {
          // Append token to whatever's currently in the store
          const cur = useChatStore.getState().messages.find((m) => m.id === assistantId);
          updateMessage(assistantId, {
            content: (cur?.content ?? "") + data.token,
            phase: "generating",
          });
        } else if (data.event === "citations") {
          updateMessage(assistantId, {
            citations: data.citations ?? [],
            meta: data.meta,
            phase: "done",
          });
        } else if (data.event === "error") {
          updateMessage(assistantId, {
            phase: "error", error: data.error ?? "unknown error",
          });
        }
      }
    } catch (e) {
      if (ac.signal.aborted) {
        toast("Streaming aborted", "info");
      } else if (e instanceof ApiError) {
        toast(`Chat failed: HTTP ${e.status}`, "error");
        updateMessage(assistantId, {
          phase: "error", error: `HTTP ${e.status}`,
        });
      } else {
        toast("Chat failed: network error", "error");
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }, [sessionId, streaming, appendMessage, updateMessage, setStreaming]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    resetSession();
  }, [resetSession]);

  return { sessionId, messages, send, stop, reset, streaming };
}
