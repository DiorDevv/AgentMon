import {
  AlertTriangle, CheckCircle2, CircleDashed, CircleOff, HelpCircle, Network, OctagonX, ScanEye, ShieldCheck,
  ShieldAlert, Bug, Split, Loader, type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { PRODUCT_COLOR, PRODUCT_NAMES, STATE_COLOR, STATE_HELP, stateLabel } from "../labels";
import type { Product, State } from "../types";

export const STATE_ICON: Record<State, LucideIcon> = {
  OK: CheckCircle2,
  UNHEALTHY: ShieldAlert,
  CONFLICT: Split,
  NO_SIGNAL: HelpCircle,
  STOPPED: OctagonX,
  NOT_INSTALLED: CircleOff,
  PENDING: Loader,
  OFFLINE: CircleDashed,
};

export const PRODUCT_ICON: Record<Product, LucideIcon> = {
  ad: Network,
  cortex: ShieldCheck,
  ksc: Bug,
  si: ScanEye,
};

export function StateIcon({ state, size = 14 }: { state: State; size?: number }) {
  const I = STATE_ICON[state];
  return (
    <span className="sicon" style={{ ["--c" as string]: STATE_COLOR[state] }} aria-hidden>
      <I size={size} strokeWidth={2.3} />
    </span>
  );
}

/** Holat belgisi: rangli fon + ikonka + matn. OK holati ataylab sokin — e'tibor muammolarga qaratiladi. */
export function StatePill({ product, state, title, faded }: {
  product: Product | null; state: State | null | undefined; title?: string | null; faded?: boolean;
}) {
  if (!state) return <span className="muted">—</span>;
  const I = STATE_ICON[state];
  return (
    <span
      className={`pill${state === "OK" ? " ok" : ""}${faded ? " faded" : ""}`}
      style={{ ["--c" as string]: STATE_COLOR[state] }}
      title={title || STATE_HELP[state]}
    >
      <span className="i"><I size={13} strokeWidth={2.4} /></span>
      {stateLabel(product, state)}
    </span>
  );
}

export function ProductIcon({ product, size = 32 }: { product: Product; size?: number }) {
  const I = PRODUCT_ICON[product];
  return (
    <span className="product-icon" style={{ background: PRODUCT_COLOR[product], width: size, height: size }} aria-hidden>
      <I size={Math.round(size * 0.52)} strokeWidth={2.2} />
    </span>
  );
}

export function ProductLabel({ product }: { product: Product }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <span className="swatch" style={{ background: PRODUCT_COLOR[product] }} />
      {PRODUCT_NAMES[product]}
    </span>
  );
}

/** Bitta qiymat uchun halqa ko'rsatkich (umumiy muvofiqlik). */
export function Ring({ value, size = 76, stroke = 8, color = "var(--accent)" }: {
  value: number | null; size?: number; stroke?: number; color?: string;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(100, value ?? 0));
  return (
    <svg className="ring" width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`${v}%`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={stroke} />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={`${(v / 100) * c} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  );
}

export function Kpi({ label, value, sub, icon: I, tone, to }: {
  label: string; value: ReactNode; sub?: ReactNode; icon: LucideIcon; tone?: "accent" | "bad" | "warn"; to?: string;
}) {
  const body = (
    <>
      <div className="kpi-top">
        <span className="kpi-label">{label}</span>
        <span className={`kpi-icon${tone ? " " + tone : ""}`}><I size={17} strokeWidth={2.1} /></span>
      </div>
      <div className="kpi-value num">{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </>
  );
  return to ? <Link className="card kpi" to={to}>{body}</Link> : <div className="card kpi">{body}</div>;
}

export function Delta({ value, suffix = " pp", invert }: { value: number | null | undefined; suffix?: string; invert?: boolean }) {
  if (value === null || value === undefined || Number.isNaN(value)) return null;
  const good = invert ? value < 0 : value > 0;
  const cls = Math.abs(value) < 0.05 ? "flat" : good ? "up" : "down";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "±";
  return <span className={`delta ${cls}`}>{sign}{Math.abs(value).toFixed(1)}{suffix}</span>;
}

export function Empty({ icon: I = AlertTriangle, children }: { icon?: LucideIcon; children: ReactNode }) {
  return (
    <div className="empty">
      <I size={30} strokeWidth={1.6} />
      <div>{children}</div>
    </div>
  );
}

export function Banner({ tone = "bad", icon: I = AlertTriangle, title, children }: {
  tone?: "bad" | "warn" | "info"; icon?: LucideIcon; title: string; children?: ReactNode;
}) {
  return (
    <div className={`banner ${tone === "bad" ? "" : tone}`}>
      <span className="bi"><I size={18} strokeWidth={2.2} /></span>
      <div><b>{title}</b>{children}</div>
    </div>
  );
}

export function Skeleton({ h = 16, w = "100%" }: { h?: number; w?: number | string }) {
  return <div className="skel" style={{ height: h, width: w }} />;
}
