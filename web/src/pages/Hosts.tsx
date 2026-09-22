import { ChevronLeft, ChevronRight, Download, Laptop, Search, SearchX, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { qs } from "../api";
import { Banner, Empty, Skeleton, StatePill } from "../components/ui";
import { ago, fmtDate, fmtInt } from "../format";
import { useApi } from "../hooks";
import { PRODUCT_NAMES, PRODUCT_SHORT, PRODUCTS, STATE_COLOR, STATE_ORDER, STATE_SEVERITY, stateLabel } from "../labels";
import type { HostRow, Product, State } from "../types";

const SIZE = 50;
const SCOPES = [
  ["recent", "7 kun"],
  ["online", "Onlayn"],
  ["all", "Hammasi"],
] as const;

/** Qatordagi eng jiddiy muammo rangi — jadval chap chizig'i uchun. */
function severityColor(h: HostRow): string | null {
  let worst: State | null = null;
  for (const p of PRODUCTS) {
    const st = h.states[p]?.eff;
    if (st && STATE_SEVERITY[st] > 0 && (!worst || STATE_SEVERITY[st] > STATE_SEVERITY[worst])) worst = st;
  }
  return worst && worst !== "PENDING" ? STATE_COLOR[worst] : null;
}

export function Hosts() {
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const f = {
    q: params.get("q") ?? "",
    product: params.get("product") ?? "",
    state: params.get("state") ?? "",
    site: params.get("site") ?? "",
    scope: params.get("scope") ?? "recent",
    problems: params.get("problems") === "true",
    excluded: params.get("excluded") === "true",
    sort: params.get("sort") ?? "problems",
  };
  const page = Math.max(1, Math.floor(Number(params.get("page")) || 1));
  const [q, setQ] = useState(f.q);

  function set(patch: Record<string, string | boolean>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === "" || v === false) next.delete(k);
      else next.set(k, String(v));
    }
    if (!("page" in patch)) next.delete("page");
    setParams(next, { replace: true });
  }

  // URL tashqaridan o'zgarsa (havola, orqaga tugmasi) — qidiruv maydonini ham yangilaymiz.
  useEffect(() => setQ(f.q), [f.q]);

  // Qidiruv — yozib bo'lgach (debounce).
  useEffect(() => {
    const t = setTimeout(() => q !== f.q && set({ q }), 300);
    return () => clearTimeout(t);
  }, [q]); // eslint-disable-line react-hooks/exhaustive-deps

  const query = qs({ ...f, page, size: SIZE });
  const { data, loading, error } = useApi<{ total: number; items: HostRow[] }>(`/api/hosts${query}`, 60000);
  const { data: sites } = useApi<string[]>("/api/sites");
  const pages = data ? Math.max(1, Math.ceil(data.total / SIZE)) : 1;

  const chips: Array<[string, () => void]> = [];
  if (f.q) chips.push([`Qidiruv: ${f.q}`, () => set({ q: "" })]);
  if (f.product) chips.push([
    `${PRODUCT_NAMES[f.product as Product]}${f.state ? ": " + (f.state === "PROBLEM" ? "har qanday muammo" : stateLabel(f.product as Product, f.state as State)) : ""}`,
    () => set({ product: "", state: "" }),
  ]);
  if (f.site) chips.push([`Sayt: ${f.site}`, () => set({ site: "" })]);
  if (f.problems) chips.push(["Faqat muammolilar", () => set({ problems: false })]);
  if (f.excluded) chips.push(["Istisnolar bilan", () => set({ excluded: false })]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Kompyuterlar</h1>
          <p>{data ? <><b className="num">{fmtInt(data.total)}</b> ta kompyuter topildi</> : " "}</p>
        </div>
        <a className="btn" href={`/api/hosts.csv${qs({ ...f })}`}><Download size={16} />Excel'ga eksport</a>
      </div>

      {error && <Banner title="Ma'lumot olinmadi">{error}</Banner>}

      <div className="card">
        <div className="toolbar">
          <div className="input-icon">
            <Search size={15} />
            <input className="input" placeholder="Nom, IP yoki FQDN…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Qidiruv" />
          </div>
          <select className="select" value={f.product} onChange={(e) => set({ product: e.target.value, state: e.target.value ? f.state : "" })} aria-label="Mahsulot">
            <option value="">Barcha mahsulotlar</option>
            {PRODUCTS.map((p) => <option key={p} value={p}>{PRODUCT_NAMES[p]}</option>)}
          </select>
          <select className="select" value={f.state} disabled={!f.product} onChange={(e) => set({ state: e.target.value })} aria-label="Holat">
            <option value="">Istalgan holat</option>
            <option value="PROBLEM">Har qanday muammo</option>
            {STATE_ORDER.map((s) => <option key={s} value={s}>{stateLabel((f.product || null) as Product | null, s)}</option>)}
          </select>
          <select className="select" value={f.site} onChange={(e) => set({ site: e.target.value })} aria-label="Sayt">
            <option value="">Barcha saytlar</option>
            {(sites ?? []).map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <div className="seg" role="tablist" aria-label="Qamrov">
            {SCOPES.map(([v, label]) => (
              <button key={v} className={f.scope === v ? "on" : ""} onClick={() => set({ scope: v === "recent" ? "" : v })}>{label}</button>
            ))}
          </div>
          <label className="check">
            <input type="checkbox" checked={f.problems} onChange={(e) => set({ problems: e.target.checked })} />
            Faqat muammolilar
          </label>
          <select className="select" value={f.sort} onChange={(e) => set({ sort: e.target.value })} aria-label="Saralash" style={{ marginLeft: "auto" }}>
            <option value="problems">Saralash: avval muammolilar</option>
            <option value="name">Saralash: nom</option>
            <option value="site">Saralash: sayt</option>
            <option value="last_alive">Saralash: oxirgi faollik</option>
          </select>
        </div>

        {chips.length > 0 && (
          <div className="chips" style={{ padding: "10px 16px", borderBottom: "1px solid var(--border)" }}>
            {chips.map(([label, clear]) => (
              <span className="chip" key={label}>
                {label}
                <button onClick={clear} aria-label="Filtrni olib tashlash"><X size={13} /></button>
              </span>
            ))}
            <button className="btn sm" style={{ border: 0 }} onClick={() => setParams({}, { replace: true })}>Hammasini tozalash</button>
          </div>
        )}

        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>Kompyuter</th>
                <th>IP manzil</th>
                <th>Sayt</th>
                {PRODUCTS.map((p) => <th key={p}>{PRODUCT_SHORT[p]}</th>)}
                <th className="r">Oxirgi faollik</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((h) => {
                const sev = severityColor(h);
                return (
                  <tr key={h.id} className={`click${sev ? " sev" : ""}`} style={sev ? { ["--sev" as string]: sev } : undefined} onClick={() => nav(`/hosts/${h.id}`)}>
                    <td>
                      <div className="host-cell">
                        <span className="host-ico">
                          <Laptop size={16} />
                          <span className="on" style={{ background: h.online ? "var(--good)" : "var(--neutral)" }} title={h.online ? "Onlayn" : "Oflayn"} />
                        </span>
                        <div>
                          <div className="host-name">
                            {h.name}
                            {h.excluded && <span className="pill plain" style={{ marginLeft: 8, height: 20 }}>istisno</span>}
                          </div>
                          <div className="host-sub">{h.os ?? "—"}</div>
                        </div>
                      </div>
                    </td>
                    <td className="mono">{h.ips.join(", ") || <span className="muted">—</span>}</td>
                    <td className="nowrap">{h.site ?? <span className="muted">—</span>}</td>
                    {PRODUCTS.map((p) => {
                      const s = h.states[p];
                      const transient = s && (s.state === "OFFLINE" || s.state === "PENDING") && s.eff !== s.state;
                      return (
                        <td key={p}>
                          <StatePill product={p} state={s?.eff} faded={!!transient}
                            title={[s?.reason, transient ? "(kompyuter hozir oflayn — oxirgi ma'lum holat)" : ""].filter(Boolean).join(" ")} />
                        </td>
                      );
                    })}
                    <td className="r ink-2 nowrap" title={fmtDate(h.last_alive)}>{ago(h.last_alive)}</td>
                  </tr>
                );
              })}
              {data && !data.items.length && (
                <tr><td colSpan={4 + PRODUCTS.length}><Empty icon={SearchX}>Filtrlarga mos kompyuter topilmadi</Empty></td></tr>
              )}
              {!data && loading && (
                <tr><td colSpan={4 + PRODUCTS.length} style={{ padding: 20 }}><Skeleton h={320} /></td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="pager">
          <span>
            {data && data.total > 0
              ? <>Ko'rsatilmoqda <b className="num">{fmtInt((page - 1) * SIZE + 1)}–{fmtInt(Math.min(page * SIZE, data.total))}</b> / {fmtInt(data.total)}</>
              : " "}
          </span>
          <div className="btns">
            <button className="btn sm" disabled={page <= 1} onClick={() => set({ page: String(page - 1) })} aria-label="Oldingi sahifa"><ChevronLeft size={15} /></button>
            <span className="num" style={{ padding: "0 6px" }}>{page} / {pages}</span>
            <button className="btn sm" disabled={page >= pages} onClick={() => set({ page: String(page + 1) })} aria-label="Keyingi sahifa"><ChevronRight size={15} /></button>
          </div>
        </div>
      </div>
    </>
  );
}
