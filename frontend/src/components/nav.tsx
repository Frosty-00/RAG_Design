import { LogOut, Shield, User } from "lucide-react";
import { NavLink } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useMe } from "@/hooks/use-admin";
import { clearToken } from "@/lib/auth";
import { cn } from "@/lib/utils";

const baseLinks = [
  { to: "/chat", label: "Chat" },
  { to: "/documents", label: "Documents" },
  { to: "/eval", label: "Eval" },
];

export function Nav({ ready }: { ready: boolean }) {
  const me = useMe();
  // Admin link only when current user has admin role — the page itself
  // also re-checks via /admin/me, but hiding the link avoids 403 noise.
  const links = me.data?.is_admin
    ? [...baseLinks, { to: "/admin", label: "Admin" }]
    : baseLinks;

  // Debug page only registered in dev (see App.tsx)
  const finalLinks = import.meta.env.DEV
    ? [...links, { to: "/debug", label: "Debug" }]
    : links;

  return (
    <nav className="flex items-center justify-between border-b bg-background px-6 py-3">
      <div className="flex items-center gap-2">
        <span className="text-lg font-semibold">self-rag</span>
        <span
          className={cn(
            "ml-2 inline-flex h-2 w-2 rounded-full",
            ready ? "bg-emerald-500" : "bg-rose-500",
          )}
          title={ready ? "Backend ready" : "Backend down"}
        />
      </div>
      <div className="flex items-center gap-1">
        {finalLinks.map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            className={({ isActive }) =>
              cn(
                "rounded-md px-3 py-1.5 text-sm transition-colors",
                isActive
                  ? "bg-secondary text-secondary-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent",
              )
            }
          >
            {l.label}
          </NavLink>
        ))}
        {/* Current user badge — shows who you're signed in as. Critical for
            ACL demos where you flip between alice / bob / admin. */}
        {me.data && (
          <Badge
            variant={me.data.is_admin ? "warning" : "secondary"}
            className="ml-2 gap-1"
            title={
              me.data.groups.length
                ? `groups: ${me.data.groups.join(", ")}`
                : "no groups"
            }
          >
            {me.data.is_admin ? (
              <Shield className="h-3 w-3" />
            ) : (
              <User className="h-3 w-3" />
            )}
            {me.data.user_id}
          </Badge>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            clearToken();
            window.location.reload();
          }}
          title="Sign out"
        >
          <LogOut className="h-4 w-4" />
        </Button>
      </div>
    </nav>
  );
}
