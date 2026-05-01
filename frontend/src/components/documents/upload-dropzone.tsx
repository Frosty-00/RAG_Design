import { Upload } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/toast";
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
  const [users, setUsers] = useState("");
  const [groups, setGroups] = useState("");
  const fileInput = useRef<HTMLInputElement | null>(null);

  const handleFiles = useCallback(
    async (files: FileList | File[]) => {
      const list = Array.from(files);
      if (!list.length) return;
      setPending((n) => n + list.length);
      // Upload in parallel; report per-file outcome
      await Promise.all(
        list.map(async (file) => {
          // Optimistic row — appears in the documents table instantly.
          const tempId = startInflight(file.name, file.size);
          try {
            const res = await upload.mutateAsync({
              file, isPublic, users, groups,
            });
            succeedInflight(tempId, res.doc_id, res.version);
            if (res.status === "already_exists") {
              toast(`${file.name}: already in library (v${res.version})`, "info");
            } else {
              toast(`${file.name}: queued (v${res.version})`, "success");
            }
          } catch (e) {
            // Surface the structured 409 message from the filename-dedupe
            // guard so the user sees *why* it was rejected, not just "409".
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

      <details className="rounded-lg border p-3 text-sm">
        <summary className="cursor-pointer text-muted-foreground">
          Visibility &amp; ACL
        </summary>
        <div className="mt-3 space-y-3">
          <div className="flex items-center gap-2">
            <input
              id="public"
              type="checkbox"
              checked={isPublic}
              onChange={(e) => setIsPublic(e.target.checked)}
            />
            <Label htmlFor="public">Public (anyone can read)</Label>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <Label htmlFor="users" className="text-xs">
                Users (comma-separated)
              </Label>
              <Input
                id="users" value={users}
                onChange={(e) => setUsers(e.target.value)}
                placeholder="alice, bob"
              />
            </div>
            <div>
              <Label htmlFor="groups" className="text-xs">
                Groups (comma-separated)
              </Label>
              <Input
                id="groups" value={groups}
                onChange={(e) => setGroups(e.target.value)}
                placeholder="hr, eng"
              />
            </div>
          </div>
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
