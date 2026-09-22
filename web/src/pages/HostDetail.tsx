import { ChevronRight, EyeOff, History, Laptop, Network, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Banner, Empty, ProductIcon, Skeleton, StateIcon, StatePill } from "../components/ui";
import { ago, duration, fmtDate } from "../format";
import { useApi } from "../hooks";
import { PRODUCT_NAMES, PRODUCTS, STATE_COLOR, STATE_HELP, stateLabel } from "../labels";
import type { Product, State } from "../types";

interface ConsoleInfo {
  display_name: string;
  os: string | null;
  ips: string[];
  last_seen: string | null;
  healthy: boolean;
  reason: string | null;
  version: string | null;
  details: Record<string, unknown>;
  synced_at: string;
}

interface ProductDetail {
  state: State | null;
  eff: State | null;
  reason: string | null;
  since: string | null;
  last_known: State | null;
  last_known_reason: string | null;
  net_last_seen: string | null;
  net_denied: string | null;
  console: ConsoleInfo | null;
}

interface Detail {
  host: {
    id: number;
    display_name: string;
    fqdn: string | null;
    os: string | null;
    site: string | null;
    sources: string[];
    excluded: boolean;
    note: string | null;
    first_seen: string;
    last_alive: string | null;
  };
  net_any: string | null;
  ips: Array<{ ip: string; source: string; observed_at: string | null; alternatives: number; site: string | null; alive_since: string | null; last_seen: string | null }>;
  products: Record<Product, ProductDetail>;
  history: Array<{ product: Product; state: State; reason: string | null; started_at: string; ended_at: string | null }>;
}

const SOURCE_NAMES: Record<string, string> = { dns: "AD DNS", cortex: "Cortex agenti", ksc: "Kaspersky agenti", ad: "Active Directory" };

const DETAIL_KEYS: Record<string, string> = {
  endpoint_status: "Konsol holati",
  operational_status: "Himoya holati",
  content_version: "Kontent versiyasi",
  is_isolated: "Izolyatsiya",
  rtp_state: "RTP kodi",
  bases_updated: "Bazalar yangilangan",
  nagent_alive: "Network Agent faol",
  last_logon: "Oxirgi kirish (AD)",
  enabled: "Hisob faol",
};

