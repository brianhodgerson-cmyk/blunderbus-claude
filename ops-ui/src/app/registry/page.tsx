"use client";

import { useEffect, useState } from "react";
import { api, type Person, type Account, type Inventory } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EditForm, type FieldDef } from "@/components/edit-form";
import { cn } from "@/lib/utils";
import { Users, Wallet, Server, RefreshCw, AlertCircle, Search, Pencil } from "lucide-react";

type Tab = "people" | "accounts" | "inventory";

const PERSON_FIELDS: FieldDef[] = [
  { name: "full_name", label: "Full name", kind: "text" },
  { name: "role", label: "Role", kind: "text" },
  { name: "title", label: "Title", kind: "text" },
  { name: "firm", label: "Firm", kind: "text" },
  { name: "triage", label: "Triage", kind: "select", options: ["", "high", "medium", "low", "self"] },
  { name: "tags", label: "Tags", kind: "tags" },
  { name: "relationships", label: "On projects", kind: "list" },
  { name: "notes", label: "Notes", kind: "textarea" },
];

const ACCOUNT_FIELDS: FieldDef[] = [
  { name: "name", label: "Name", kind: "text" },
  { name: "institution", label: "Institution", kind: "text" },
  { name: "account_type", label: "Type", kind: "text" },
  { name: "last_four", label: "Last four", kind: "text" },
  { name: "owner", label: "Owner (person id, or 'joint')", kind: "text" },
  { name: "tags", label: "Tags", kind: "tags" },
  { name: "notes", label: "Notes", kind: "textarea" },
];

const INVENTORY_FIELDS: FieldDef[] = [
  { name: "hostname", label: "Hostname", kind: "text" },
  { name: "ip", label: "IP", kind: "text" },
  { name: "kind", label: "Kind", kind: "select", options: ["vm", "lxc", "host", "service"] },
  { name: "role", label: "Role", kind: "text" },
  { name: "ssh_alias", label: "SSH alias", kind: "text" },
  { name: "status", label: "Status", kind: "select", options: ["running", "stopped", "unreachable"] },
  { name: "tags", label: "Tags", kind: "tags" },
  { name: "notes", label: "Notes", kind: "textarea" },
];

