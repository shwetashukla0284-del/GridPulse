export type Reading = {
  meter_id: string;
  timestamp: string;
  demand_kw: number;
  solar_kw: number;
  net_grid_kw: number;
  is_fault: boolean;
};

export type AnomalyAlert = {
  meter_id: string;
  timestamp: string;
  demand_kw: number;
  baseline_mean_kw: number;
  baseline_std_kw: number;
  z_score: number;
};

export type ReadingBatch = {
  type: "readings";
  readings: Reading[];
  /** Absent from servers that predate anomaly detection. */
  alerts?: AnomalyAlert[];
};

export type ChartPoint = {
  timestamp: string;
  demand_kw: number;
  solar_kw: number;
  net_grid_kw: number;
};

export type HistoryBucket = {
  bucket_start: string;
  avg_demand_kw: number;
  avg_solar_kw: number;
  avg_net_grid_kw: number;
  max_net_grid_kw: number;
  reading_count: number;
  fault_count: number;
};

export function isHistoryBuckets(value: unknown): value is HistoryBucket[] {
  if (!Array.isArray(value)) return false;

  return value.every((item: unknown) => {
    if (typeof item !== "object" || item === null) return false;
    const bucket = item as Record<string, unknown>;
    const numericFields = [
      "avg_demand_kw",
      "avg_solar_kw",
      "avg_net_grid_kw",
      "max_net_grid_kw",
      "reading_count",
      "fault_count",
    ];

    return (
      typeof bucket.bucket_start === "string" &&
      Number.isFinite(Date.parse(bucket.bucket_start)) &&
      numericFields.every(
        (field) => typeof bucket[field] === "number" && Number.isFinite(bucket[field]),
      )
    );
  });
}

export type KpiSummary = {
  start: string;
  end: string;
  meter_count: number;
  reading_count: number;
  fault_count: number;
  demand_kwh: number;
  solar_kwh: number;
  import_kwh: number;
  export_kwh: number;
  solar_coverage_pct: number | null;
  peak_net_grid_kw: number | null;
  peak_at: string | null;
};

const isFiniteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

export function isKpiSummary(value: unknown): value is KpiSummary {
  if (typeof value !== "object" || value === null) return false;
  const kpi = value as Record<string, unknown>;
  const required = [
    "meter_count",
    "reading_count",
    "fault_count",
    "demand_kwh",
    "solar_kwh",
    "import_kwh",
    "export_kwh",
  ];

  return (
    required.every((field) => isFiniteNumber(kpi[field])) &&
    (kpi.solar_coverage_pct === null || isFiniteNumber(kpi.solar_coverage_pct)) &&
    (kpi.peak_net_grid_kw === null || isFiniteNumber(kpi.peak_net_grid_kw)) &&
    (kpi.peak_at === null ||
      (typeof kpi.peak_at === "string" && Number.isFinite(Date.parse(kpi.peak_at))))
  );
}

export function isAnomalyAlert(value: unknown): value is AnomalyAlert {
  if (typeof value !== "object" || value === null) return false;
  const alert = value as Record<string, unknown>;
  return (
    typeof alert.meter_id === "string" &&
    alert.meter_id.length > 0 &&
    typeof alert.timestamp === "string" &&
    Number.isFinite(Date.parse(alert.timestamp)) &&
    isFiniteNumber(alert.demand_kw) &&
    isFiniteNumber(alert.baseline_mean_kw) &&
    isFiniteNumber(alert.baseline_std_kw) &&
    alert.baseline_std_kw > 0 &&
    isFiniteNumber(alert.z_score)
  );
}

export function isAlertList(value: unknown): value is AnomalyAlert[] {
  return Array.isArray(value) && value.every(isAnomalyAlert);
}

export function isReadingBatch(value: unknown): value is ReadingBatch {
  if (typeof value !== "object" || value === null || !("type" in value) || !("readings" in value)) {
    return false;
  }

  const candidate = value as { type: unknown; readings: unknown; alerts?: unknown };
  if (candidate.type !== "readings" || !Array.isArray(candidate.readings)) {
    return false;
  }
  if (candidate.alerts !== undefined && !isAlertList(candidate.alerts)) {
    return false;
  }

  return candidate.readings.every((item: unknown) => {
    if (typeof item !== "object" || item === null) return false;
    const reading = item as Record<string, unknown>;

    return (
      typeof reading.meter_id === "string" &&
      reading.meter_id.length > 0 &&
      typeof reading.timestamp === "string" &&
      Number.isFinite(Date.parse(reading.timestamp)) &&
      typeof reading.demand_kw === "number" &&
      Number.isFinite(reading.demand_kw) &&
      reading.demand_kw >= 0 &&
      typeof reading.solar_kw === "number" &&
      Number.isFinite(reading.solar_kw) &&
      reading.solar_kw >= 0 &&
      typeof reading.net_grid_kw === "number" &&
      Number.isFinite(reading.net_grid_kw) &&
      Math.abs(reading.net_grid_kw - (reading.demand_kw - reading.solar_kw)) < 1e-8 &&
      typeof reading.is_fault === "boolean"
    );
  });
}
