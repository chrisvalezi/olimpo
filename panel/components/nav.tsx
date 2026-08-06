"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, Users, CheckSquare, Megaphone,
  Radar, Images, MessageSquare, Activity, Settings, Server, Drama,
} from "lucide-react";
import { clsx } from "clsx";

// A ordem reflete a prioridade da sala de controle: primeiro o que precisa
// de atenção, depois o que o sistema está fazendo. Ver docs/10-painel.md.
const ITEMS = [
  { href: "/", label: "Visão geral", icon: LayoutDashboard },
  { href: "/aprovacoes", label: "Aprovações", icon: CheckSquare },
  { href: "/contas", label: "Contas", icon: Users },
  { href: "/personas", label: "Personas", icon: Drama },
  { href: "/campanhas", label: "Campanhas", icon: Megaphone },
  { href: "/monitores", label: "Monitores", icon: Radar },
  { href: "/servidores", label: "Servidores", icon: Server },
  { href: "/midias", label: "Mídias", icon: Images },
  { href: "/interacoes", label: "Interações", icon: MessageSquare },
  { href: "/observabilidade", label: "Observabilidade", icon: Activity },
  { href: "/configuracoes", label: "Configurações", icon: Settings },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <nav className="w-56 shrink-0 border-r border-surface-border bg-surface-raised/40 p-3">
      <div className="mb-6 px-2 pt-2">
        <span className="text-lg font-semibold tracking-tight text-slate-100">
          🏛️ Olimpo
        </span>
      </div>

      <ul className="space-y-0.5">
        {ITEMS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <li key={href}>
              <Link
                href={href}
                className={clsx(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
                  active
                    ? "bg-surface-border/60 font-medium text-slate-100"
                    : "text-slate-400 hover:bg-surface-border/30 hover:text-slate-200",
                )}
              >
                <Icon size={16} strokeWidth={1.75} />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
