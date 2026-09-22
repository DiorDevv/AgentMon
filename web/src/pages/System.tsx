import { Cpu, Database, Globe, Layers, MemoryStick, Router, Settings2, ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Empty, ProductLabel, Skeleton } from "../components/ui";
import { ago, fmtDate, fmtInt } from "../format";
import { useApi } from "../hooks";
import { PRODUCT_NAMES } from "../labels";
import { collectorLive, engineFresh } from "../status";
import type { CollectorStatus, EngineStatus, Incident, Product } from "../types";

interface SystemData {
  status: { collector?: CollectorStatus; engine?: EngineStatus };
  sources: Array<{ source: string; last_attempt: string | null; last_success: string | null; last_error: string | null; item_count: number | null }>;
  incidents: Incident[];
  subnets: Array<{ cidr: string; site: string; source: string }>;
  dcs: Array<{ ip: string; name: string }>;
  config: {
    targets: Record<"cortex" | "ksc" | "si", string[]> & { ad_ports: string };
    thresholds: Record<Product, number>;
    alive_window: number;
    grace: number;
    debounce: number;
    mass_outage_ratio: number;
    mass_outage_min: number;
    products: Product[];
  };
}

type Health = "ok" | "warn" | "bad" | "idle";
const HEALTH_COLOR: Record<Health, string> = { ok: "var(--good)", warn: "var(--warning)", bad: "var(--critical)", idle: "var(--neutral)" };
const EVENT_NAMES: Record<string, string> = { "0": "NetFlow", "1": "Yaratildi", "2": "Yopildi", "3": "Rad etildi", "5": "Yangilanish" };
const SOURCE_NAMES: Record<string, string> = { ad: "Active Directory (LDAP + DNS)", cortex: "Cortex XDR API", ksc: "Kaspersky Security Center" };

const mins = (s: number) => (s >= 3600 ? `${s / 3600} soat` : `${Math.round(s / 60)} daqiqa`);

function Dot({ h }: { h: Health }) {
  return <span className="dot" style={{ background: HEALTH_COLOR[h] }} />;
}

function HealthPill({ h, children }: { h: Health; children: ReactNode }) {
  return (
    <span className="pill" style={{ ["--c" as string]: HEALTH_COLOR[h] }}>
      <Dot h={h} />{children}
    </span>
  );
}

function Stage({ icon: I, name, sub, h }: { icon: typeof Cpu; name: string; sub: ReactNode; h: Health }) {
  return (
    <div className="stage">
      <div className="s-ico"><I size={20} /><Dot h={h} /></div>
      <div className="s-name">{name}</div>
      <div className="s-sub">{sub}</div>
    </div>
  );
}

