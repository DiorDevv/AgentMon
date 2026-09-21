import type { CollectorStatus, EngineStatus } from "./types";

const FIVE_MIN = 5 * 60 * 1000;

/** NetFlow "jonli"mi: server bayrog'i + oxirgi hodisa yangiligi (engine o'zi to'xtab qolgan holatni ham ushlaydi). */
export function collectorLive(col: CollectorStatus | undefined): boolean {
  if (!col || col.stale || !col.last_rx) return false;
  return Date.now() - new Date(col.last_rx).getTime() < FIVE_MIN;
}

/** Engine oxirgi 5 daqiqada baholash o'tkazganmi. */
export function engineFresh(eng: EngineStatus | undefined): boolean {
  return !!eng && Date.now() - new Date(eng.last_eval).getTime() < FIVE_MIN;
}
