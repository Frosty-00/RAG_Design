/** Reusable empty-state block.
 *
 *  Replaces the project's previous one-line "No X yet" muted text with
 *  a more inviting card: gradient icon disc + headline + supporting
 *  copy + optional CTA. Same shape across Documents / Chat / Eval /
 *  Admin so the app feels coherent.
 */
import type { LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: React.ReactNode;
  action?: { label: string; onClick: () => void; icon?: LucideIcon };
  className?: string;
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center px-6 py-12 text-center",
        className,
      )}
    >
      {/* Gradient icon disc. Soft circular halo behind it for visual weight
          without resorting to an external illustration asset. */}
      <div className="relative mb-5">
        <div className="absolute inset-0 -m-3 rounded-full bg-primary/10 blur-2xl" />
        <div className="relative flex h-14 w-14 items-center justify-center rounded-full bg-gradient-to-br from-primary to-primary/60 text-primary-foreground shadow-sm">
          <Icon className="h-6 w-6" />
        </div>
      </div>
      <h3 className="mb-1 text-base font-semibold tracking-tight">{title}</h3>
      {description && (
        <p className="mb-5 max-w-sm text-sm text-muted-foreground">
          {description}
        </p>
      )}
      {action && (
        <Button onClick={action.onClick}>
          {action.icon && <action.icon className="mr-2 h-4 w-4" />}
          {action.label}
        </Button>
      )}
    </div>
  );
}
