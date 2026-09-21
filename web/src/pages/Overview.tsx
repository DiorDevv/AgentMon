import { ArrowRight, Download, History, Radar, ShieldAlert, Wifi, WifiOff } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { TrendChart } from "../components/TrendChart";
import { Banner, Delta, Empty, Kpi, ProductIcon, Ring, Skeleton, StateIcon } from "../components/ui";
import { ago, fmtDate, fmtInt } from "../format";
import { useApi } from "../hooks";
import { PRODUCT_COLOR, PRODUCT_NAMES, PRODUCT_SHORT, PRODUCTS, STATE_COLOR, STATE_ORDER, stateLabel } from "../labels";
import type { Product, ProductSummary, StateEvent, Summary, TrendPoint } from "../types";

type Trend = Record<Product, TrendPoint[]>;

/** Oxirgi nuqta va ~7 kun oldingi nuqta orasidagi qamrov farqi (foiz punktlarda). */
function weekDelta(points: TrendPoint[] | undefined): number | null {
  const pts = (points ?? []).filter((p) => p.coverage !== null);
  if (pts.length < 2) return null;
  const last = pts[pts.length - 1];
  const target = new Date(last.ts).getTime() - 7 * 86400000;
  let best = pts[0];
  for (const p of pts) if (new Date(p.ts).getTime() <= target) best = p;
  return (last.coverage as number) - (best.coverage as number);
}

