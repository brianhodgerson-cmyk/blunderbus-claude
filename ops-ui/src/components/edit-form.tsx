"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { X, Save } from "lucide-react";

export type FieldDef =
  | { name: string; label: string; kind: "text" | "textarea" }
  | { name: string; label: string; kind: "select"; options: string[] }
  | { name: string; label: string; kind: "tags" }
  | { name: string; label: string; kind: "list" }; // newline-separated list of strings

export interface EditFormProps<T extends Record<string, unknown>> {
  initial: T;
  fields: FieldDef[];
  onSave: (next: T) => Promise<void> | void;
  onCancel: () => void;
  saving?: boolean;
  className?: string;
}

export function EditForm<T extends Record<string, unknown>>({
  initial, fields, onSave, onCancel, saving, className,
}: EditFormProps<T>) {
  const [draft, setDraft] = React.useState<T>(initial);

  function set(name: string, value: unknown) {
    setDraft((d) => ({ ...d, [name]: value }));
  }

  return (
    <form
      className={cn("space-y-3", className)}
      onSubmit={(e) => {
        e.preventDefault();
        onSave(draft);
      }}
    >
      {fields.map((f) => {
        const val = (draft as Record<string, unknown>)[f.name];
        const id = `f_${f.name}`;
        switch (f.kind) {
          case "text":
            return (
              <Field key={f.name} label={f.label} htmlFor={id}>
                <input
                  id={id}
                  type="text"
                  value={(val as string) || ""}
                  onChange={(e) => set(f.name, e.target.value)}
                  className={inputCls}
                />
              </Field>
            );
          case "textarea":
            return (
              <Field key={f.name} label={f.label} htmlFor={id}>
                <textarea
                  id={id}
                  rows={3}
                  value={(val as string) || ""}
                  onChange={(e) => set(f.name, e.target.value)}
                  className={cn(inputCls, "resize-y")}
                />
              </Field>
            );
          case "select":
            return (
              <Field key={f.name} label={f.label} htmlFor={id}>
                <select
                  id={id}
                  value={(val as string) || ""}
                  onChange={(e) => set(f.name, e.target.value)}
                  className={inputCls}
                >
                  {f.options.map((opt) => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
              </Field>
            );
          case "tags":
          case "list": {
            const arr = (val as string[] | undefined) ?? [];
            const text = arr.join(f.kind === "tags" ? ", " : "\n");
            return (
              <Field key={f.name} label={f.label + (f.kind === "tags" ? " (comma-separated)" : " (one per line)")} htmlFor={id}>
                {f.kind === "tags" ? (
                  <input
                    id={id}
                    type="text"
                    value={text}
                    onChange={(e) =>
                      set(f.name, e.target.value.split(",").map((s) => s.trim()).filter(Boolean))
                    }
                    className={inputCls}
                  />
                ) : (
                  <textarea
                    id={id}
                    rows={Math.max(2, arr.length + 1)}
                    value={text}
                    onChange={(e) =>
                      set(f.name, e.target.value.split("\n").map((s) => s.trim()).filter(Boolean))
                    }
                    className={cn(inputCls, "resize-y")}
                  />
                )}
              </Field>
            );
          }
        }
      })}

      <div className="flex justify-end gap-2 pt-1">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
          <X className="mr-1.5 h-3 w-3" />
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={saving}>
          <Save className="mr-1.5 h-3 w-3" />
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}

const inputCls =
  "w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-100 placeholder:text-slate-500 focus:border-slate-500 focus:outline-none";

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1 block text-[10px] font-medium uppercase tracking-wider text-slate-500">
        {label}
      </label>
      {children}
    </div>
  );
}