export function HostDetail() {
  const { id } = useParams();
  const { data, error, reload } = useApi<Detail>(`/api/hosts/${id}`, 60000);

  if (error) return <Banner title="Kompyuter ma'lumoti olinmadi">{error}</Banner>;
  if (!data) return <div className="grid"><Skeleton h={140} /><Skeleton h={260} /></div>;
  const h = data.host;
  const online = !!data.net_any && Date.now() - new Date(data.net_any).getTime() < 15 * 60 * 1000;
  const problems = PRODUCTS.filter((p) => {
    const e = data.products[p].eff;
    return e && !["OK", "PENDING", "OFFLINE"].includes(e);
  });

  return (
    <>
      <div className="crumbs">
        <Link to="/hosts">Kompyuterlar</Link>
        <ChevronRight size={14} />
        <span className="ink-2">{h.display_name}</span>
      </div>

      <div className="card host-hero">
        <span className="big-ico"><Laptop size={26} /></span>
        <div style={{ flex: 1, minWidth: 260 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <h1>{h.display_name}</h1>
            <span className="pill" style={{ ["--c" as string]: online ? "var(--good)" : "var(--neutral)" }}>
              <span className="dot" style={{ background: online ? "var(--good)" : "var(--neutral)" }} />
              {online ? "Onlayn" : "Oflayn"}
            </span>
            {problems.length > 0
              ? <span className="pill" style={{ ["--c" as string]: "var(--critical)" }}>{problems.length} ta muammo</span>
              : <span className="pill ok"><StateIcon state="OK" size={13} />Barcha agentlar joyida</span>}
            {h.excluded && <span className="pill plain">Istisno</span>}
          </div>
          <dl className="meta">
            <div><dt>FQDN</dt><dd>{h.fqdn ?? "—"}</dd></div>
            <div><dt>Operatsion tizim</dt><dd>{h.os ?? "—"}</dd></div>
            <div><dt>Sayt</dt><dd>{h.site ?? "—"}</dd></div>
            <div><dt>IP manzil</dt><dd className="mono">{data.ips.map((i) => i.ip).join(", ") || "—"}</dd></div>
            <div><dt>Oxirgi faollik</dt><dd>{online ? "hozir" : ago(data.net_any)}</dd></div>
            <div><dt>Inventarda</dt><dd>{h.sources.map((s) => SOURCE_NAMES[s] ?? s).join(", ") || "—"}</dd></div>
          </dl>
        </div>
        <ExcludeControl id={h.id} excluded={h.excluded} note={h.note} onDone={reload} />
      </div>

      {h.excluded && (
        <div className="section">
          <Banner tone="warn" icon={EyeOff} title="Bu kompyuter monitoringdan chiqarilgan">
            {h.note || "Izoh yo'q"} — holati baholanmaydi va hisobotlarga kirmaydi.
          </Banner>
        </div>
      )}

      <div className="grid g-4 section" style={{ gridTemplateColumns: `repeat(${PRODUCTS.length}, minmax(0, 1fr))` }}>
        {PRODUCTS.map((p) => <ProductCard key={p} product={p} d={data.products[p]} />)}
      </div>

      <div className="grid g-detail section">
        <div className="card">
          <div className="card-head">
            <div>
              <h2>Holatlar tarixi</h2>
              <div className="sub">Har bir agent bo'yicha holat o'zgarishlari</div>
            </div>
            <History size={18} className="muted" />
          </div>
          <div className="timeline" style={{ maxHeight: 460, overflowY: "auto" }}>
            {data.history.map((r, i) => (
              <div className="tl-item" key={i}>
                <span className="tl-dot" style={{ ["--c" as string]: STATE_COLOR[r.state] }}><StateIcon state={r.state} size={12} /></span>
                <div className="tl-main">
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <b>{PRODUCT_NAMES[r.product]}</b>
                    <span className="ink-2">— {stateLabel(r.product, r.state)}</span>
                  </div>
                  {r.reason && r.state !== "OK" && <div className="t">{r.reason}</div>}
                  <div className="t">
                    {fmtDate(r.started_at)} · {r.ended_at ? `${duration(r.started_at, new Date(r.ended_at).getTime())} davom etdi` : "hozirgacha"}
                  </div>
                </div>
              </div>
            ))}
            {!data.history.length && <Empty icon={History}>Tarix yo'q</Empty>}
          </div>
        </div>

        <div className="card" style={{ alignSelf: "start" }}>
          <div className="card-head">
            <div>
              <h2>IP manzillar</h2>
              <div className="sub">Kompyuter qaysi IP'lar orqali aniqlangan</div>
            </div>
            <Network size={18} className="muted" />
          </div>
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr><th>IP</th><th>Manba</th><th className="r">Oxirgi trafik</th></tr>
              </thead>
              <tbody>
                {data.ips.map((i) => (
                  <tr key={i.ip}>
                    <td>
                      <span className="mono">{i.ip}</span>
                      {i.alternatives > 0 && (
                        <div className="host-sub" title="Bu IP boshqa nomlarga ham ko'rsatmoqda (DNS yozuvlari eskirgan bo'lishi mumkin). Eng yangi dalil tanlandi.">
                          yana {i.alternatives} ta nomga ko'rsatmoqda
                        </div>
                      )}
                    </td>
                    <td>
                      {SOURCE_NAMES[i.source] ?? i.source}
                      <div className="host-sub">{i.observed_at ? ago(i.observed_at) : "statik yozuv"}</div>
                    </td>
                    <td className="r ink-2" title={fmtDate(i.last_seen)}>{ago(i.last_seen)}</td>
                  </tr>
                ))}
                {!data.ips.length && (
                  <tr><td colSpan={3}><Empty icon={Network}>IP aniqlanmadi — DNS'da ham, agentlarda ham ishonchli dalil yo'q</Empty></td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="card-body" style={{ borderTop: "1px solid var(--border)", fontSize: 12.5 }}>
            <span className="muted">Inventarga birinchi marta qo'shilgan: </span>{fmtDate(h.first_seen)}
          </div>
        </div>
      </div>
    </>
  );
}

function ProductCard({ product, d }: { product: Product; d: ProductDetail }) {
  const c = d.console;
  const shown = d.eff ?? d.state;
  const isStale = !!(d.state && d.eff && d.state !== d.eff);
  const details = c ? Object.entries(c.details).filter(([k, v]) => DETAIL_KEYS[k] && v !== null && v !== undefined && v !== "") : [];
  return (
    <div className="card pcard" style={{ ["--c" as string]: shown ? STATE_COLOR[shown] : "var(--neutral)" }}>
      <div className="pcard-top" />
      <div className="pcard-head">
        <ProductIcon product={product} size={28} />
        <b style={{ flex: 1 }}>{PRODUCT_NAMES[product]}</b>
        <StatePill product={product} state={shown} faded={isStale} />
      </div>
      <div className="pcard-body">
        <div className="reason">
          {(isStale ? d.last_known_reason : d.reason) || (shown ? STATE_HELP[shown] : "Hali baholanmagan")}
          {isStale && <span className="muted"> Kompyuter hozir {d.state === "OFFLINE" ? "oflayn" : "tekshirilmoqda"} — bu oxirgi ma'lum holat.</span>}
        </div>
        <dl className="kv">
          <dt>Holatda</dt>
          <dd>{d.since ? <span title={fmtDate(d.since)}>{duration(d.since)}</span> : "—"}</dd>
          <dt>Tarmoq</dt>
          <dd>{d.net_last_seen ? `oxirgi trafik ${ago(d.net_last_seen)}` : <span className="bad-text">serverga trafik yo'q</span>}</dd>
          {d.net_denied && (
            <>
              <dt>FTD</dt>
              <dd className="bad-text">rad etilgan urinish {ago(d.net_denied)}</dd>
            </>
          )}
          {product !== "si" && (
            <>
              <dt>Konsol</dt>
              <dd>
                {c ? (
                  <>
                    {c.healthy ? <span className="good-text">sog'lom</span> : c.reason}
                    {c.last_seen && <div className="muted" style={{ fontSize: 12 }}>{product === "ad" ? "oxirgi kirish" : "oxirgi aloqa"} {ago(c.last_seen)}</div>}
                  </>
                ) : (
                  <span className="bad-text">konsolda yo'q</span>
                )}
              </dd>
            </>
          )}
          {c?.version && (
            <>
              <dt>Versiya</dt>
              <dd className="mono">{c.version}</dd>
            </>
          )}
          {details.map(([k, v]) => (
            <KV key={k} k={DETAIL_KEYS[k]} v={formatVal(v)} />
          ))}
        </dl>
      </div>
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </>
  );
}

function formatVal(v: unknown): string {
  if (typeof v === "boolean") return v ? "ha" : "yo'q";
  if (typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v)) return fmtDate(v);
  return String(v);
}

function ExcludeControl({ id, excluded, note, onDone }: { id: number; excluded: boolean; note: string | null; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(note ?? "");
  const [busy, setBusy] = useState(false);

  async function save(ex: boolean) {
    setBusy(true);
    try {
      await api(`/api/hosts/${id}/exclude`, { method: "POST", json: { excluded: ex, note: ex ? text : null } });
      setOpen(false);
      onDone();
    } finally {
      setBusy(false);
    }
  }

  if (excluded) return <button className="btn" disabled={busy} onClick={() => save(false)}><RotateCcw size={15} />Monitoringga qaytarish</button>;
  if (!open) return <button className="btn" onClick={() => setOpen(true)}><EyeOff size={15} />Istisno qilish</button>;
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      <input className="input" placeholder="Sabab (masalan, test stend)" value={text} onChange={(e) => setText(e.target.value)} autoFocus style={{ width: 240 }} />
      <button className="btn primary" disabled={busy || !text.trim()} onClick={() => save(true)}>Saqlash</button>
      <button className="btn" onClick={() => setOpen(false)}>Bekor qilish</button>
    </div>
  );
}