export function Overview() {
  const { data: s, error } = useApi<Summary>("/api/summary", 30000);
  const [days, setDays] = useState(30);
  const { data: trend } = useApi<Trend>(`/api/trend?days=${days}`, 300000);
  const { data: week } = useApi<Trend>("/api/trend?days=8", 300000);
  const { data: events } = useApi<StateEvent[]>("/api/events?limit=40", 60000);

  if (error) return <Banner title="Ma'lumot olinmadi">{error}</Banner>;
  if (!s) return <OverviewSkeleton />;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Umumiy ko'rinish</h1>
          <p>
            So'nggi 7 kunda tarmoqda ko'ringan {fmtInt(s.hosts_recent)} ta kompyuter
            {s.status.engine && <span className="muted"> · yangilandi {ago(s.status.engine.last_eval)}</span>}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <a className="btn" href="/api/hosts.csv?problems=true"><Download size={16} />Hisobot (CSV)</a>
          <Link className="btn primary" to="/hosts?problems=true">Muammoli kompyuterlar <ArrowRight size={16} /></Link>
        </div>
      </div>

      <Banners s={s} />

      <div className="grid g-kpi">
        <div className="card hero">
          <div style={{ position: "relative" }}>
            <Ring value={s.compliance} size={84} stroke={9} color={s.compliance !== null && s.compliance >= 95 ? "var(--good)" : "var(--accent)"} />
          </div>
          <div>
            <div className="hero-title">Umumiy muvofiqlik</div>
            <div className="hero-value num">{s.compliance ?? "—"}<small>%</small></div>
            <div className="hero-sub">{fmtInt(s.compliant)} / {fmtInt(s.judged_hosts)} kompyuterda barcha agentlar joyida</div>
          </div>
        </div>
        <Kpi
          label="Hozir onlayn" icon={s.hosts_online ? Wifi : WifiOff} tone="accent" to="/hosts?scope=online"
          value={fmtInt(s.hosts_online)}
          sub={s.hosts_recent ? `${Math.round((s.hosts_online / s.hosts_recent) * 100)}% kompyuterlar tarmoqda` : undefined}
        />
        <Kpi
          label="Muammoli kompyuterlar" icon={ShieldAlert} tone="bad" to="/hosts?problems=true"
          value={fmtInt(s.hosts_with_problems)} sub="kamida bitta agent bo'yicha"
        />
        <Kpi
          label="Noma'lum qurilmalar" icon={Radar} tone="warn" to="/unknown"
          value={fmtInt(s.unknown_devices)} sub="inventarda yo'q, 24 soatda faol"
        />
      </div>

      <div className="grid g-4 section">
        {PRODUCTS.map((p) => (
          <ProductCard key={p} product={p} d={s.products[p]} delta={weekDelta(week?.[p])} />
        ))}
      </div>

      <div className="grid g-main section">
        <div className="card">
          <div className="card-head">
            <div>
              <h2>Qamrov dinamikasi</h2>
              <div className="sub">Har bir mahsulot bo'yicha ishlayotgan agentlar ulushi</div>
            </div>
            <div className="seg" role="tablist" aria-label="Davr">
              {[7, 30, 90].map((d) => (
                <button key={d} className={days === d ? "on" : ""} onClick={() => setDays(d)} role="tab" aria-selected={days === d}>
                  {d} kun
                </button>
              ))}
            </div>
          </div>
          <div className="card-body">
            {trend ? <TrendChart data={trend} products={PRODUCTS} /> : <Skeleton h={280} />}
          </div>
        </div>

        <div className="card" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div className="card-head">
            <div>
              <h2>So'nggi hodisalar</h2>
              <div className="sub">Yangi muammolar va tiklanishlar, 7 kun</div>
            </div>
            <History size={18} className="muted" />
          </div>
          <div className="feed" style={{ maxHeight: 382, overflowY: "auto" }}>
            {events?.map((e, i) => <EventRow key={i} e={e} />)}
            {events && !events.length && <Empty icon={History}>So'nggi 7 kunda o'zgarish bo'lmagan</Empty>}
            {!events && <div style={{ padding: 20 }}><Skeleton h={200} /></div>}
          </div>
        </div>
      </div>

      <div className="card section">
        <div className="card-head">
          <div>
            <h2>Saytlar bo'yicha holat</h2>
            <div className="sub">Muammoli kompyuterlar ulushi va mahsulotlar kesimida soni</div>
          </div>
        </div>
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Sayt</th>
                <th className="r">Kompyuterlar</th>
                <th style={{ width: "28%" }}>Muammoli ulush</th>
                {PRODUCTS.map((p) => (
                  <th key={p} className="r">{PRODUCT_SHORT[p]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {s.sites.map((r, _i, all) => {
                const share = r.hosts ? (r.problem_hosts / r.hosts) * 100 : 0;
                const maxShare = Math.max(...all.map((x) => (x.hosts ? (x.problem_hosts / x.hosts) * 100 : 0)), 1);
                return (
                  <tr key={r.site}>
                    <td><Link className="host-name" to={`/hosts?site=${encodeURIComponent(r.site)}&problems=true`}>{r.site}</Link></td>
                    <td className="r num">{fmtInt(r.hosts)}</td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <div className="bar-track" style={{ flex: 1 }}>
                          <div className="bar-fill" style={{ width: `${(share / maxShare) * 100}%`, background: share >= 15 ? "var(--critical)" : share >= 8 ? "var(--serious)" : "var(--warning)" }} />
                        </div>
                        <span className="num nowrap" style={{ width: 92, textAlign: "right" }}>
                          <b>{fmtInt(r.problem_hosts)}</b> <span className="muted">· {share.toFixed(1)}%</span>
                        </span>
                      </div>
                    </td>
                    {PRODUCTS.map((p) => (
                      <td key={p} className="r num">
                        {r[p] ? <Link className="link" to={`/hosts?site=${encodeURIComponent(r.site)}&product=${p}&state=PROBLEM`}>{r[p]}</Link> : <span className="muted">0</span>}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function ProductCard({ product, d, delta }: { product: Product; d: ProductSummary; delta: number | null }) {
  const states = STATE_ORDER.filter((st) => (d.counts[st] ?? 0) > 0);
  const total = states.reduce((a, st) => a + (d.counts[st] ?? 0), 0) || 1;
  return (
    <div className="card product">
      <div className="product-head">
        <ProductIcon product={product} />
        <div>
          <div className="product-name">{PRODUCT_NAMES[product]}</div>
          <div className="product-meta">{fmtInt(d.judged)} ta baholangan</div>
        </div>
      </div>
      <div className="product-cov">
        <span className="v num">{d.coverage ?? "—"}<small>%</small></span>
        <Delta value={delta} />
        {delta !== null && <span className="muted" style={{ fontSize: 12 }}>7 kunda</span>}
      </div>
      <div className="meter" role="img" aria-label="Holatlar ulushi">
        {states.map((st) => (
          <span key={st} title={`${stateLabel(product, st)}: ${d.counts[st]}`}
            style={{ flex: (d.counts[st] ?? 0) / total, background: STATE_COLOR[st] }} />
        ))}
      </div>
      <div className="state-rows">
        {states.map((st) => (
          <Link key={st} to={`/hosts?product=${product}&state=${st}`}>
            <StateIcon state={st} />
            <span className={st === "OK" ? "ink-2" : ""}>{stateLabel(product, st)}</span>
            <span className="n">{fmtInt(d.counts[st])}</span>
            <span className="pct">{(((d.counts[st] ?? 0) / total) * 100).toFixed(1)}%</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

function EventRow({ e }: { e: StateEvent }) {
  const recovered = e.state === "OK";
  return (
    <Link className="feed-item" to={`/hosts/${e.host_id}`}>
      <span className="feed-ico" style={{ ["--c" as string]: STATE_COLOR[e.state] }}>
        <StateIcon state={e.state} size={15} />
      </span>
      <div className="feed-body">
        <div>
          <b>{e.display_name}</b>{" "}
          <span className="ink-2">
            {recovered ? "tiklandi" : stateLabel(e.product, e.state).toLowerCase()}
          </span>
        </div>
        <div className="t">
          <span className="swatch" style={{ background: PRODUCT_COLOR[e.product], width: 8, height: 8, marginRight: 6 }} />
          {PRODUCT_NAMES[e.product]}{e.site ? ` · ${e.site}` : ""}
        </div>
      </div>
      <span className="feed-time" title={fmtDate(e.started_at)}>{ago(e.started_at)}</span>
    </Link>
  );
}

function Banners({ s }: { s: Summary }) {
  const col = s.status.collector;
  return (
    <>
      {s.incidents.map((i) =>
        i.kind === "mass_outage" ? (
          <Banner key={i.id} title={`Ommaviy uzilish: ${PRODUCT_NAMES[i.product as Product]}`}>
            {i.details.silent} / {i.details.base} ta oldin ishlab turgan agent birdan jim bo'ldi ({fmtDate(i.started_at)} dan beri).
            Bu alohida kompyuterlar emas, server yoki tarmoq muammosi bo'lishi mumkin — yangi xulosalar to'xtatib turilgan.
          </Banner>
        ) : (
          <Banner key={i.id} title="NetFlow ma'lumoti kelmayapti">
            {fmtDate(i.started_at)} dan beri kollektorga hodisa kelmadi. Holatlar muzlatilgan — ko'rsatilayotgan ma'lumot eskirgan bo'lishi mumkin.
          </Banner>
        ),
      )}
      {col && !col.stale && !col.update_events_seen && (
        <Banner tone="warn" title="FTD'dan flow-update hodisalari kelmayapti">
          Uzoq ochiq turadigan agent ulanishlari "jim" ko'rinishi mumkin. FTD FlexConfig'da <span className="mono">flow-export active refresh-interval</span> sozlang.
        </Banner>
      )}
    </>
  );
}

function OverviewSkeleton() {
  return (
    <>
      <div className="page-head"><div><Skeleton h={26} w={240} /><div style={{ height: 8 }} /><Skeleton h={14} w={360} /></div></div>
      <div className="grid g-kpi">{[0, 1, 2, 3].map((i) => <div key={i} className="card card-pad"><Skeleton h={80} /></div>)}</div>
      <div className="grid g-4 section">{[0, 1, 2, 3].map((i) => <div key={i} className="card card-pad"><Skeleton h={220} /></div>)}</div>
    </>
  );
}
