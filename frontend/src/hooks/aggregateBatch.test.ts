import { describe, expect, it } from "vitest";

import { aggregateBatch } from "@/hooks/useReadingsSocket";
import type { Reading } from "@/types/readings";

function reading(meterId: string, timestamp: string, demand: number, solar: number): Reading {
  return {
    meter_id: meterId,
    timestamp,
    demand_kw: demand,
    solar_kw: solar,
    net_grid_kw: demand - solar,
    is_fault: false,
  };
}

describe("aggregateBatch", () => {
  it("sums meters that share a timestamp into one chart point", () => {
    const points = aggregateBatch([
      reading("m1", "2026-11-12T17:00:00Z", 2, 1),
      reading("m2", "2026-11-12T17:00:00Z", 3, 0.5),
    ]);

    expect(points).toHaveLength(1);
    expect(points[0].demand_kw).toBeCloseTo(5);
    expect(points[0].solar_kw).toBeCloseTo(1.5);
    expect(points[0].net_grid_kw).toBeCloseTo(3.5);
  });

  it("returns one point per timestamp, oldest first", () => {
    const points = aggregateBatch([
      reading("m1", "2026-11-12T17:01:00Z", 1, 0),
      reading("m1", "2026-11-12T17:00:00Z", 2, 0),
    ]);

    expect(points.map((point) => point.timestamp)).toEqual([
      "2026-11-12T17:00:00Z",
      "2026-11-12T17:01:00Z",
    ]);
  });

  it("returns no points for an empty batch", () => {
    expect(aggregateBatch([])).toEqual([]);
  });
});
