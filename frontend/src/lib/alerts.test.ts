import { describe, expect, it } from "vitest";

import { alertKey, mergeAlerts } from "@/lib/alerts";
import type { AnomalyAlert } from "@/types/readings";

function alert(meterId: string, timestamp: string, zScore = 50): AnomalyAlert {
  return {
    meter_id: meterId,
    timestamp,
    demand_kw: 6,
    baseline_mean_kw: 1,
    baseline_std_kw: 0.1,
    z_score: zScore,
  };
}

describe("alertKey", () => {
  it("treats different spellings of the same instant as one alert", () => {
    expect(alertKey(alert("m1", "2026-11-12T17:45:00Z"))).toBe(
      alertKey(alert("m1", "2026-11-12T17:45:00+00:00")),
    );
  });

  it("distinguishes meters at the same time", () => {
    expect(alertKey(alert("m1", "2026-11-12T17:45:00Z"))).not.toBe(
      alertKey(alert("m2", "2026-11-12T17:45:00Z")),
    );
  });
});

describe("mergeAlerts", () => {
  it("de-duplicates an alert seen live and in stored history", () => {
    const live = [alert("m1", "2026-11-12T17:45:00Z")];
    const stored = [alert("m1", "2026-11-12T17:45:00+00:00"), alert("m2", "2026-11-12T17:30:00Z")];

    expect(mergeAlerts([live, stored], 50)).toHaveLength(2);
  });

  it("keeps the copy from the earlier list on duplicates", () => {
    const merged = mergeAlerts(
      [[alert("m1", "2026-11-12T17:45:00Z", 40)], [alert("m1", "2026-11-12T17:45:00Z", 99)]],
      50,
    );

    expect(merged[0].z_score).toBe(40);
  });

  it("orders newest first, breaking ties by meter", () => {
    const merged = mergeAlerts(
      [[alert("m2", "2026-11-12T17:00:00Z"), alert("m1", "2026-11-12T18:00:00Z"), alert("m1", "2026-11-12T17:00:00Z")]],
      50,
    );

    expect(merged.map((a) => `${a.meter_id}@${a.timestamp.slice(11, 13)}`)).toEqual([
      "m1@18",
      "m1@17",
      "m2@17",
    ]);
  });

  it("is bounded to the newest max entries", () => {
    const many = Array.from({ length: 60 }, (_, minute) =>
      alert("m1", new Date(Date.UTC(2026, 10, 12, 17, minute)).toISOString()),
    );
    const merged = mergeAlerts([many], 50);

    expect(merged).toHaveLength(50);
    expect(merged[0].timestamp).toBe("2026-11-12T17:59:00.000Z");
  });
});
