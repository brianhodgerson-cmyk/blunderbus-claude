"use client";

import { useEffect, useState } from "react";
import { api, type Concern, type Brief, type Health } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn, relativeTime, severityColor } from "@/lib/utils";
import { Activity, AlertCircle, CheckCircle2, RefreshCw, Server, Mail, Wallet } from "lucide-react";
import { BriefingRenderer } from "@/components/briefing-renderer";

const AGENT_ICONS: Record<string, typeof Server> = {
  infra: Server,
  workspace: Mail,
  finance: Wallet,
};

export default function NowPage() {
  const [concerns, setConcerns] = useState<Concern[] | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resolving, setResolving] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    setRefreshing(true);
    setError(null);
    try {
      const [c, b, h] = await Promise.all([
        api.concerns().catch(() => []),
        api.brief().catch(() => null),
        api.health().catch(() => null),
      ]);
      setConcerns(c);
      setBrief(b);
      setHealth(h);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function resolve(id: string) {
    setResolving(id);
    try {
      await api.resolveConcern(id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setResolving(null);
    }
  }

  const grouped = (concerns || []).reduce((acc, c) => {
    (acc[c.agent] ??= []).push(c);
    return acc;
  }, {} as Record<string, Concern[]>);

  return (
    <div className="mx-auto max-w-7xl p-6 lg:p-8">
      <header className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Now</h1>
          <p className="text-sm text-slate-400">
            Live operational state — auto-resolves as conditions clear
          </p>
        </div>
        <div className="flex items-center gap-3">
          {health && (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <span
                className={cn(
                  "inline-block h-2 w-2 rounded-full",
                  health.ok ? "bg-emerald-500" : "bg-red-500"
                )}
              />
              {health.ok ? "API healthy" : "API down"}
              {health.counts && (
                <span className="text-slate-500">
                  · {health.counts.people}p · {health.counts.projects}j ·{" "}
                  {health.counts.accounts}a · {health.counts.inventory}h
                </span>
              )}
            </div>
          )}
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

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* ── Concerns column (2/3 width) ── */}
        <div className="lg:col-span-2 space-y-6">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Activity className="h-5 w-5 text-amber-500" />
                    Active Signals
                  </CardTitle>
                  <CardDescription className="mt-1">
                    {concerns === null
                      ? "Loading…"
                      : concerns.length === 0
                        ? "Nothing's pulling for your attention."
                        : `${concerns.length} active across ${Object.keys(grouped).length} domain(s)`}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {concerns?.length === 0 && (
                <div className="flex items-center justify-center py-12 text-sm text-slate-500">
                  <CheckCircle2 className="mr-2 h-4 w-4 text-emerald-500" />
                  All clear.
                </div>
              )}
              {Object.entries(grouped).map(([agent, items]) => {
                const Icon = AGENT_ICONS[agent] ?? Activity;
                return (
                  <div key={agent}>
                    <div className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-slate-400">
                      <Icon className="h-3.5 w-3.5" />
                      {agent}
                    </div>
                    <div className="space-y-2">
                      {items.map((c) => (
                        <div
                          key={c.id}
                          className="rounded-lg border border-slate-800 bg-slate-900/50 p-3 transition hover:border-slate-700"
                        >
                          <div className="flex items-start gap-3">
                            <Badge variant="outline" className={severityColor(c.severity)}>
                              {c.severity}
                            </Badge>
                            <div className="min-w-0 flex-1">
                              <div className="text-sm font-medium leading-snug">{c.summary}</div>
                              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
                                {c.target && <span>target: {c.target}</span>}
                                {c.first_seen && <span>first seen {relativeTime(c.first_seen)}</span>}
                                {c.verifier && <span className="font-mono">via {c.verifier}</span>}
                              </div>
                              {c.suggested_action && (
                                <div className="mt-2 text-xs text-slate-400 italic">
                                  → {c.suggested_action}
                                </div>
                              )}
                            </div>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => resolve(c.id)}
                              disabled={resolving === c.id}
                              className="shrink-0"
                            >
                              {resolving === c.id ? "…" : "Resolve"}
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>
        </div>

        {/* ── Today's Brief column (1/3 width) ── */}
        <div>
          <Card>
            <CardHeader>
              <CardTitle>Today&apos;s Brief</CardTitle>
              <CardDescription>
                {brief?.date
                  ? (() => {
                      // Parse YYYY-MM-DD as local date (not UTC midnight)
                      const [y, m, d] = brief.date.split("-").map(Number);
                      return new Date(y, m - 1, d).toLocaleDateString(undefined, {
                        weekday: "long",
                        month: "long",
                        day: "numeric",
                      });
                    })()
                  : "—"}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {brief === null && <div className="text-sm text-slate-500">Loading…</div>}
              {brief && !brief.exists && (
                <div className="text-sm text-slate-500">
                  No briefing for today yet. The 6:30 AM scheduled task will produce one.
                </div>
              )}
              {brief?.briefing && <BriefingRenderer markdown={brief.briefing} />}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