export function System() {
  const { data } = useApi<SystemData>("/api/system", 30000);
  if (!data) return <div className="grid"><Skeleton h={160} /><Skeleton h={300} /></div>;
  const col = data.status.collector;
  const eng = data.status.engine;
  const cfg = data.config;

  const live = collectorLive(col);
  const colH: Health = !col ? "idle" : !live ? "bad" : col.update_events_seen ? "ok" : "warn";
  const queueH: Health = !col || col.queue === null ? "idle" : col.queue < 50000 ? "ok" : "warn";
  const engH: Health = !eng ? "idle" : engineFresh(eng) ? "ok" : "bad";
  const srcH: Health = !data.sources.length ? "idle" : data.sources.every((s) => s.last_success && !s.last_error) ? "ok" : "warn";
  // Zanjirning umumiy holati — eng yomon bosqich bo'yicha.
  const chain: [Health, string] =
    colH === "bad" ? ["bad", "NetFlow kelmayapti"]
      : engH === "bad" ? ["bad", "Engine javob bermayapti"]
        : colH === "warn" || queueH === "warn" ? ["warn", "E'tibor talab"]
          : colH === "idle" ? ["idle", "Ma'lumot yo'q"] : ["ok", "Zanjir ishlayapti"];

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Tizim holati</h1>
          <p>Ma'lumot zanjiri, inventar manbalari va qoidalar sozlamalari</p>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <div>
            <h2>Ma'lumot zanjiri</h2>
            <div className="sub">NetFlow hodisasidan dashboard'gacha</div>
          </div>
          <HealthPill h={chain[0]}>{chain[1]}</HealthPill>
        </div>
        <div className="pipeline">
          <Stage icon={Router} name="Cisco FTD" h={colH === "idle" ? "idle" : live ? "ok" : "bad"} sub={col?.update_events_seen ? "NSEL + flow-update" : "NSEL"} />
          <Stage icon={Layers} name="Logstash" h={colH} sub={col ? `${fmtInt(Math.round(col.eps))} hodisa/s` : "—"} />
          <Stage icon={MemoryStick} name="Redis" h={queueH} sub={col?.queue === null || !col ? "—" : `navbat: ${fmtInt(col.queue)}`} />
          <Stage icon={Cpu} name="Engine" h={engH} sub={eng ? `${eng.eval_ms} ms · ${ago(eng.last_eval)}` : "—"} />
          <Stage icon={Database} name="PostgreSQL" h={eng ? "ok" : "idle"} sub={eng ? `${fmtInt(eng.hosts)} host` : "—"} />
          <Stage icon={Globe} name="Web" h="ok" sub="shu sahifa" />
        </div>
      </div>

      <div className="grid g-2 section">
        <div className="card">
          <div className="card-head"><h2>NetFlow kollektori</h2><HealthPill h={colH}>{!col ? "Ma'lumot yo'q" : !live ? "Kelmayapti" : "Jonli"}</HealthPill></div>
          <div className="card-body">
            {col ? (
              <dl className="kv">
                <dt>Oxirgi hodisa</dt><dd>{fmtDate(col.last_rx)} <span className="muted">({ago(col.last_rx)})</span></dd>
                <dt>Oqim</dt><dd className="num">{fmtInt(Math.round(col.eps))} hodisa/s</dd>
                <dt>flow-update</dt>
                <dd>{col.update_events_seen ? <span className="good-text">kelmoqda</span> : <span className="bad-text">kelmayapti — FTD'da refresh-interval sozlang</span>}</dd>
                <dt>Kuzatilayotgan IP</dt><dd className="num">{fmtInt(col.tracked_ips)}</dd>
                <dt>Qabul qilingan</dt><dd className="num">{fmtInt(col.received)} <span className="muted">({fmtInt(col.accepted)} foydalanuvchi trafigi, {fmtInt(col.malformed)} buzuq)</span></dd>
                <dt>Hodisa turlari</dt>
                <dd><div className="chips">{Object.entries(col.by_event).map(([k, v]) => <span key={k} className="pill plain" style={{ height: 22 }}>{EVENT_NAMES[k] ?? k}: <b className="num">{fmtInt(v)}</b></span>)}</div></dd>
              </dl>
            ) : <Empty>Engine hali ma'lumot yozmagan</Empty>}
          </div>
        </div>

        <div className="card">
          <div className="card-head"><h2>Qoidalar</h2><Settings2 size={18} className="muted" /></div>
          <div className="card-body">
            <dl className="kv">
              <dt>Tirik oynasi</dt><dd>{mins(cfg.alive_window)} <span className="muted">· yoqilgandan keyin kutish {mins(cfg.grace)}</span></dd>
              {(Object.keys(cfg.thresholds) as Product[]).map((p) => (
                <KV key={p} k={<ProductLabel product={p} />} v={`${mins(cfg.thresholds[p])} jimlik`} />
              ))}
              <dt>Tasdiqlash</dt><dd>{cfg.debounce} ketma-ket tekshiruv</dd>
              <dt>Ommaviy uzilish</dt><dd>OK agentlarning ≥{Math.round(cfg.mass_outage_ratio * 100)}% jim bo'lsa (kamida {cfg.mass_outage_min} ta)</dd>
            </dl>
          </div>
        </div>
      </div>

      <div className="card section">
        <div className="card-head"><h2>Inventar manbalari</h2><HealthPill h={srcH}>{srcH === "ok" ? "Hammasi ishlayapti" : srcH === "idle" ? "Sozlanmagan" : "E'tibor talab"}</HealthPill></div>
        <div className="table-wrap">
          <table className="tbl">
            <thead><tr><th>Manba</th><th>Holat</th><th>Oxirgi muvaffaqiyat</th><th className="r">Yozuvlar</th><th>Xatolik</th></tr></thead>
            <tbody>
              {data.sources.map((s) => {
                const fresh = !!s.last_success && Date.now() - new Date(s.last_success).getTime() < 4 * 3600 * 1000;
                const h: Health = !s.last_success ? "bad" : !fresh ? "bad" : s.last_error ? "warn" : "ok";
                return (
                  <tr key={s.source}>
                    <td className="host-name">{SOURCE_NAMES[s.source] ?? s.source}</td>
                    <td><HealthPill h={h}>{!s.last_success ? "Ulanmagan" : !fresh ? "Eskirgan" : s.last_error ? "Oxirgi urinish xato" : "Ishlayapti"}</HealthPill></td>
                    <td className="ink-2">{s.last_success ? <>{fmtDate(s.last_success)} <span className="muted">({ago(s.last_success)})</span></> : "—"}</td>
                    <td className="r num">{fmtInt(s.item_count)}</td>
                    <td className="mono bad-text" style={{ maxWidth: 420, fontWeight: 400 }}>{s.last_error ?? ""}</td>
                  </tr>
                );
              })}
              {!data.sources.length && <tr><td colSpan={5}><Empty>Manbalar sozlanmagan — .env faylini tekshiring</Empty></td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid g-2 section">
        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <div className="card">
            <div className="card-head"><h2>Signal manzillari</h2></div>
            <div className="card-body">
              <dl className="kv">
                <KV k={<ProductLabel product="cortex" />} v={<span className="mono">{cfg.targets.cortex.join(", ")}</span>} />
                <KV k={<ProductLabel product="ksc" />} v={<span className="mono">{cfg.targets.ksc.join(", ")}</span>} />
                <KV k={<ProductLabel product="si" />} v={<span className="mono">{cfg.targets.si.join(", ")}</span>} />
                <KV k={<ProductLabel product="ad" />} v={<span className="mono">{data.dcs.map((d) => `${d.ip} (${d.name})`).join(", ") || (cfg.products.includes("ad") ? "AD'dan hali olinmagan" : "AD o'chirilgan (PRODUCTS_ENABLED)")} · port {cfg.targets.ad_ports}</span>} />
              </dl>
            </div>
          </div>
          <div className="card">
            <div className="card-head"><h2>Hodisalar (30 kun)</h2><ShieldAlert size={18} className="muted" /></div>
            <div className="table-wrap">
              <table className="tbl">
                <tbody>
                  {data.incidents.map((i) => (
                    <tr key={i.id}>
                      <td><HealthPill h={i.ended_at ? "idle" : "bad"}>{i.kind === "mass_outage" ? `Ommaviy uzilish: ${PRODUCT_NAMES[i.product as Product]}` : "NetFlow kelmay qoldi"}</HealthPill></td>
                      <td className="ink-2 r">{fmtDate(i.started_at)} — {i.ended_at ? fmtDate(i.ended_at) : <b className="bad-text">davom etmoqda</b>}</td>
                    </tr>
                  ))}
                  {!data.incidents.length && <tr><td><Empty>Hodisa bo'lmagan</Empty></td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="card" style={{ alignSelf: "start" }}>
          <div className="card-head"><div><h2>Foydalanuvchi subnetlari</h2><div className="sub">{data.subnets.length} ta tarmoq hisobga olinmoqda</div></div></div>
          <div className="table-wrap" style={{ maxHeight: 480, overflowY: "auto" }}>
            <table className="tbl">
              <thead><tr><th>Subnet</th><th>Sayt</th><th>Manba</th></tr></thead>
              <tbody>
                {data.subnets.map((s) => (
                  <tr key={s.cidr}>
                    <td className="mono">{s.cidr}</td>
                    <td>{s.site}</td>
                    <td><span className="pill plain" style={{ height: 22 }}>{s.source === "ad" ? "AD Sites" : "Sozlama"}</span></td>
                  </tr>
                ))}
                {!data.subnets.length && <tr><td colSpan={3}><Empty>Subnet topilmadi — barcha xususiy manzillar hisobga olinmoqda</Empty></td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}

function KV({ k, v }: { k: ReactNode; v: ReactNode }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </>
  );
}
