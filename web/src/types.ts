export type Product = "ad" | "cortex" | "ksc" | "si";
export type State =
  | "OK" | "UNHEALTHY" | "STOPPED" | "NOT_INSTALLED" | "NO_SIGNAL" | "CONFLICT" | "PENDING" | "OFFLINE";

export interface HostState {
  state: State;
  eff: State;
  reason: string | null;
  since: string;
}

export interface HostRow {
  id: number;
  name: string;
  fqdn: string | null;
  os: string | null;
  site: string | null;
  sources: string[];
  excluded: boolean;
  note: string | null;
  ips: string[];
  last_alive: string | null;
  online: boolean;
  recent: boolean;
  states: Record<Product, HostState | null>;
  problems: Product[];
}

export interface ProductSummary {
  counts: Partial<Record<State | "NONE", number>>;
  ok: number;
  problems: number;
  judged: number;
  coverage: number | null;
}

export interface Incident {
  id: number;
  kind: "mass_outage" | "collector_stale" | "exporter_stale";
  product: Product | null;
  started_at: string;
  ended_at?: string | null;
  details: { silent?: number; base?: number; ratio?: number; escalated?: boolean; escalated_at?: string; exporter?: string; state?: string };
}

export interface CollectorStatus {
  received: number;
  accepted: number;
  malformed: number;
  by_event: Record<string, number>;
  eps: number;
  queue: number | null;
  buffer_pct?: number | null;
  rejected?: number;
  exporters?: Record<string, { last_rx: string | null; received: number; state: "ok" | "quiet" | "stale" | "missing" }>;
  last_rx: string | null;
  stale: boolean;
  update_events_seen: boolean;
  tracked_ips: number;
  updated_at: string;
}

export interface EngineStatus {
  last_eval: string;
  hosts: number;
  alive: number;
  eval_ms: number;
  outages: string[];
  updated_at: string;
}

export interface Summary {
  compliant: number;
  judged_hosts: number;
  compliance: number | null;
  hosts_total: number;
  hosts_recent: number;
  hosts_online: number;
  hosts_with_problems: number;
  unknown_devices: number;
  products: Record<Product, ProductSummary>;
  sites: Array<{ site: string; hosts: number; problem_hosts: number } & Record<Product, number>>;
  incidents: Incident[];
  status: { collector?: CollectorStatus; engine?: EngineStatus };
}

export interface TrendPoint {
  ts: string;
  ok: number;
  problems: number;
  coverage: number | null;
}

export interface StateEvent {
  host_id: number;
  display_name: string;
  site: string | null;
  product: Product;
  state: State;
  reason: string | null;
  started_at: string;
  prev_state: State | null;
}
