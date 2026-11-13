import { describe, expect, it } from "vitest";

import {
  isAlertList,
  isHistoryBuckets,
  isKpiSummary,
  isReadingBatch,
  type Reading,
} from "@/types/readings";

const reading: Reading = {
  meter_id: "meter-001",
  timestamp: "2026-11-12T17:45:00Z",
  demand_kw: 2.5,
  solar_kw: 1,
  net_grid_kw: 1.5,
  is_fault: false,
};

const alert = {
  meter_id: "meter-001",
  timestamp: "2026-11-12T17:45:00Z",
  demand_kw: 10.18,
  baseline_mean_kw: 1.2,
  baseline_std_kw: 0.1,
  z_score: 89.5,
};

describe("isReadingBatch", () => {
  it("accepts a batch with alerts and one without (older server)", () => {
    expect(isReadingBatch({ type: "readings", readings: [reading], alerts: [alert] })).toBe(true);
    expect(isReadingBatch({ type: "readings", readings: [reading] })).toBe(true);
  });

  it("rejects malformed batches and readings", () => {
    expect(isReadingBatch(null)).toBe(false);
    expect(isReadingBatch({ type: "other", readings: [] })).toBe(false);
    expect(isReadingBatch({ type: "readings", readings: [{ ...reading, demand_kw: -1, net_grid_kw: -2 }] })).toBe(false);
    expect(isReadingBatch({ type: "readings", readings: [{ ...reading, timestamp: "not a date" }] })).toBe(false);
  });

  it("rejects readings whose net power is inconsistent", () => {
    expect(isReadingBatch({ type: "readings", readings: [{ ...reading, net_grid_kw: 9 }] })).toBe(false);
  });

  it("rejects batches with malformed alerts", () => {
    expect(isReadingBatch({ type: "readings", readings: [reading], alerts: [{ meter_id: "m" }] })).toBe(false);
    expect(isReadingBatch({ type: "readings", readings: [reading], alerts: "none" })).toBe(false);
  });
});

describe("isAlertList", () => {
  it("accepts valid alerts and rejects non-positive std or non-finite z", () => {
    expect(isAlertList([alert])).toBe(true);
    expect(isAlertList([])).toBe(true);
    expect(isAlertList([{ ...alert, baseline_std_kw: 0 }])).toBe(false);
    // JSON cannot carry Infinity, but a buggy producer could still send a string.
    expect(isAlertList([{ ...alert, z_score: "89.5" }])).toBe(false);
    expect(isAlertList({})).toBe(false);
  });
});

describe("isHistoryBuckets", () => {
  const bucket = {
    bucket_start: "2026-11-12T17:00:00Z",
    avg_demand_kw: 2,
    avg_solar_kw: 1,
    avg_net_grid_kw: 1,
    max_net_grid_kw: 1.5,
    reading_count: 10,
    fault_count: 0,
  };

  it("accepts valid buckets and rejects missing or non-numeric fields", () => {
    expect(isHistoryBuckets([bucket])).toBe(true);
    expect(isHistoryBuckets([{ ...bucket, reading_count: undefined }])).toBe(false);
    expect(isHistoryBuckets([{ ...bucket, avg_demand_kw: "2" }])).toBe(false);
  });
});

describe("isKpiSummary", () => {
  const kpis = {
    start: "2026-11-12T16:00:00Z",
    end: "2026-11-12T17:00:00Z",
    meter_count: 2,
    reading_count: 50,
    fault_count: 2,
    demand_kwh: 0.61,
    solar_kwh: 3.74,
    import_kwh: 0.08,
    export_kwh: 3.21,
    solar_coverage_pct: 86.7,
    peak_net_grid_kw: -1.38,
    peak_at: "2026-11-12T16:04:00Z",
  };

  it("accepts a full summary and the empty-range nulls", () => {
    expect(isKpiSummary(kpis)).toBe(true);
    expect(isKpiSummary({ ...kpis, solar_coverage_pct: null, peak_net_grid_kw: null, peak_at: null })).toBe(true);
  });

  it("rejects missing energy values and invalid peak times", () => {
    expect(isKpiSummary({ ...kpis, import_kwh: undefined })).toBe(false);
    expect(isKpiSummary({ ...kpis, peak_at: "yesterday" })).toBe(false);
  });
});
