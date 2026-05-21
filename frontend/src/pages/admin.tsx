/** Admin page · user & token management.
 *
 *  Lists every user that has at least one issued token, lets admin create
 *  new users with the convention-friendly `{user_id}-dev-token`, and
 *  offers a one-click "Switch to this user" that swaps the localStorage
 *  token + reloads so you can demo ACL behaviour quickly.
 *
 *  Routing: only registered when current user has role=admin (see App.tsx).
 */
import { Copy, LogIn, Plus, Shield, Trash2, User, Users } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/toast";
import {
  useCreateUser,
  useDeleteUser,
  useMe,
  useUsers,
  type CreateUserResponse,
  type UserInfo,
} from "@/hooks/use-admin";
import { ApiError } from "@/lib/api";
import { setToken } from "@/lib/auth";

export default function AdminPage() {
  const me = useMe();
  const users = useUsers();
  const create = useCreateUser();
  const del = useDeleteUser();

  const [userId, setUserId] = useState("");
  const [groups, setGroups] = useState("");
  const [managedGroups, setManagedGroups] = useState("");
  const [role, setRole] = useState<"user" | "admin">("user");

  const [justIssued, setJustIssued] = useState<CreateUserResponse | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<UserInfo | null>(null);

  const onCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = userId.trim();
    if (!trimmed) return;
    try {
      const res = await create.mutateAsync({
        user_id: trimmed,
        groups: groups
          .split(",")
          .map((g) => g.trim())
          .filter(Boolean),
        managed_groups: managedGroups
          .split(",")
          .map((g) => g.trim())
          .filter(Boolean),
        role,
        predictable: true,
      });
      setJustIssued(res);
      setUserId("");
      setGroups("");
      setManagedGroups("");
      setRole("user");
      toast(`Issued token for ${res.user_id}`, "success");
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? typeof e.body === "object" && e.body && "detail" in e.body
            ? String((e.body as { detail: unknown }).detail)
            : `HTTP ${e.status}`
          : "create failed";
      toast(`Create user failed: ${msg}`, "error");
    }
  };

  const onConfirmDelete = async () => {
    if (!confirmDelete) return;
    try {
      const res = await del.mutateAsync(confirmDelete.user_id);
      toast(
        `Removed ${res.user_id} (${res.tokens_removed} token(s))`,
        "success",
      );
      setConfirmDelete(null);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? typeof e.body === "object" && e.body && "detail" in e.body
            ? String((e.body as { detail: unknown }).detail)
            : `HTTP ${e.status}`
          : "delete failed";
      toast(`Delete failed: ${msg}`, "error");
    }
  };

  if (me.data && !me.data.is_admin) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Forbidden</CardTitle>
          <CardDescription>
            Admin role required. Sign in with an admin token.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[380px_1fr]">
      {/* Create form */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Plus className="h-4 w-4" /> Create user
          </CardTitle>
          <CardDescription>
            Token format <code>{"{user_id}-dev-token"}</code>. Re-issuing for an
            existing user_id overwrites their token.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onCreate} className="space-y-3">
            <div>
              <Label htmlFor="uid">User ID</Label>
              <Input
                id="uid"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="alice"
                pattern="[A-Za-z0-9_\-]+"
                autoFocus
                required
              />
              <p className="mt-1 text-[10px] text-muted-foreground">
                Letters, digits, <code>_</code>, <code>-</code> only.
              </p>
            </div>
            <div>
              <Label htmlFor="groups">Groups (comma-separated)</Label>
              <Input
                id="groups"
                value={groups}
                onChange={(e) => setGroups(e.target.value)}
                placeholder="hr, eng"
              />
            </div>
            <div>
              <Label htmlFor="managed">
                Manages (comma-separated)
                <span className="ml-2 text-[10px] text-muted-foreground">
                  · read-through any doc accessible to these depts
                </span>
              </Label>
              <Input
                id="managed"
                value={managedGroups}
                onChange={(e) => setManagedGroups(e.target.value)}
                placeholder="hr   (e.g. HR director)"
              />
              <p className="mt-1 text-[10px] text-muted-foreground">
                Independent from <b>Groups</b>. A user managing <code>hr</code>
                {" "}sees every doc HR members see — even private grants to
                specific HR people. Doesn't grant upload/delete rights.
              </p>
            </div>
            <div>
              <Label>Role</Label>
              <div className="flex gap-2 pt-1">
                <Button
                  type="button"
                  variant={role === "user" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setRole("user")}
                >
                  <User className="mr-1 h-3 w-3" /> user
                </Button>
                <Button
                  type="button"
                  variant={role === "admin" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setRole("admin")}
                >
                  <Shield className="mr-1 h-3 w-3" /> admin
                </Button>
              </div>
            </div>
            <Button
              type="submit"
              className="w-full"
              disabled={create.isPending || !userId.trim()}
            >
              {create.isPending ? "Creating…" : "Create user + issue token"}
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* Users list */}
      <Card>
        <CardHeader>
          <CardTitle>Users</CardTitle>
          <CardDescription>
            {users.data?.length ?? 0} user(s). Click{" "}
            <LogIn className="inline h-3 w-3" /> to switch to that user — the
            page reloads with their token.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {users.isError && (
            <p className="text-sm text-destructive">Failed to load users.</p>
          )}
          {users.data && users.data.length === 0 && (
            <EmptyState
              icon={Users}
              title="No team members yet"
              description={
                <>
                  Add users to grant scoped access to the knowledge base.
                  Tokens follow the predictable
                  {" "}<code>{"{user_id}-dev-token"}</code> pattern.
                </>
              }
            />
          )}
          {users.data && users.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase text-muted-foreground">
                  <tr className="border-b">
                    <th className="px-3 py-2">User</th>
                    <th className="px-3 py-2">Role</th>
                    <th className="px-3 py-2">Groups</th>
                    <th className="px-3 py-2">Manages</th>
                    <th className="px-3 py-2">Tokens</th>
                    <th className="px-3 py-2 text-right"></th>
                  </tr>
                </thead>
                <tbody>
                  {users.data.map((u) => (
                    <UserRow
                      key={u.user_id}
                      user={u}
                      isMe={u.user_id === me.data?.user_id}
                      onDelete={() => setConfirmDelete(u)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* "Just-issued" dialog — shows the new token + lets admin switch */}
      <Dialog open={justIssued !== null} onOpenChange={(o) => !o && setJustIssued(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Token issued for {justIssued?.user_id}</DialogTitle>
            <DialogDescription>
              Save this token somewhere safe. You can re-issue any time (same
              user_id always gets the same predictable token).
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-md border bg-muted/30 p-3 font-mono text-sm">
            {justIssued?.token}
          </div>
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              onClick={() => {
                if (justIssued) {
                  void navigator.clipboard.writeText(justIssued.token);
                  toast("Token copied", "success");
                }
              }}
            >
              <Copy className="mr-2 h-4 w-4" /> Copy
            </Button>
            <Button
              onClick={() => {
                if (!justIssued) return;
                setToken(justIssued.token);
                toast(`Signed in as ${justIssued.user_id}`, "success");
                window.location.href = "/chat";
              }}
            >
              <LogIn className="mr-2 h-4 w-4" /> Sign in as this user
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <Dialog
        open={confirmDelete !== null}
        onOpenChange={(o) => !o && setConfirmDelete(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove {confirmDelete?.user_id}?</DialogTitle>
            <DialogDescription>
              Revokes <b>{confirmDelete?.n_tokens}</b> token(s) for this user.
              Documents they uploaded are not deleted — they stay in the
              library but become unowned. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={onConfirmDelete}
              disabled={del.isPending}
            >
              {del.isPending ? "Removing…" : "Remove user"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function UserRow({
  user,
  isMe,
  onDelete,
}: {
  user: UserInfo;
  isMe: boolean;
  onDelete: () => void;
}) {
  const navigate = useNavigate();

  const switchTo = () => {
    if (!user.predictable_token) {
      toast(
        "No predictable token — this user was created with a random token. Re-issue from the form to get a switchable one.",
        "info",
      );
      return;
    }
    setToken(user.predictable_token);
    toast(`Switched to ${user.user_id}`, "success");
    // Hard reload so AuthGate re-evaluates and all React Query caches reset
    window.location.href = "/chat";
    navigate("/chat");
  };

  return (
    <tr className="border-b last:border-b-0 hover:bg-muted/40">
      <td className="px-3 py-2 font-medium">
        {user.user_id}
        {isMe && (
          <Badge variant="secondary" className="ml-2 text-[10px]">
            you
          </Badge>
        )}
      </td>
      <td className="px-3 py-2">
        <Badge variant={user.role === "admin" ? "warning" : "secondary"}>
          {user.role}
        </Badge>
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">
        {user.groups.length ? user.groups.join(", ") : "—"}
      </td>
      <td className="px-3 py-2 text-xs text-muted-foreground">
        {user.managed_groups.length ? (
          <div className="flex flex-wrap gap-1">
            {user.managed_groups.map((g) => (
              <Badge key={g} variant="warning" className="text-[10px]">
                {g}
              </Badge>
            ))}
          </div>
        ) : (
          "—"
        )}
      </td>
      <td className="px-3 py-2 tabular-nums text-xs">{user.n_tokens}</td>
      <td className="px-3 py-2 text-right">
        <div className="flex justify-end gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={switchTo}
            disabled={isMe || !user.predictable_token}
            title={
              isMe
                ? "You are already this user"
                : user.predictable_token
                  ? `Switch to ${user.user_id}`
                  : "No predictable token — re-issue to enable"
            }
            aria-label="Switch user"
          >
            <LogIn className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={onDelete}
            disabled={isMe}
            title={isMe ? "Cannot delete yourself" : "Remove user"}
            aria-label="Delete user"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </td>
    </tr>
  );
}
