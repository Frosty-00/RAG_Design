import { Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { MultiSelectChips } from "@/components/ui/multi-select-chips";
import { toast } from "@/components/ui/toast";
import { useMe, usePickerUsers } from "@/hooks/use-admin";
import { useUploadDocument } from "@/hooks/use-documents";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useUploadsStore } from "@/stores/uploads";

export function UploadDropzone() {
  const upload = useUploadDocument();
  const startInflight = useUploadsStore((s) => s.start);
  const succeedInflight = useUploadsStore((s) => s.succeed);
  const failInflight = useUploadsStore((s) => s.fail);
  const [dragOver, setDragOver] = useState(false);
  const [pending, setPending] = useState(0);
  const [isPublic, setIsPublic] = useState(false);
  // ACL fields are arrays internally (driven by MultiSelectChips), serialized
  // to comma-separated strings on submit (backend's existing form-data shape).
  const [users, setUsers] = useState<string[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  const fileInput = useRef<HTMLInputElement | null>(null);

  // Picker suggestions: every authenticated user can call /admin/picker/users.
  // Backend scopes the result — admin sees all, non-admin sees colleagues in
  // shared groups + admins + self. So the chip dropdown surfaces exactly the
  // user_ids it's *legal* for the caller to grant access to.
  const me = useMe();
  const pickerQ = usePickerUsers();
  const isAdmin = me.data?.is_admin ?? false;

  const userSuggestions = useMemo<string[]>(
    () => (pickerQ.data ?? []).map((u) => u.user_id).sort(),
    [pickerQ.data],
  );
  // Group suggestions:
  //   * admin → every group anyone has been assigned to (full org)
  //   * non-admin → only their own groups (can't grant to departments
  //                 they're not a member of — backend enforces too)
  const groupSuggestions = useMemo<string[]>(() => {
    if (isAdmin) {
      const set = new Set<string>();
      for (const u of pickerQ.data ?? []) {
        for (const g of u.groups) set.add(g);
      }
      return Array.from(set).sort();
    }
    return [...(me.data?.groups ?? [])].sort();
  }, [pickerQ.data, isAdmin, me.data]);

  // Non-admin convenience: prefill `groups` with caller's own groups on
  // first render. Without this an HR member's first upload would default
  // to "owner only" instead of "shared with HR". Admin keeps empty default
  // (admin actively decides ACL per upload).
  const didPrefillGroups = useRef(false);
  useEffect(() => {
    if (
      !didPrefillGroups.current
      && me.data
      && !isAdmin
      && groups.length === 0
      && (me.data.groups?.length ?? 0) > 0
    ) {
      setGroups([...me.data.groups]);
      didPrefillGroups.current = true;
    }
  }, [me.data, isAdmin, groups.length]);

  const handleFiles = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files);
      if (!list.length) return;
      setPending((n) => n + list.length);
      // Convert arrays → CSV at the boundary; backend still expects CSV.
      const usersCsv = users.join(",");
      const groupsCsv = groups.join(",");
      await Promise.all(
        list.map(async (file) => {
          const tempId = startInflight(file.name, file.size);
          try {
            const res = await upload.mutateAsync({
              file,
              isPublic,
              users: usersCsv,
              groups: groupsCsv,
            });
            succeedInflight(tempId, res.doc_id, res.version);
            if (res.status === "already_exists") {
              toast(`${file.name}: already in library (v${res.version})`, "info");
            } else {
              toast(`${file.name}: queued (v${res.version})`, "success");
            }
          } catch (e) {
            let msg: string;
            if (e instanceof ApiError) {
              const body = e.body as
                | { detail?: { code?: string; message?: string } | string }
                | undefined;
              const detail = body?.detail;
              if (e.status === 409 && typeof detail === "object"
                  && detail?.code === "filename_exists") {
                msg = detail.message ?? "filename already in library";
              } else if (typeof detail === "string") {
                msg = detail;
              } else {
                msg = `HTTP ${e.status}`;
              }
            } else {
              msg = "upload failed";
            }
            failInflight(tempId, msg);
            toast(`${file.name}: ${msg}`, "error");
          } finally {
            setPending((n) => n - 1);
          }
        }),
      );
    },
    [upload, isPublic, users, groups, startInflight, succeedInflight, failInflight],
  );

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault(); setDragOver(false);
          if (e.dataTransfer.files.length) void handleFiles(e.dataTransfer.files);
        }}
        onClick={() => fileInput.current?.click()}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 text-center transition-colors",
          dragOver ? "border-primary bg-accent" : "border-input bg-background",
        )}
      >
        <Upload className="mb-2 h-8 w-8 text-muted-foreground" />
        <p className="text-sm font-medium">
          Drag &amp; drop files here, or click to browse
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          PDF · DOCX · Markdown · TXT · HTML · XLSX · CSV · PPTX · JSON · PNG/JPG (OCR)
        </p>
        {pending > 0 && (
          <p className="mt-2 text-xs text-amber-600">
            {pending} upload(s) in flight…
          </p>
        )}
        <input
          ref={fileInput}
          type="file"
          multiple
          accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm,.xlsx,.csv,.tsv,.pptx,.json,.png,.jpg,.jpeg,.bmp,.webp"
          className="hidden"
          onChange={(e) => {
            if (e.target.files) void handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      <details className="rounded-lg border p-3 text-sm" open>
        <summary className="cursor-pointer text-muted-foreground">
          Visibility &amp; ACL
        </summary>
        <div className="mt-3 space-y-3">
          <div className="flex items-center gap-2">
            <input
              id="public"
              type="checkbox"
              checked={isPublic}
              disabled={!isAdmin}
              onChange={(e) => setIsPublic(e.target.checked)}
            />
            <Label
              htmlFor="public"
              className={cn(!isAdmin && "text-muted-foreground")}
            >
              Public (anyone can read){" "}
              {!isAdmin && (
                <span className="text-[10px] text-muted-foreground">
                  · admin only
                </span>
              )}
            </Label>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="users-input" className="text-xs">
                Users
              </Label>
              <MultiSelectChips
                id="users-input"
                value={users}
                onChange={setUsers}
                suggestions={userSuggestions}
                // Non-admin can only grant to people the backend will let
                // them see — strict selection avoids 403 surprises later.
                allowCustom={isAdmin}
                placeholder={
                  isAdmin ? "click to pick or type" : "click to pick"
                }
                emptySuggestionsHint={
                  pickerQ.isError
                    ? "Sign in to load suggestions."
                    : isAdmin
                      ? "No users yet — go to Admin to create some."
                      : "No colleagues in your group(s) yet."
                }
              />
            </div>
            <div>
              <Label htmlFor="groups-input" className="text-xs">
                Groups
                {!isAdmin && (
                  <span className="ml-2 text-[10px] text-muted-foreground">
                    · scoped to your departments
                  </span>
                )}
              </Label>
              <MultiSelectChips
                id="groups-input"
                value={groups}
                onChange={setGroups}
                suggestions={groupSuggestions}
                // Non-admin: lock to their own groups — backend rejects
                // anything else with 403 `groups_out_of_scope`.
                allowCustom={isAdmin}
                placeholder={
                  isAdmin
                    ? "click to pick or type"
                    : groupSuggestions.length === 0
                      ? "you don't belong to any group"
                      : "click to pick"
                }
                emptySuggestionsHint={
                  isAdmin
                    ? "No groups yet — assign groups to users in Admin."
                    : undefined
                }
              />
            </div>
          </div>
          {!isAdmin && (
            <p className="text-[10px] text-muted-foreground">
              Defaults to your department groups so colleagues can see this
              file. Clear all to keep it owner-only.
            </p>
          )}
        </div>
      </details>

      <Button
        variant="outline"
        size="sm"
        onClick={() => fileInput.current?.click()}
        disabled={upload.isPending}
        className="w-full"
      >
        Choose files
      </Button>
    </div>
  );
}
