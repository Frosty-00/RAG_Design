/** Admin / auth API hooks.
 *
 *  - `useMe()` returns the currently signed-in user — used by the Nav to
 *    render the user badge and decide whether to show the Admin link.
 *  - `useUsers()`, `useCreateUser()`, `useDeleteUser()` power the /admin
 *    page; only admin tokens can call these (backend enforces).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

export interface MeResponse {
  user_id: string;
  role: "user" | "admin";
  groups: string[];
  is_admin: boolean;
}

export interface UserInfo {
  user_id: string;
  role: "user" | "admin";
  groups: string[];
  n_tokens: number;
  /** When non-null, this is the canonical `{user_id}-dev-token` and the
   *  admin UI can offer one-click "switch to this user". */
  predictable_token: string | null;
}

export interface CreateUserPayload {
  user_id: string;
  groups: string[];
  role: "user" | "admin";
  predictable?: boolean;
}

export interface CreateUserResponse {
  token: string;
  user_id: string;
  role: string;
  groups: string[];
}

/** "Who am I right now" — cheap, cached for the session. */
export function useMe() {
  return useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => api.get<MeResponse>("/api/v1/admin/me"),
    staleTime: 60_000,
    retry: false,   // 401 already wipes the token; no point retrying
  });
}

/** List every user the admin can manage. Admin-only. */
export function useUsers() {
  return useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => api.get<UserInfo[]>("/api/v1/admin/users"),
    staleTime: 10_000,
    retry: false,
  });
}

export interface PickerUser {
  user_id: string;
  role: "user" | "admin";
  groups: string[];
}

/** Picker-friendly user list — any authenticated caller can use.
 *  Backend scopes results: admin sees all; non-admin sees colleagues
 *  sharing a group + admin users + themselves. Powers the upload page's
 *  Users ACL chip dropdown without leaking the full org chart. */
export function usePickerUsers() {
  return useQuery({
    queryKey: ["picker", "users"],
    queryFn: () => api.get<PickerUser[]>("/api/v1/admin/picker/users"),
    staleTime: 30_000,
    retry: false,
  });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateUserPayload) =>
      api.post<CreateUserResponse>("/api/v1/admin/tokens", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}

export function useDeleteUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      api.del<{ user_id: string; tokens_removed: number }>(
        `/api/v1/admin/users/${encodeURIComponent(userId)}`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }),
  });
}
