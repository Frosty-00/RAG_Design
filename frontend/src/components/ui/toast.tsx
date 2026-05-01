/** Tiny toast: zustand-backed list + a portal-style viewport, no @radix.
 *  Auto-dismiss after 4s. Used for upload / delete / eval feedback. */
import { useEffect } from "react";
import { create } from "zustand";

import { cn } from "@/lib/utils";

type ToastKind = "success" | "error" | "info";

interface ToastItem { id: string; message: string; kind: ToastKind }

interface ToastStore {
  items: ToastItem[];
  push: (msg: string, kind?: ToastKind) => void;
  remove: (id: string) => void;
}

export const useToastStore = create<ToastStore>((set) => ({
  items: [],
  push: (message, kind = "info") => {
    const id = crypto.randomUUID();
    set((s) => ({ items: [...s.items, { id, message, kind }] }));
    setTimeout(
      () => set((s) => ({ items: s.items.filter((x) => x.id !== id) })),
      4000,
    );
  },
  remove: (id) => set((s) => ({ items: s.items.filter((x) => x.id !== id) })),
}));

export function toast(message: string, kind: ToastKind = "info") {
  useToastStore.getState().push(message, kind);
}

export function ToastViewport() {
  const items = useToastStore((s) => s.items);
  const remove = useToastStore((s) => s.remove);

  return (
    <div className="pointer-events-none fixed right-4 top-4 z-50 flex flex-col items-end gap-2">
      {items.map((t) => (
        <ToastCard key={t.id} item={t} onDismiss={() => remove(t.id)} />
      ))}
    </div>
  );
}

function ToastCard({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  useEffect(() => {
    const id = setTimeout(onDismiss, 4000);
    return () => clearTimeout(id);
  }, [onDismiss]);

  return (
    <div
      role="status"
      className={cn(
        "pointer-events-auto min-w-[240px] max-w-md rounded-md px-4 py-2 text-sm shadow-lg",
        item.kind === "success" && "bg-emerald-600 text-white",
        item.kind === "error" && "bg-rose-600 text-white",
        item.kind === "info" && "bg-slate-800 text-white",
      )}
    >
      {item.message}
    </div>
  );
}