export default function RegistryPage() {
  const [tab, setTab] = useState<Tab>("people");
  const [people, setPeople] = useState<Person[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [inventory, setInventory] = useState<Inventory[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    setRefreshing(true);
    setError(null);
    try {
      const [p, a, i] = await Promise.all([api.people(), api.accounts(), api.inventory()]);
      setPeople(p); setAccounts(a); setInventory(i);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function savePerson(p: Person, draft: Partial<Person>) {
    setSaving(true);
    try { await api.upsertPerson(p.id, { ...p, ...draft }); setEditing(null); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  }
  async function saveAccount(a: Account, draft: Partial<Account>) {
    setSaving(true);
    try { await api.upsertAccount(a.id, { ...a, ...draft }); setEditing(null); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  }
  async function saveInventory(h: Inventory, draft: Partial<Inventory>) {
    setSaving(true);
    try { await api.upsertInventory(h.id, { ...h, ...draft }); setEditing(null); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSaving(false); }
  }

  const tabs: { id: Tab; label: string; icon: typeof Users; count: number }[] = [
    { id: "people",    label: "People",    icon: Users,  count: people.length },
    { id: "accounts",  label: "Accounts",  icon: Wallet, count: accounts.length },
    { id: "inventory", label: "Inventory", icon: Server, count: inventory.length },
  ];

  const matchesQuery = (s: string) =>
    !query || s.toLowerCase().includes(query.toLowerCase());

  return (
    <div className="mx-auto max-w-7xl p-6 lg:p-8">
      <header className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Registry</h1>
          <p className="text-sm text-slate-400">
            Click any card to edit. Saves write to the canonical markdown — agents pick up changes on next run.
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

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border border-slate-800 bg-slate-900/50 p-1">
          {tabs.map(({ id, label, icon: Icon, count }) => (
            <button
              key={id}
              onClick={() => { setTab(id); setEditing(null); }}
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                tab === id ? "bg-slate-800 text-slate-100" : "text-slate-400 hover:text-slate-200"
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
              <Badge variant="outline" className="ml-1 border-slate-700 text-[10px]">{count}</Badge>
            </button>
          ))}
        </div>
        <div className="relative ml-auto w-full max-w-xs">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search…"
            className="w-full rounded-md border border-slate-800 bg-slate-900/50 py-1.5 pl-8 pr-3 text-sm placeholder:text-slate-500 focus:border-slate-700 focus:outline-none"
          />
        </div>
      </div>

      {tab === "people" && (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          {people.filter((p) => matchesQuery(`${p.full_name} ${p.role} ${p.firm} ${p.id}`)).map((p) => (
            <PersonCard
              key={p.id}
              p={p}
              editing={editing === p.id}
              saving={saving && editing === p.id}
              onEdit={() => setEditing(p.id)}
              onCancel={() => setEditing(null)}
              onSave={(d) => savePerson(p, d)}
            />
          ))}
        </div>
      )}

      {tab === "accounts" && (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {accounts.filter((a) => matchesQuery(`${a.name} ${a.institution} ${a.account_type} ${a.id}`)).map((a) => (
            <AccountCard
              key={a.id}
              a={a}
              editing={editing === a.id}
              saving={saving && editing === a.id}
              onEdit={() => setEditing(a.id)}
              onCancel={() => setEditing(null)}
              onSave={(d) => saveAccount(a, d)}
            />
          ))}
        </div>
      )}

      {tab === "inventory" && (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          {inventory.filter((h) => matchesQuery(`${h.hostname} ${h.ip} ${h.role} ${h.id}`)).map((h) => (
            <InventoryCard
              key={h.id}
              h={h}
              editing={editing === h.id}
              saving={saving && editing === h.id}
              onEdit={() => setEditing(h.id)}
              onCancel={() => setEditing(null)}
              onSave={(d) => saveInventory(h, d)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function EditableCard({
  editing, onEdit, onCancel, onSave, saving, fields, initial, label, children,
}: {
  editing: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSave: (d: Record<string, unknown>) => void;
  saving: boolean;
  fields: FieldDef[];
  initial: Record<string, unknown>;
  label: string;
  children: React.ReactNode;
}) {
  if (editing) {
    return (
      <Card className="bg-slate-900/40 ring-1 ring-sky-500/40">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Editing {label}</CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <EditForm
            initial={initial}
            fields={fields}
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
      {children}
    </Card>
  );
}

function PersonCard({ p, editing, saving, onEdit, onCancel, onSave }: {
  p: Person; editing: boolean; saving: boolean;
  onEdit: () => void; onCancel: () => void;
  onSave: (d: Partial<Person>) => void;
}) {
  return (
    <EditableCard
      editing={editing}
      saving={saving}
      onEdit={onEdit}
      onCancel={onCancel}
      onSave={(d) => onSave(d as Partial<Person>)}
      fields={PERSON_FIELDS}
      initial={{
        full_name: p.full_name, role: p.role || "", title: p.title || "",
        firm: p.firm || "", triage: p.triage || "",
        tags: p.tags || [], relationships: p.relationships || [],
        notes: p.notes || "",
      }}
      label={p.full_name}
    >
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="text-base leading-snug">{p.full_name}</CardTitle>
            <CardDescription className="text-xs">
              {p.role}{p.firm && <> · {p.firm}</>}
            </CardDescription>
          </div>
          <Pencil className="h-3.5 w-3.5 shrink-0 text-slate-600 opacity-0 transition group-hover:opacity-100" />
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        {p.notes && (
          <p className="mb-3 text-xs leading-relaxed text-slate-400 line-clamp-3">{p.notes}</p>
        )}
        <div className="flex flex-wrap gap-1">
          {p.tags?.map((t) => (
            <Badge key={t} variant="outline" className="text-[10px]">{t}</Badge>
          ))}
        </div>
        {p.relationships && p.relationships.length > 0 && (
          <div className="mt-2 text-[10px] text-slate-500">on: {p.relationships.join(", ")}</div>
        )}
      </CardContent>
    </EditableCard>
  );
}

function AccountCard({ a, editing, saving, onEdit, onCancel, onSave }: {
  a: Account; editing: boolean; saving: boolean;
  onEdit: () => void; onCancel: () => void;
  onSave: (d: Partial<Account>) => void;
}) {
  const ownerUnknown = (a.owner || "").toUpperCase() === "UNKNOWN" || !a.owner;
  return (
    <EditableCard
      editing={editing}
      saving={saving}
      onEdit={onEdit}
      onCancel={onCancel}
      onSave={(d) => onSave(d as Partial<Account>)}
      fields={ACCOUNT_FIELDS}
      initial={{
        name: a.name, institution: a.institution, account_type: a.account_type,
        last_four: a.last_four || "", owner: a.owner || "",
        tags: (a as Account & { tags?: string[] }).tags || [],
        notes: a.notes || "",
      }}
      label={a.name}
    >
      <div className={cn("rounded-xl", ownerUnknown && "border border-amber-500/30")}>
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <CardTitle className="text-base leading-snug">
                {a.name}
                {a.last_four && (
                  <span className="ml-2 font-mono text-xs text-slate-500">…{a.last_four}</span>
                )}
              </CardTitle>
              <CardDescription className="text-xs">
                {a.institution} · {a.account_type}
                {a.balance != null && <> · ${a.balance.toLocaleString()}</>}
              </CardDescription>
            </div>
            <Pencil className="h-3.5 w-3.5 shrink-0 text-slate-600 opacity-0 transition group-hover:opacity-100" />
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          {ownerUnknown && (
            <div className="mb-2 text-xs text-amber-400">⚠ owner unknown — click to set</div>
          )}
          {!ownerUnknown && a.owner && (
            <div className="text-xs text-slate-500">
              owner: <span className="text-slate-300">{a.owner}</span>
            </div>
          )}
        </CardContent>
      </div>
    </EditableCard>
  );
}

function InventoryCard({ h, editing, saving, onEdit, onCancel, onSave }: {
  h: Inventory; editing: boolean; saving: boolean;
  onEdit: () => void; onCancel: () => void;
  onSave: (d: Partial<Inventory>) => void;
}) {
  return (
    <EditableCard
      editing={editing}
      saving={saving}
      onEdit={onEdit}
      onCancel={onCancel}
      onSave={(d) => onSave(d as Partial<Inventory>)}
      fields={INVENTORY_FIELDS}
      initial={{
        hostname: h.hostname, ip: h.ip || "",
        kind: h.kind, role: h.role || "",
        ssh_alias: h.ssh_alias || "",
        status: h.status,
        tags: (h as Inventory & { tags?: string[] }).tags || [],
        notes: h.notes || "",
      }}
      label={h.hostname}
    >
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="text-base leading-snug">{h.hostname}</CardTitle>
            <CardDescription className="text-xs">
              {h.kind}{h.role && <> · {h.role}</>}{h.ip && <> · <span className="font-mono">{h.ip}</span></>}
            </CardDescription>
          </div>
          <Pencil className="h-3.5 w-3.5 shrink-0 text-slate-600 opacity-0 transition group-hover:opacity-100" />
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          {h.ssh_alias && <span className="font-mono">{h.ssh_alias}</span>}
          {h.monitored ? (
            <span className="text-emerald-400">● monitored</span>
          ) : (
            <span className="text-slate-600">○ not monitored</span>
          )}
        </div>
        {h.notes && (
          <p className="mt-2 text-xs leading-relaxed text-slate-400 line-clamp-2">{h.notes}</p>
        )}
      </CardContent>
    </EditableCard>
  );
}
