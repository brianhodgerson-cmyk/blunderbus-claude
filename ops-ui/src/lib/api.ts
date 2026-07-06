// API client for the BlunderBus Ops backend.
// In dev: NEXT_PUBLIC_API_BASE points at the local FastAPI (e.g. http://127.0.0.1:8000)
// In prod: empty string -> same-origin /api/* via the reverse proxy.

const BASE = process.env.NEXT_PUBLIC_API_BASE || "";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export type Person = {
  id: string;
  full_name: string;
  role?: string;
  title?: string;
  firm?: string;
  tags?: string[];
  triage?: string;
  relationships?: string[];
  attributes?: Record<string, unknown>;
  notes?: string;
  updated_at?: string;
};

export type Project = {
  id: string;
  name: string;
  status: "active" | "blocked" | "paused" | "done" | "archived";
  summary?: string;
  blockers?: string[];
  people?: string[];
  tags?: string[];
  attributes?: Record<string, unknown>;
  notes?: string;
  updated_at?: string;
};

export type Account = {
  id: string;
  name: string;
  institution: string;
  account_type: string;
  last_four?: string;
  owner?: string;
  balance?: number;
  notes?: string;
  attributes?: Record<string, unknown>;
};

export type Inventory = {
  id: string;
  hostname: string;
  ip?: string;
  vmid?: number;
  kind: string;
  role?: string;
  ssh_alias?: string;
  status: "running" | "stopped" | "unreachable";
  monitored?: boolean;
  notes?: string;
  attributes?: Record<string, unknown>;
};

export type Concern = {
  id: string;
  agent: string;
  type: string;
  target?: string;
  severity: "critical" | "high" | "medium" | "low" | "info";
  status: "active" | "resolved" | "stale";
  summary: string;
  suggested_action?: string;
  verifier?: string;
  first_seen?: string;
  last_verified?: string;
  resolved_at?: string;
  payload?: Record<string, unknown>;
};

export type Health = {
  ok: boolean;
  backend?: string;
  counts?: { people: number; projects: number; accounts: number; inventory: number };
  auth_required?: boolean;
  error?: string;
};

export type Brief = {
  date: string;
  exists: boolean;
  markdown: string;
  briefing: string;
};

export type Task = {
  id: string;
  text: string;
  done: boolean;
  section: string;
  line: number;
};

export type TasksResponse = {
  path: string;
  exists: boolean;
  sections: { name: string; tasks: Task[] }[];
};

export const api = {
  health: () => req<Health>("/api/health"),
  people: () => req<Person[]>("/api/registry/people"),
  projects: () => req<Project[]>("/api/registry/projects"),
  accounts: () => req<Account[]>("/api/registry/accounts"),
  inventory: () => req<Inventory[]>("/api/registry/inventory"),
  concerns: (agent?: string) =>
    req<Concern[]>(`/api/concerns${agent ? `?agent=${encodeURIComponent(agent)}` : ""}`),
  brief: () => req<Brief>("/api/brief/today"),
  resolveConcern: (id: string) =>
    req<{ resolved: boolean; id: string }>(
      `/api/concerns/${encodeURIComponent(id)}/resolve`,
      { method: "POST" }
    ),
  upsertProject: (id: string, body: Partial<Project>) =>
    req<Project>(`/api/registry/projects/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  upsertPerson: (id: string, body: Partial<Person>) =>
    req<Person>(`/api/registry/people/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  upsertAccount: (id: string, body: Partial<Account>) =>
    req<Account>(`/api/registry/accounts/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  upsertInventory: (id: string, body: Partial<Inventory>) =>
    req<Inventory>(`/api/registry/inventory/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  tasks: () => req<TasksResponse>("/api/tasks"),
  toggleTask: (id: string, done: boolean) =>
    req<{ id: string; done: boolean; changed: boolean }>("/api/tasks/toggle", {
      method: "POST",
      body: JSON.stringify({ id, done }),
    }),
  externalTasks: () => req<ExternalTasksResponse>("/api/tasks/external"),
};

export type ExternalTask = {
  id: string;
  text: string;
  source: string;
  section?: string;
  done: boolean;
  metadata?: Record<string, unknown>;
};

export type ExternalTasksResponse = {
  agents: Record<string, {
    agent: string;
    captured_at: string;
    tasks: ExternalTask[];
  }>;
};
