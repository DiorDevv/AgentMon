import { useEffect, useMemo, useRef, useState } from "react";
import { fmtDate, fmtShortDate } from "../format";
import { PRODUCT_COLOR, PRODUCT_SHORT } from "../labels";
import type { Product, TrendPoint } from "../types";

const H = 290;
const M = { top: 12, right: 76, bottom: 26, left: 40 };

interface Series {
  product: Product;
  points: { t: number; v: number; raw: TrendPoint }[];
}

/** Qamrov (%) dinamikasi: bitta o'q, 2px chiziqlar, oxirida to'g'ridan-to'g'ri yorliqlar, crosshair tooltip. */
export function TrendChart({ data, products }: { data: Record<Product, TrendPoint[]>; products: Product[] }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(800);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(320, e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const series: Series[] = useMemo(
    () =>
      products.map((p) => ({
        product: p,
        points: (data[p] || [])
          .filter((d) => d.coverage !== null)
          .map((d) => ({ t: new Date(d.ts).getTime(), v: d.coverage as number, raw: d })),
      })),
    [data, products],
  );

  const all = series.flatMap((s) => s.points);
  if (!all.length) return <div className="empty">Hali ma'lumot yo'q — statistika har soatda yig'iladi</div>;

  const t0 = Math.min(...all.map((p) => p.t));
  const t1 = Math.max(...all.map((p) => p.t));
  const vmin = Math.min(...all.map((p) => p.v));
  const lo = Math.max(0, Math.floor((vmin - 1) / 5) * 5);
  const hi = 100;
  const iw = w - M.left - M.right;
  const ih = H - M.top - M.bottom;
  const x = (t: number) => M.left + (t1 === t0 ? iw / 2 : ((t - t0) / (t1 - t0)) * iw);
  const y = (v: number) => M.top + (1 - (v - lo) / (hi - lo)) * ih;

  const yTicks: number[] = [];
  const step = hi - lo > 20 ? 10 : 5;
  for (let v = lo; v <= hi; v += step) yTicks.push(v);
  const days = (t1 - t0) / 86400000;
  const xTickEvery = days > 20 ? 7 : days > 6 ? 2 : 1;
  const xTicks: number[] = [];
  const first = new Date(t0);
  first.setHours(0, 0, 0, 0);
  for (let t = first.getTime() + 86400000; t <= t1; t += 86400000 * xTickEvery) xTicks.push(t);

  // Oxirgi qiymat yorliqlari — bir-birini bosmasligi uchun vertikal suriladi.
  const ends = series
    .filter((s) => s.points.length)
    .map((s) => {
      const last = s.points[s.points.length - 1];
      return { product: s.product, v: last.v, y: y(last.v) };
    })
    .sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;

  const times = series.reduce((a, s) => (s.points.length > a.length ? s.points.map((p) => p.t) : a), [] as number[]);
  const hoverT = hover !== null ? times[hover] : null;

  function onMove(e: React.MouseEvent<SVGRectElement>) {
    const r = (e.currentTarget as SVGRectElement).getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * iw;
    const t = t0 + (px / iw) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < times.length; i++) if (Math.abs(times[i] - t) < Math.abs(times[best] - t)) best = i;
    setHover(best);
  }

  return (
    <div className="chart" ref={wrap}>
      <div className="legend" style={{ marginBottom: 12 }}>
        {products.map((p) => (
          <span key={p}>
            <i style={{ background: PRODUCT_COLOR[p] }} />
            {PRODUCT_SHORT[p]}
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${w} ${H}`} role="img" aria-label="Qamrov dinamikasi, foizda">
        {yTicks.map((v) => (
          <g key={v}>
            <line className={v === lo ? "baseline" : "gridline"} x1={M.left} x2={M.left + iw} y1={y(v)} y2={y(v)} />
            <text className="tick" x={M.left - 8} y={y(v) + 4} textAnchor="end">
              {v}%
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={t} className="tick" x={x(t)} y={H - 6} textAnchor="middle">
            {fmtShortDate(new Date(t))}
          </text>
        ))}
        {series.map((s) => (
          <path
            key={s.product}
            d={s.points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("")}
            fill="none"
            stroke={PRODUCT_COLOR[s.product]}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        {ends.map((e) => (
          <text key={e.product} className="dlabel" x={M.left + iw + 8} y={e.y + 4}>
            {PRODUCT_SHORT[e.product]} {e.v.toFixed(1)}%
          </text>
        ))}
        {hoverT !== null && (
          <g>
            <line className="crosshair" x1={x(hoverT)} x2={x(hoverT)} y1={M.top} y2={M.top + ih} />
            {series.map((s) => {
              const p = s.points.find((q) => q.t === hoverT);
              return p ? (
                <circle key={s.product} cx={x(p.t)} cy={y(p.v)} r={4.5} fill={PRODUCT_COLOR[s.product]} stroke="var(--surface)" strokeWidth={2} />
              ) : null;
            })}
          </g>
        )}
        <rect x={M.left} y={M.top} width={iw} height={ih} fill="transparent" onMouseMove={onMove} onMouseLeave={() => setHover(null)} />
      </svg>
      {hoverT !== null && (
        <div
          className="tooltip"
          style={{
            left: Math.min(((x(hoverT) + 14) / w) * 100, 70) + "%",
            top: 30,
          }}
        >
          <div className="t">{fmtDate(new Date(hoverT).toISOString())}</div>
          {series.map((s) => {
            const p = s.points.find((q) => q.t === hoverT);
            return p ? (
              <div className="row" key={s.product}>
                <span className="swatch" style={{ background: PRODUCT_COLOR[s.product] }} />
                {PRODUCT_SHORT[s.product]}
                <b>{p.v.toFixed(1)}%</b>
                <span className="muted num" style={{ minWidth: 44, textAlign: "right" }}>
                  {p.raw.problems} ta
                </span>
              </div>
            ) : null;
          })}
        </div>
      )}
    </div>
  );
}
