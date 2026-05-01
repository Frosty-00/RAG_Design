/** Chat session store — survives across route changes.
 *
 * Previously messages lived in `useChatStream`'s local useState; switching
 * to /documents and back unmounted the component and the conversation was
 * lost. Now state lives here so navigating away and back keeps history.
 *
 * `sessionId` is also kept here (was previously in localStorage only —
 * still mirrored there so a full reload restores it).
 */
import { create } from "zustand";

import type { Citation } from "@/lib/types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  phase?: "accepted" | "retrieving" | "generating" | "done" | "error";
  meta?: Record<string, unknown>;
  error?: string;
}

const SESSION_KEY = "self-rag.session_id";

function loadSession(): string {
  let sid = localStorage.getItem(SESSION_KEY);
  if (!sid) {
    sid = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, sid);
  }
  return sid;
}

interface ChatStore {
  sessionId: string;
  messages: ChatMessage[];
  streaming: boolean;

  setStreaming: (v: boolean) => void;
  appendMessage: (m: ChatMessage) => void;
  updateMessage: (id: string, patch: Partial<ChatMessage>) => void;
  resetSession: () => void;
}

export const useChatStore = create<ChatStore>((set) => ({
  sessionId: loadSession(),
  messages: [],
  streaming: false,

  setStreaming: (v) => set({ streaming: v }),

  appendMessage: (m) =>
    set((s) => ({ messages: [...s.messages, m] })),

  updateMessage: (id, patch) =>
    set((s) => {
      const i = s.messages.findIndex((m) => m.id === id);
      if (i < 0) return s;
      const next = s.messages.slice();
      next[i] = { ...next[i], ...patch };
      return { messages: next };
    }),

  resetSession: () => {
    const sid = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, sid);
    set({ sessionId: sid, messages: [], streaming: false });
  },
}));
