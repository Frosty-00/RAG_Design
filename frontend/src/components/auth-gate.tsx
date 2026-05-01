/** Sign-in screen: prompts the user for an admin/user bearer token, stores
 *  it in localStorage, and renders children once present.
 *
 *  Backend tokens are issued via `POST /api/v1/admin/tokens` (admin only).
 *  For first-run, paste the ADMIN_TOKEN from the .env file directly.
 */
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { getToken, setToken } from "@/lib/auth";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [token, setLocal] = useState<string | null>(getToken());
  const [draft, setDraft] = useState("");

  if (token) return <>{children}</>;

  return (
    <div className="grid h-full place-items-center bg-muted/30 p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>self-rag — sign in</CardTitle>
          <CardDescription>
            Paste your bearer token. First-run: use <code>ADMIN_TOKEN</code> from{" "}
            <code>.env</code> to issue user tokens via{" "}
            <code>POST /api/v1/admin/tokens</code>.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="bearer token"
            autoFocus
          />
          <Button
            disabled={!draft.trim()}
            onClick={() => {
              setToken(draft.trim());
              setLocal(draft.trim());
            }}
            className="w-full"
          >
            Sign in
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
