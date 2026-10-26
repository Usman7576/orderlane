import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BarChart3, Boxes, Inbox, LogOut, MessageSquare, Users } from "lucide-react";
import { NavLink, Outlet } from "react-router";

import { api } from "../lib/api";
import { useLogout, useSession } from "../lib/auth";
import type { Summary } from "../lib/types";
import { cn } from "../lib/cn";

function NavItem({ to, icon: Icon, label, count, alert = false }: {
  to: string;
  icon: typeof Inbox;
  label: string;
  count?: number;
  alert?: boolean;
}) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
          isActive ? "bg-white text-ink-900 shadow-xs ring-1 ring-ink-200" : "text-ink-600 hover:bg-ink-100 hover:text-ink-900",
        )
      }
    >
      <Icon className="size-4" aria-hidden />
      <span className="flex-1">{label}</span>
      {count ? (
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-xs font-semibold",
            alert ? "bg-red-100 text-red-700" : "bg-amber-100 text-amber-800",
          )}
        >
          {count}
        </span>
      ) : null}
    </NavLink>
  );
}

export function Layout() {
  const session = useSession();
  const logout = useLogout();
  const summary = useQuery({
    queryKey: ["summary"],
    queryFn: () => api<Summary>("/api/summary"),
    refetchInterval: 15_000,
  });
  const user = session.data?.user;

  return (
    <div className="flex min-h-screen bg-ink-50">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-ink-200 px-3 py-5 md:flex">
        <div className="mb-8 flex items-center gap-2 px-3">
          <div className="grid size-8 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white">OL</div>
          <div>
            <p className="text-sm font-semibold text-ink-900">OrderLane</p>
            <p className="text-xs text-ink-500">Staff dashboard</p>
          </div>
        </div>
        <nav className="flex flex-col gap-1" aria-label="Main">
          <NavItem to="/orders" icon={Inbox} label="Orders" count={summary.data?.needs_review} />
          <NavItem to="/catalog" icon={Boxes} label="Catalog" />
          <NavItem to="/customers" icon={Users} label="Customers" />
          <NavItem to="/jobs" icon={AlertTriangle} label="Failed jobs" count={summary.data?.failed_jobs} alert />
          <NavItem to="/simulator" icon={MessageSquare} label="Chat simulator" />
          <NavItem to="/metrics" icon={BarChart3} label="Metrics" />
        </nav>
        <div className="mt-auto border-t border-ink-200 px-3 pt-4">
          <p className="truncate text-sm font-medium text-ink-900">{user?.name ?? user?.email}</p>
          <p className="truncate text-xs text-ink-500">{user?.email}</p>
          <button
            type="button"
            onClick={() => logout.mutate()}
            className="mt-3 flex items-center gap-2 text-sm text-ink-600 hover:text-ink-900"
          >
            <LogOut className="size-4" aria-hidden /> Sign out
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-ink-200 bg-white px-4 py-3 md:hidden">
          <span className="font-semibold text-ink-900">OrderLane</span>
          <nav className="flex gap-3 text-sm text-ink-600" aria-label="Main">
            <NavLink to="/orders">Orders</NavLink>
            <NavLink to="/catalog">Catalog</NavLink>
            <NavLink to="/customers">Customers</NavLink>
            <NavLink to="/jobs">Jobs</NavLink>
            <NavLink to="/simulator">Chat</NavLink>
            <NavLink to="/metrics">Metrics</NavLink>
          </nav>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-8 md:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
