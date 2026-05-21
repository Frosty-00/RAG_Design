/** Sign-in screen: prompts the user for an admin/user bearer token, stores
 *  it in localStorage, and renders children once present.
 *
 *  Backend tokens are issued via `POST /api/v1/admin/tokens` (admin only).
 *  For first-run, paste the ADMIN_TOKEN from the .env file directly.
 */
import { LogIn } from "lucide-react";
import { useState } from "react";

import { BrandMark } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getToken, setToken } from "@/lib/auth";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [token, setLocal] = useState<string | null>(getToken());
  const [draft, setDraft] = useState("");

  if (token) return <>{children}</>;

  const submit = () => {
    if (!draft.trim()) return;
    setToken(draft.trim());
    setLocal(draft.trim());
  };

  return (
    <div className="grid h-full place-items-center bg-gradient-to-br from-background via-background to-primary/5 p-6">
      <div className="w-full max-w-md">
        {/* Brand lockup above the card — keeps the sign-in panel focused
            on the action while still introducing the product. */}
        <div className="mb-6 flex flex-col items-center">
          <BrandMark size={48} className="mb-3" />
          <h1 className="text-xl font-semibold tracking-tight">
            self<span className="text-primary">·</span>rag
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Enterprise knowledge-base RAG
          </p>
        </div>

        <div className="rounded-xl border bg-card p-6 shadow-sm">
          <h2 className="mb-1 text-base font-semibold">Sign in</h2>
          <p className="mb-4 text-xs text-muted-foreground">
            Paste your bearer token. First run? Use{" "}
            <code className="rounded bg-muted px-1 py-0.5">admin-dev-token</code>{" "}
            (default ADMIN_TOKEN in <code>.env</code>).
          </p>
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="bearer token"
            autoFocus
          />
          <Button
            disabled={!draft.trim()}
            onClick={submit}
            className="mt-3 w-full"
          >
            <LogIn className="mr-2 h-4 w-4" />
            Sign in
          </Button>
        </div>

        <p className="mt-4 text-center text-[10px] text-muted-foreground">
          Tokens stored in localStorage on this device only.
        </p>
      </div>
    </div>
  );
}
