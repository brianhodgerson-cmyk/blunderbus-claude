"use client";

import { useEffect, useState } from "react";
import { api, type Concern } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn, relativeTime, severityColor } from "@/lib/utils";
import { Activity, RefreshCw, AlertCircle } from "lucide-react";

const STATUS_COLOR: Record<string, string> = {
  active:   "bg-amber-500/15 text-amber-400 border-amber-500/30",
  resolved: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  stale:    "bg-slate-500/15 text-slate-400 border-slate-600/30",
};

export default function ConcernsPage() {
  const [items, setItems] = useState<Concern[] | null>(null);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState<"all" | "active" | "resolved" | "stale">("all");

  async function load() {
    setRefreshing(true);
    setError(null);
    try {
      const r = await fetch(`${process.env.NEXT_PUBLIC_API_BASE || ""}/api/concerns/all?limit=200`);
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const data = (await r.json()) as Concern[];
      setItems(data);
      const s: Record<string, number> = {};
      for (const c of data) s[c.status] = (s[c.status] || 0) + 1;
      setStats(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }
  useEffect(() => { load(); }, []);

  const visible = (items || []).filter((c) => filter === "all" || c.status === filter);

  return (
    <div className="mx-auto max-w-7xl p-6 lg:p-8">
      <header className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <Activity className="h-6 w-6 text-slate-400" />
            Concerns
          </h1>
          <p className="text-sm text-slate-400">
            Audit log of every concern across all agents — history of what broke when.
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

      <div className="mb-4 flex gap-2">
        {(["all", "active", "resolved", "stale"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={cn(
              "rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
              filter === s
                ? "border-slate-600 bg-slate-800 text-slate-100"
                : "border-slate-800 bg-slate-900/50 text-slate-400 hover:text-slate-200"
            )}
          >
            {s === "all" ? "All" : s.charAt(0).toUpperCase() + s.slice(1)}
            {s !== "all" && stats[s] != null && (
              <span className="ml-1.5 text-slate-500">({stats[s]})</span>
            )}
          </button>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{visible.length} concern(s)</CardTitle>
          <CardDescription className="text-xs">
            Most recently verified first. `first_seen` and `resolved_at` come from the
            agent_concerns Postgres table — actual timestamps, not paraphrased ages.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {visible.map((c) => (
              <div
                key={c.id}
                className="rounded-lg border border-slate-800 bg-slate-900/50 p-3"
              >
                <div className="flex flex-wrap items-start gap-2">
                  <Badge variant="outline" className={severityColor(c.severity)}>
                    {c.severity}
                  </Badge>
                  <Badge variant="outline" className={STATUS_COLOR[c.status]}>
                    {c.status}
                  </Badge>
                  <Badge variant="secondary" className="text-[10px]">
                    {c.agent}
                  </Badge>
                  <div className="ml-auto text-xs text-slate-500">
                    last verified {relativeTime(c.last_verified)}
                  </div>
                </div>
                <div className="mt-2 text-sm font-medium leading-snug">{c.summary}</div>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
                  <span className="font-mono">{c.id}</span>
                  {c.first_seen && <span>first {relativeTime(c.first_seen)}</span>}
                  {c.resolved_at && <span>resolved {relativeTime(c.resolved_at)}</span>}
                  {c.verifier && <span>via {c.verifier}</span>}
                </div>
              </div>
            ))}
            {visible.length === 0 && (
              <div className="py-12 text-center text-sm text-slate-500">
                No concerns matching this filter.
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
