import { LogOut } from "lucide-react";
import { NavLink } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { clearToken } from "@/lib/auth";
import { cn } from "@/lib/utils";

const links = [
  { to: "/chat", label: "Chat" },
  { to: "/documents", label: "Documents" },
  { to: "/eval", label: "Eval" },
];
// Debug page only registered in dev (see App.tsx)
if (import.meta.env.DEV) {
  links.push({ to: "/debug", label: "Debug" });
}

export function Nav({ ready }: { ready: boolean }) {
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
        {links.map((l) => (
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
