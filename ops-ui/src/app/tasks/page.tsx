"use client";

import { useEffect, useMemo, useState } from "react";
import { api, type Task, type TasksResponse, type ExternalTask, type ExternalTasksResponse } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn, relativeTime } from "@/lib/utils";
import { CheckSquare, RefreshCw, AlertCircle, FileText, Bot } from "lucide-react";

const SECTION_TINT: Record<string, string> = {
  "Active":              "border-emerald-500/30 text-emerald-400",
  "Ops — Needs Attention": "border-red-500/30 text-red-400",
  "Backlog":             "border-slate-700 text-slate-400",
  "Completed":           "border-slate-700 text-slate-500",
};

export default function TasksPage() {
  const [data, setData] = useState<TasksResponse | null>(null);
  const [external, setExternal] = useState<ExternalTasksResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [toggling, setToggling] = useState<string | null>(null);
  const [showCompleted, setShowCompleted] = useState(false);

  async function load() {
    setRefreshing(true);
    setError(null);
    try {
      const [r, e] = await Promise.all([
        api.tasks(),
        api.externalTasks().catch(() => ({ agents: {} })),
      ]);
      setData(r);
      setExternal(e);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function toggle(t: Task) {
    setToggling(t.id);
    try {
      await api.toggleTask(t.id, !t.done);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setToggling(null);
    }
  }

  const counts = useMemo(() => {
    let open = 0, done = 0;
    for (const s of data?.sections || []) {
      for (const t of s.tasks) {
        if (t.done) done++;
        else open++;
      }
    }
    return { open, done };
  }, [data]);

  const sections = (data?.sections || []).filter(
    (s) => showCompleted || s.name !== "Completed"
  );

  return (
    <div className="mx-auto max-w-5xl p-6 lg:p-8">
      <header className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <CheckSquare className="h-6 w-6 text-slate-400" />
            Tasks
          </h1>
          <p className="text-sm text-slate-400">
            Sourced from <code className="rounded bg-slate-800 px-1 py-0.5 font-mono text-xs">TASKS.md</code> — check
            anything to write back. <span className="text-slate-300">{counts.open} open</span> ·{" "}
            <span className="text-slate-500">{counts.done} done</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowCompleted((v) => !v)}
          >
            {showCompleted ? "Hide" : "Show"} completed
          </Button>
          <Button variant="outline" size="sm" onClick={load} disabled={refreshing}>
            <RefreshCw className={cn("mr-2 h-3 w-3", refreshing && "animate-spin")} />
            Refresh
          </Button>
        </div>
      </header>

      {error && (
        <Card className="mb-6 border-red-500/30 bg-red-500/5">
          <CardContent className="flex items-center gap-2 pt-5 text-sm text-red-400">
            <AlertCircle className="h-4 w-4" />
            {error}
          </CardContent>
        </Card>
      )}

      <div className="space-y-4">
        {/* TASKS.md sections (live, writeable) */}
        {sections.map((s) => (
          <Card key={s.name}>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className={cn(
                  "text-sm font-semibold uppercase tracking-wider",
                  SECTION_TINT[s.name]?.split(" ")[1] || "text-slate-300"
                )}>
                  {s.name}
                </CardTitle>
                <Badge variant="outline" className="border-slate-700 text-[10px]">
                  {s.tasks.filter((t) => !t.done).length} open
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              {s.tasks.length === 0 ? (
                <div className="py-3 text-center text-xs text-slate-600">No tasks</div>
              ) : (
                <ul className="space-y-2">
                  {s.tasks.map((t) => (
                    <TaskRow
                      key={t.id}
                      task={t}
                      busy={toggling === t.id}
                      onToggle={() => toggle(t)}
                    />
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        ))}

        {/* Agent-pushed external task snapshots (read-only — agent owns these) */}
        {external && Object.entries(external.agents).map(([agentName, snap]) => {
          // Group by section (e.g., "Google Tasks")
          const groups: Record<string, ExternalTask[]> = {};
          for (const t of snap.tasks) {
            const k = t.section || t.source;
            (groups[k] ??= []).push(t);
          }
          return Object.entries(groups).map(([sectionName, tasks]) => (
            <Card key={`${agentName}::${sectionName}`} className="border-slate-800">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <CardTitle className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-sky-400">
                    <Bot className="h-3.5 w-3.5" />
                    {sectionName}
                  </CardTitle>
                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="border-slate-700 text-[10px]">
                      {tasks.filter((t) => !t.done).length} open
                    </Badge>
                    <span className="text-[10px] text-slate-600">
                      {agentName} agent · {relativeTime(snap.captured_at)}
                    </span>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="pt-0">
                {tasks.length === 0 ? (
                  <div className="py-3 text-center text-xs text-slate-600">
                    None pending
                  </div>
                ) : (
                  <ul className="space-y-2">
                    {tasks.map((t) => (
                      <ExternalTaskRow key={t.id} task={t} />
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          ));
        })}

        {external && Object.keys(external.agents).length === 0 && (
          <Card className="border-dashed border-slate-800 bg-transparent">
            <CardContent className="py-6 text-center text-xs text-slate-500">
              No agent-pushed task sources yet. The workspace agent will populate
              Google Tasks here on its next run.
            </CardContent>
          </Card>
        )}
      </div>

      {data && (
        <div className="mt-6 flex items-center gap-2 text-xs text-slate-600">
          <FileText className="h-3 w-3" />
          <code className="font-mono">{data.path}</code>
        </div>
      )}
    </div>
  );
}

function ExternalTaskRow({ task }: { task: ExternalTask }) {
  const due = (task.metadata as { due?: string } | undefined)?.due;
  return (
    <li className="flex items-start gap-3 rounded-md border border-slate-800 bg-slate-900/40 p-2.5">
      <div className="mt-0.5 h-4 w-4 shrink-0 rounded border border-slate-700 bg-slate-950" />
      <div className="min-w-0 flex-1">
        <div className="text-sm leading-snug text-slate-200">{task.text}</div>
        {due && (
          <div className="mt-1 text-[10px] text-slate-500">
            due {new Date(due).toLocaleDateString()}
          </div>
        )}
      </div>
    </li>
  );
}

function TaskRow({ task, busy, onToggle }: { task: Task; busy: boolean; onToggle: () => void }) {
  return (
    <li className="flex items-start gap-3 rounded-md border border-slate-800 bg-slate-900/40 p-2.5 transition hover:border-slate-700">
      <button
        onClick={onToggle}
        disabled={busy}
        className={cn(
          "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border transition",
          task.done
            ? "border-emerald-500 bg-emerald-500/20 text-emerald-400"
            : "border-slate-600 hover:border-emerald-500 hover:bg-emerald-500/10",
          busy && "opacity-50"
        )}
        aria-label={task.done ? "Mark as not done" : "Mark as done"}
      >
        {task.done && (
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none">
            <path
              d="M3 8l3.5 3.5L13 5"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </button>
      <span
        className={cn(
          "text-sm leading-snug",
          task.done ? "text-slate-500 line-through" : "text-slate-200"
        )}
      >
        {task.text}
      </span>
    </li>
  );
}
