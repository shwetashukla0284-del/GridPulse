import type { AnomalyAlert } from "../types/readings";

/** Same identity as the database primary key: one alert per meter per timestamp. */
export function alertKey(alert: AnomalyAlert): string {
  // Parse the time so "...Z" and "...+00:00" spellings of one instant match.
  return `${alert.meter_id}|${Date.parse(alert.timestamp)}`;
}

/**
 * Combine alert lists (e.g. live WebSocket and stored REST history), keeping
 * one copy of each alert, newest first, at most `max` entries. Earlier lists
 * win on duplicates.
 */
export function mergeAlerts(lists: AnomalyAlert[][], max: number): AnomalyAlert[] {
  const byKey = new Map<string, AnomalyAlert>();
  for (const list of lists) {
    for (const alert of list) {
      const key = alertKey(alert);
      if (!byKey.has(key)) byKey.set(key, alert);
    }
  }

  return [...byKey.values()]
    .sort(
      (left, right) =>
        Date.parse(right.timestamp) - Date.parse(left.timestamp) ||
        left.meter_id.localeCompare(right.meter_id),
    )
    .slice(0, max);
}
