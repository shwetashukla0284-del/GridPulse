"use client";

import { useApiQuery } from "@/hooks/useApiQuery";
import {
  AnomalyAlert,
  HistoryBucket,
  isAlertList,
  isHistoryBuckets,
  isKpiSummary,
  KpiSummary,
} from "@/types/readings";

export const HISTORY_RANGES = {
  "1h": { label: "1 hour", minutes: 60, bucketSeconds: 60 },
  "6h": { label: "6 hours", minutes: 360, bucketSeconds: 300 },
  "24h": { label: "24 hours", minutes: 1440, bucketSeconds: 900 },
} as const;

export type HistoryRange = keyof typeof HISTORY_RANGES;

export function useHistory(range: HistoryRange, refreshKey: number) {
  const { minutes, bucketSeconds } = HISTORY_RANGES[range];
  const { data, ...rest } = useApiQuery<HistoryBucket[]>(
    "/readings/history",
    minutes,
    `bucket_seconds=${bucketSeconds}`,
    isHistoryBuckets,
    refreshKey,
  );
  return { ...rest, buckets: data ?? [] };
}

export function useKpis(range: HistoryRange, refreshKey: number) {
  return useApiQuery<KpiSummary>(
    "/analytics/kpis",
    HISTORY_RANGES[range].minutes,
    "",
    isKpiSummary,
    refreshKey,
  );
}

const ALERT_HISTORY_MINUTES = 1440;

/** Stored alerts for the last 24 hours (newest first, at most `limit`). */
export function useStoredAlerts(limit: number, refreshKey: number) {
  return useApiQuery<AnomalyAlert[]>(
    "/alerts",
    ALERT_HISTORY_MINUTES,
    `limit=${limit}`,
    isAlertList,
    refreshKey,
  );
}
