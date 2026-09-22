import { Check, EyeOff, Radar, RotateCcw, Search, ShieldQuestion, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { Empty, Kpi, Skeleton } from "../components/ui";
import { ago, duration, fmtDate, fmtInt } from "../format";
import { useApi } from "../hooks";
import { PRODUCT_COLOR, PRODUCT_SHORT } from "../labels";
import type { Product } from "../types";
import { canEdit } from "../session";

interface Device {
  ip: string;
  site: string | null;
  first_seen: string;
  alive_since: string;
  last_seen: string;
  signals: Partial<Record<string, string>>;
  dns_name: string | null;
  prev_owner: string | null;
}

interface Ignored {
  ip: string;
  note: string | null;
  created_by: string | null;
  created_at: string;
}

const PAGE = 200;
const API_LIMIT = 20000;

const PERIODS = [
  [1, "1 soat"],
  [24, "24 soat"],
  [168, "7 kun"],
] as const;

export function Unknown() {
  const [hours, setHours] = useState(24);
  const [q, setQ] = useState("");
  const { data: resp, reload } = useApi<{ items: Device[]; dc_known: boolean }>(`/api/unknown?hours=${hours}`, 60000);
  const data = resp?.items;
  const dcKnown = resp?.dc_known ?? true;
  const { data: ignored, reload: reloadIgnored } = useApi<Ignored[]>("/api/unknown/ignored");
  const [editing, setEditing] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [shown, setShown] = useState(PAGE);

  async function ignore(ip: string) {
    await api("/api/unknown/ignore", { method: "POST", json: { ip, note } });
    setEditing(null);
    setNote("");
    reload();
    reloadIgnored();
  }
  async function restore(ip: string) {
    await api(`/api/unknown/ignore/${encodeURIComponent(ip)}`, { method: "DELETE" });
    reload();
    reloadIgnored();
  }

  const needle = q.trim().toLowerCase();
  const rows = (data ?? []).filter((d) => !needle || d.ip.includes(needle) || (d.site ?? "").toLowerCase().includes(needle) || (d.dns_name ?? "").includes(needle));
  const withDc = (data ?? []).filter((d) => d.signals["ad-auth"]).length;
  const total = data?.length ?? 0;
  const visible = rows.slice(0, shown);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Noma'lum qurilmalar</h1>
          <p>Foydalanuvchi tarmoqlarida faol, lekin AD, DNS, Cortex va Kaspersky inventarida yo'q IP manzillar</p>
        </div>
        <div className="seg" role="tablist" aria-label="Davr">
          {PERIODS.map(([h, label]) => (
            <button key={h} className={hours === h ? "on" : ""} onClick={() => setHours(h)}>{label}</button>
          ))}
        </div>
      </div>

      <div className="grid g-kpi" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
        <Kpi label="Noma'lum qurilmalar" icon={Radar} tone="warn" value={data ? fmtInt(total) : "—"} sub="tanlangan davrda faol bo'lgan" />
        {dcKnown ? (
          <Kpi label="Domen trafigi yo'q" icon={ShieldQuestion} tone="bad" value={data ? fmtInt(total - withDc) : "—"}
            sub="shaxsiy qurilma yoki domenga kirmagan kompyuter" />
        ) : (
          <Kpi label="Agent trafigi yo'q" icon={ShieldQuestion} tone="bad"
            value={data ? fmtInt(data.filter((d) => !d.signals.cortex && !d.signals.ksc && !d.signals.si).length) : "—"}
            sub="Cortex, Kaspersky va SI serverlariga murojaat yo'q" />
        )}
        <Kpi label="Ma'lum deb belgilangan" icon={EyeOff} value={ignored ? fmtInt(ignored.length) : "—"} sub="printer, telefon va boshqalar" />
      </div>

      <div className="card section">
        <div className="toolbar">
          <div className="input-icon">
            <Search size={15} />
            <input className="input" placeholder="IP, sayt yoki DNS nomi…" value={q} onChange={(e) => { setQ(e.target.value); setShown(PAGE); }} />
          </div>
          <span className="muted" style={{ marginLeft: "auto", fontSize: 13 }}>{fmtInt(rows.length)} ta topildi</span>
        </div>
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>IP manzil</th>
                <th>Sayt</th>
                <th>Xulosa</th>
                <th>Agent trafigi</th>
                <th>Birinchi marta</th>
                <th>Oxirgi faollik</th>
                <th className="r">Amal</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((d) => (
                <tr key={d.ip} className={dcKnown && !d.signals["ad-auth"] ? "sev" : ""} style={dcKnown && !d.signals["ad-auth"] ? { ["--sev" as string]: "var(--critical)" } : undefined}>
                  <td>
                    <span className="mono" style={{ fontWeight: 600 }}>{d.ip}</span>
                    {d.dns_name && <div className="host-sub">{d.dns_name}</div>}
                    {d.prev_owner && (
                      <div className="host-sub" title="Bu IP oldin shu kompyuterga tegishli edi, lekin joriy seansda tasdiqlanmadi (DHCP IP'ni boshqa qurilmaga bergan bo'lishi mumkin)">
                        oldin: {d.prev_owner}
                      </div>
                    )}
                  </td>
                  <td className="nowrap">{d.site ?? "—"}</td>
                  <td>
                    {!dcKnown ? (
                      <span className="muted" title="DC IP'lari ma'lum emas (AD ulanmagan, DC_IPS bo'sh) — domen a'zoligini aniqlab bo'lmaydi">
                        DC ma'lum emas
                      </span>
                    ) : d.signals["ad-auth"] ? (
                      <span className="pill" style={{ ["--c" as string]: "var(--warning)" }} title="DC bilan Kerberos/LDAP trafigi bor — domen kompyuteri, lekin nomi inventarga bog'lanmadi">
                        Domen a'zosi, nomi aniqlanmadi
                      </span>
                    ) : (
                      <span className="pill" style={{ ["--c" as string]: "var(--critical)" }}>Domen trafigi yo'q</span>
                    )}
                  </td>
                  <td>
                    <div className="chips">
                      {(["cortex", "ksc", "si"] as Product[]).filter((p) => d.signals[p]).map((p) => (
                        <span key={p} className="pill plain" style={{ height: 22 }}>
                          <span className="swatch" style={{ background: PRODUCT_COLOR[p], width: 8, height: 8 }} />{PRODUCT_SHORT[p]}
                        </span>
                      ))}
                      {!(["cortex", "ksc", "si"] as Product[]).some((p) => d.signals[p]) && <span className="muted">yo'q</span>}
                    </div>
                  </td>
                  <td className="ink-2 nowrap" title={fmtDate(d.first_seen)}>{ago(d.first_seen)}</td>
                  <td className="ink-2 nowrap" title={`Faol seans: ${duration(d.alive_since, new Date(d.last_seen).getTime())}`}>{ago(d.last_seen)}</td>
                  <td className="r nowrap">
                    {editing === d.ip ? (
                      <span style={{ display: "inline-flex", gap: 6 }}>
                        <input className="input" style={{ height: 30, width: 180 }} placeholder="Izoh (masalan, printer)" value={note} onChange={(e) => setNote(e.target.value)} autoFocus
                          onKeyDown={(e) => e.key === "Enter" && ignore(d.ip)} />
                        <button className="btn sm primary" onClick={() => ignore(d.ip)} aria-label="Saqlash"><Check size={14} /></button>
                        <button className="btn sm" onClick={() => setEditing(null)} aria-label="Bekor qilish"><X size={14} /></button>
                      </span>
                    ) : canEdit() ? (
                      <button className="btn sm" onClick={() => (setEditing(d.ip), setNote(""))}><EyeOff size={14} />Ma'lum qurilma</button>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
              {data && !rows.length && <tr><td colSpan={7}><Empty icon={Radar}>Noma'lum qurilma topilmadi</Empty></td></tr>}
              {!data && <tr><td colSpan={7} style={{ padding: 20 }}><Skeleton h={240} /></td></tr>}
            </tbody>
          </table>
        </div>
        {(rows.length > shown || total >= API_LIMIT) && (
          <div className="pager">
            <span>
              {fmtInt(Math.min(shown, rows.length))} / {fmtInt(rows.length)} ko'rsatilmoqda
              {total >= API_LIMIT && <span className="muted"> · ro'yxat {fmtInt(API_LIMIT)} ta bilan cheklangan, qidiruvdan foydalaning</span>}
            </span>
            {rows.length > shown && <button className="btn sm" onClick={() => setShown((n) => n + PAGE)}>Yana {PAGE} ta ko'rsatish</button>}
          </div>
        )}
      </div>

      {!!ignored?.length && (
        <div className="card section">
          <div className="card-head">
            <div>
              <h2>Ma'lum deb belgilanganlar</h2>
              <div className="sub">Bu IP'lar ro'yxatda ko'rsatilmaydi</div>
            </div>
          </div>
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr><th>IP</th><th>Izoh</th><th>Kim belgilagan</th><th>Qachon</th><th /></tr>
              </thead>
              <tbody>
                {ignored.map((r) => (
                  <tr key={r.ip}>
                    <td className="mono">{r.ip}</td>
                    <td>{r.note ?? <span className="muted">—</span>}</td>
                    <td>{r.created_by ?? "—"}</td>
                    <td className="ink-2">{fmtDate(r.created_at)}</td>
                    <td className="r">{canEdit() && <button className="btn sm" onClick={() => restore(r.ip)}><RotateCcw size={14} />Qaytarish</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
