"use client";

import { useEffect, useMemo, useState } from "react";
import { api, type Project } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EditForm, type FieldDef } from "@/components/edit-form";
import { cn, relativeTime } from "@/lib/utils";
import { AlertCircle, RefreshCw, KanbanSquare, CheckCircle2, Pencil } from "lucide-react";

const COLUMNS: { status: Project["status"]; label: string; tint: string }[] = [
  { status: "active",   label: "Active",   tint: "text-emerald-400 border-emerald-500/30" },
  { status: "blocked",  label: "Blocked",  tint: "text-red-400 border-red-500/30" },
  { status: "paused",   label: "Paused",   tint: "text-amber-400 border-amber-500/30" },
  { status: "done",     label: "Done",     tint: "text-slate-400 border-slate-700" },
];

const PROJECT_FIELDS: FieldDef[] = [
  { name: "name", label: "Name", kind: "text" },
  { name: "status", label: "Status", kind: "select",
    options: ["active", "blocked", "paused", "done", "archived"] },
  { name: "summary", label: "Summary", kind: "textarea" },
  { name: "blockers", label: "Blockers", kind: "list" },
  { name: "people", label: "People", kind: "list" },
  { name: "tags", label: "Tags", kind: "tags" },
  { name: "notes", label: "Notes (markdown body)", kind: "textarea" },
];

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [savingBlocker, setSavingBlocker] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);

  async function load() {
    setRefreshing(true);
    setError(null);
    try {
      const p = await api.projects();
      setProjects(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function clearBlocker(project: Project, blocker: string) {
    const key = `${project.id}::${blocker}`;
    setSavingBlocker(key);
    try {
      const next = (project.blockers || []).filter((b) => b !== blocker);
      await api.upsertProject(project.id, { ...project, blockers: next });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingBlocker(null);
    }
  }

  async function saveEdit(p: Project, draft: Partial<Project>) {
    setSavingEdit(true);
    try {
      await api.upsertProject(p.id, { ...p, ...draft });
      setEditing(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingEdit(false);
    }
  }

  const grouped = useMemo(() => {
    const out: Record<string, Project[]> = {};
    for (const col of COLUMNS) out[col.status] = [];
    for (const p of projects || []) {
      if (out[p.status]) out[p.status].push(p);
    }
    return out;
  }, [projects]);

  return (
    <div className="mx-auto max-w-[1600px] p-6 lg:p-8">
      <header className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <KanbanSquare className="h-6 w-6 text-slate-400" />
            Projects
          </h1>
          <p className="text-sm text-slate-400">
            Click a card to edit. Uncheck a blocker to unstick a project — change reflects in tomorrow&apos;s brief.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={refreshing}>
          <RefreshCw className={cn("mr-2 h-3 w-3", refreshing && "animate-spin")} />
          Refresh
        </Button>
      </header>

      {error && (
        <Card className="mb-6 border-red-500/30 bg-red-500/5">
          <CardContent className="flex items-center gap-2 pt-5 text-sm text-red-400">
            <AlertCircle className="h-4 w-4" />
            {error}
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {COLUMNS.map((col) => (
          <div key={col.status} className="flex flex-col gap-3">
            <div className={cn(
              "flex items-center justify-between rounded-md border bg-slate-900/50 px-3 py-2",
              col.tint
            )}>
              <div className="text-xs font-semibold uppercase tracking-wider">{col.label}</div>
              <Badge variant="outline" className="border-slate-700 text-slate-400">
                {grouped[col.status]?.length ?? 0}
              </Badge>
            </div>
            <div className="space-y-3">
              {(grouped[col.status] ?? []).map((p) => (
                <ProjectCard
                  key={p.id}
                  project={p}
                  editing={editing === p.id}
                  saving={savingEdit && editing === p.id}
                  onEdit={() => setEditing(p.id)}
                  onCancel={() => setEditing(null)}
                  onSave={(draft) => saveEdit(p, draft)}
                  onClearBlocker={(b) => clearBlocker(p, b)}
                  savingBlocker={savingBlocker}
                />
              ))}
              {grouped[col.status]?.length === 0 && (
                <div className="rounded-lg border border-dashed border-slate-800 p-4 text-center text-xs text-slate-600">
                  No {col.label.toLowerCase()} projects
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ProjectCard({
  project, editing, saving, onEdit, onCancel, onSave, onClearBlocker, savingBlocker,
}: {
  project: Project;
  editing: boolean;
  saving: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSave: (draft: Partial<Project>) => void;
  onClearBlocker: (b: string) => void;
  savingBlocker: string | null;
}) {
  if (editing) {
    return (
      <Card className="bg-slate-900/40 ring-1 ring-sky-500/40">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Editing</CardTitle>
          <CardDescription className="font-mono text-xs">{project.id}</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          <EditForm<Partial<Project>>
            initial={{
              name: project.name,
              status: project.status,
              summary: project.summary || "",
              blockers: project.blockers || [],
              people: project.people || [],
              tags: project.tags || [],
              notes: project.notes || "",
            }}
            fields={PROJECT_FIELDS}
            onCancel={onCancel}
            onSave={onSave}
            saving={saving}
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card
      className="group cursor-pointer bg-slate-900/40 transition hover:bg-slate-900/70 hover:ring-1 hover:ring-slate-700"
      onClick={onEdit}
    >
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="text-sm leading-snug">{project.name}</CardTitle>
            <CardDescription className="font-mono text-xs">{project.id}</CardDescription>
          </div>
          <Pencil className="h-3.5 w-3.5 shrink-0 text-slate-600 opacity-0 transition group-hover:opacity-100" />
        </div>
      </CardHeader>
      <CardContent className="pt-0" onClick={(e) => e.stopPropagation()}>
        {project.summary && (
          <p className="mb-3 text-xs leading-relaxed text-slate-400 line-clamp-3">
            {project.summary}
          </p>
        )}
        {project.blockers && project.blockers.length > 0 && (
          <div className="mb-3">
            <div className="mb-1.5 text-xs font-medium text-red-400">Blockers</div>
            <ul className="space-y-1.5">
              {project.blockers.map((b) => {
                const key = `${project.id}::${b}`;
                const saving = savingBlocker === key;
                return (
                  <li key={b} className="flex items-start gap-2 text-xs text-slate-300">
                    <button
                      onClick={(e) => { e.stopPropagation(); onClearBlocker(b); }}
                      disabled={saving}
                      className="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border border-slate-600 transition hover:border-emerald-500 hover:bg-emerald-500/20 disabled:opacity-50"
                      aria-label={`Clear blocker: ${b}`}
                      title="Click to clear this blocker"
                    >
                      {saving && <CheckCircle2 className="h-3 w-3 text-emerald-400" />}
                    </button>
                    <span className="leading-snug">{b}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        {project.tags && project.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {project.tags.map((t) => (
              <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>
            ))}
          </div>
        )}
        {project.updated_at && (
          <div className="mt-3 text-[10px] text-slate-600">
            updated {relativeTime(project.updated_at)}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
