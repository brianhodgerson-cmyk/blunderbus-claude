"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  KanbanSquare,
  Users,
  Activity,
  CheckSquare,
} from "lucide-react";

const items = [
  { href: "/", label: "Now", icon: LayoutDashboard },
  { href: "/tasks", label: "Tasks", icon: CheckSquare },
  { href: "/projects", label: "Projects", icon: KanbanSquare },
  { href: "/registry", label: "Registry", icon: Users },
  { href: "/concerns", label: "Concerns", icon: Activity },
];

export function Nav() {
  const path = usePathname();
  return (
    <aside className="w-56 shrink-0 border-r border-slate-200 bg-slate-50/50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
      <div className="mb-6 flex items-center gap-2 px-2">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-slate-900 text-sm font-bold text-white dark:bg-slate-100 dark:text-slate-900">
          BB
        </div>
        <div>
          <div className="text-sm font-semibold leading-tight">BlunderBus</div>
          <div className="text-xs text-slate-500">Ops</div>
        </div>
      </div>
      <nav className="flex flex-col gap-1">
        {items.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? path === "/" : path.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                  : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
