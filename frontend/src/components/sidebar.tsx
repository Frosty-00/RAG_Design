/** Left sidebar navigation.
 *
 *  Replaces the previous top nav for application-feel. Collapsible to a
 *  64px icon strip so users with smaller screens / wide chat columns
 *  can reclaim horizontal space. State persists in localStorage so the
 *  preference survives reloads.
 *
 *  Layout slot the App shell expects:
 *    <Sidebar /> on the left, main content fills the remaining width.
 */
import {
  ChevronsLeft,
  ChevronsRight,
  FileText,
  LogOut,
  MessageSquare,
  Settings,
  Shield,
  TestTube2,
  User,
  Wrench,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/brand";
import { useMe } from "@/hooks/use-admin";
import { clearToken } from "@/lib/auth";
import { cn } from "@/lib/utils";

const SIDEBAR_STORAGE_KEY = "self-rag.sidebar.collapsed";

type NavItem = {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Only render when current user is admin. */
  adminOnly?: boolean;
  /** Only render in Vite dev builds. */
  devOnly?: boolean;
};

const NAV_ITEMS: NavItem[] = [
  { to: "/chat", label: "Chat", icon: MessageSquare },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/eval", label: "Evaluation", icon: TestTube2 },
  { to: "/admin", label: "Admin", icon: Settings, adminOnly: true },
  { to: "/debug", label: "Debug", icon: Wrench, devOnly: true },
];

export function Sidebar({ ready }: { ready: boolean }) {
  const me = useMe();
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1";
  });

  useEffect(() => {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  const items = NAV_ITEMS.filter((item) => {
    if (item.adminOnly && !me.data?.is_admin) return false;
    if (item.devOnly && !import.meta.env.DEV) return false;
    return true;
  });

  return (
    <aside
      className={cn(
        "flex h-full flex-col border-r bg-[hsl(var(--sidebar))] text-[hsl(var(--sidebar-foreground))] transition-[width] duration-200",
        collapsed ? "w-16" : "w-60",
      )}
      style={{ borderColor: "hsl(var(--sidebar-border))" }}
    >
      {/* Brand */}
      <div
        className={cn(
          "flex h-14 items-center gap-2 border-b px-4",
          collapsed && "justify-center px-0",
        )}
        style={{ borderColor: "hsl(var(--sidebar-border))" }}
      >
        <BrandMark size={28} />
        {!collapsed && (
          <div className="flex flex-col leading-tight">
            <span className="text-sm font-semibold tracking-tight">
              self<span className="text-primary">·</span>rag
            </span>
            <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <span
                className={cn(
                  "inline-block h-1.5 w-1.5 rounded-full",
                  ready ? "bg-emerald-500" : "bg-rose-500",
                )}
              />
              {ready ? "online" : "offline"}
            </span>
          </div>
        )}
      </div>

      {/* Nav links */}
      <nav className="flex-1 space-y-1 px-2 py-3">
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            title={item.label}
            className={({ isActive }) =>
              cn(
                "flex items-center rounded-md text-sm transition-colors",
                collapsed ? "h-10 justify-center" : "h-9 gap-3 px-3",
                isActive
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
              )
            }
          >
            <item.icon className="h-4 w-4 shrink-0" />
            {!collapsed && <span className="truncate">{item.label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* User badge + collapse toggle */}
      <div
        className="border-t px-2 py-3"
        style={{ borderColor: "hsl(var(--sidebar-border))" }}
      >
        {me.data && (
          <div
            className={cn(
              "mb-2 flex items-center gap-2 rounded-md px-2 py-2",
              collapsed && "justify-center px-0",
            )}
            title={
              me.data.groups.length
                ? `${me.data.user_id} · groups: ${me.data.groups.join(", ")}`
                : me.data.user_id
            }
          >
            <div
              className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-medium",
                me.data.is_admin
                  ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                  : "bg-primary/15 text-primary",
              )}
            >
              {me.data.is_admin ? (
                <Shield className="h-4 w-4" />
              ) : (
                <User className="h-4 w-4" />
              )}
            </div>
            {!collapsed && (
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium">
                  {me.data.user_id}
                </p>
                <p className="truncate text-[10px] text-muted-foreground">
                  {me.data.is_admin ? "admin" : "user"}
                  {me.data.managed_groups.length > 0 &&
                    ` · mgr ${me.data.managed_groups.join(",")}`}
                </p>
              </div>
            )}
            {!collapsed && (
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                onClick={() => {
                  clearToken();
                  window.location.reload();
                }}
                title="Sign out"
              >
                <LogOut className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
        )}

        <Button
          variant="ghost"
          size="sm"
          className={cn(
            "w-full text-muted-foreground",
            collapsed && "justify-center px-0",
          )}
          onClick={() => setCollapsed((c) => !c)}
        >
          {collapsed ? (
            <ChevronsRight className="h-4 w-4" />
          ) : (
            <>
              <ChevronsLeft className="mr-2 h-4 w-4" />
              <span className="text-xs">Collapse</span>
            </>
          )}
        </Button>
      </div>
    </aside>
  );
}

