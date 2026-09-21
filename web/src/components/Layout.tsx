import { Activity, LayoutDashboard, LogOut, Monitor, Moon, Radar, Search, Shield, Sun } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { api } from "../api";
import { ago, fmtInt } from "../format";
import { useApi } from "../hooks";
import { collectorLive } from "../status";
import type { Summary } from "../types";

function currentTheme(): "light" | "dark" {
  const t = document.documentElement.dataset.theme;
  if (t === "light" || t === "dark") return t;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function Layout({ user, onLogout, children }: { user: string; onLogout: () => void; children: ReactNode }) {
  const [theme, setTheme] = useState(currentTheme);
  const { data: s } = useApi<Summary>("/api/summary", 30000);
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  // "/" tugmasi — qidiruvga fokus (professional panellardagi odatiy qulaylik).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (e.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("theme", next);
    } catch {
      /* e'tiborsiz */
    }
    setTheme(next);
  }

  async function logout() {
    await api("/api/logout", { method: "POST" }).catch(() => {});
    onLogout();
  }

  const col = s?.status.collector;
  const live = collectorLive(col);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><Shield size={19} strokeWidth={2.4} /></span>
          <div>
            <div className="brand-name">AgentMon</div>
            <div className="brand-sub">Agentlar qamrovi monitoringi</div>
          </div>
        </div>
        <nav className="nav">
          <div className="nav-label">Monitoring</div>
          <NavLink to="/" end><LayoutDashboard size={18} />Umumiy ko'rinish</NavLink>
          <NavLink to="/hosts">
            <Monitor size={18} />Kompyuterlar
            {s && <span className={`count${s.hosts_with_problems ? " alert" : ""}`}>{fmtInt(s.hosts_with_problems)}</span>}
          </NavLink>
          <NavLink to="/unknown">
            <Radar size={18} />Noma'lum qurilmalar
            {s && s.unknown_devices > 0 && <span className="count alert">{fmtInt(s.unknown_devices)}</span>}
          </NavLink>
          <div className="nav-label">Tizim</div>
          <NavLink to="/system"><Activity size={18} />Tizim holati</NavLink>
        </nav>
        <div className="side-foot">
          <div className="side-card">
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
              <span className={`pulse${live ? "" : " bad"}`} />
              <b>{live ? "NetFlow jonli" : "NetFlow kelmayapti"}</b>
            </div>
            {col ? <>{fmtInt(Math.round(col.eps))} hodisa/s · {ago(col.last_rx)}</> : "Ma'lumot yo'q"}
          </div>
          <div className="side-user">
            <span className="avatar">{user.slice(0, 2)}</span>
            <div>
              <div className="name">{user}</div>
              <div style={{ fontSize: 11.5, color: "var(--side-muted)" }}>Administrator</div>
            </div>
            {user !== "anonymous" && (
              <button onClick={logout} title="Chiqish" aria-label="Chiqish"><LogOut size={16} /></button>
            )}
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <form
            className="search"
            onSubmit={(e) => {
              e.preventDefault();
              if (q.trim()) nav(`/hosts?q=${encodeURIComponent(q.trim())}&scope=all`);
            }}
          >
            <Search size={16} />
            <input ref={searchRef} placeholder="Kompyuter nomi yoki IP bo'yicha qidirish…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Qidiruv" />
            <kbd>/</kbd>
          </form>
          <div className="spacer" />
          {col && (
            <span className="live" title={`Oxirgi hodisa: ${ago(col.last_rx)}`}>
              <span className={`pulse${live ? "" : " bad"}`} />
              <span className="hide-sm">{live ? "Jonli" : "To'xtagan"}</span>
              <b>{fmtInt(Math.round(col.eps))}</b> hodisa/s
            </span>
          )}
          <button className="icon-btn" onClick={toggleTheme} title={theme === "dark" ? "Yorug' mavzu" : "Qorong'i mavzu"} aria-label="Mavzuni almashtirish">
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
          </button>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
