"use client";

import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { HISTORY_RANGES, HistoryRange, useHistory, useKpis } from "@/hooks/useHistory";
import { KpiSummary } from "@/types/readings";

function formatPower(value: number): string {
  return `${value.toFixed(2)} kW`;
}

function formatBucket(value: string, range: HistoryRange): string {
  const date = new Date(value);
  return range === "24h"
    ? date.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })
    : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatEnergy(value: number | undefined): string {
  return value === undefined ? "—" : `${value.toFixed(2)} kWh`;
}

function KpiRow({ kpis, range }: { kpis?: KpiSummary; range: HistoryRange }) {
  const coverage = kpis?.solar_coverage_pct;
  return (
    <div className="kpi-row" aria-label={`Key indicators for the last ${HISTORY_RANGES[range].label}`}>
      <div className="kpi">
        <span className="metric-label">GRID IMPORT</span>
        <strong>{formatEnergy(kpis?.import_kwh)}</strong>
      </div>
      <div className="kpi">
        <span className="metric-label">GRID EXPORT</span>
        <strong>{formatEnergy(kpis?.export_kwh)}</strong>
      </div>
      <div className="kpi">
        <span className="metric-label">SOLAR COVERAGE</span>
        <strong>{coverage === null || coverage === undefined ? "—" : `${coverage.toFixed(1)}%`}</strong>
        <span className="metric-note">Share of demand met by on-site solar</span>
      </div>
      <div className="kpi">
        <span className="metric-label">PEAK NET LOAD</span>
        <strong>
          {kpis?.peak_net_grid_kw === null || kpis?.peak_net_grid_kw === undefined
            ? "—"
            : formatPower(kpis.peak_net_grid_kw)}
        </strong>
        <span className="metric-note">
          {kpis?.peak_at ? `at ${formatBucket(kpis.peak_at, range)}` : "No readings"}
        </span>
      </div>
    </div>
  );
}

export function HistoryPanel({ refreshKey }: { refreshKey: number }) {
  const [range, setRange] = useState<HistoryRange>("1h");
  const { status, buckets, error } = useHistory(range, refreshKey);
  const kpis = useKpis(range, refreshKey);
  const totalReadings = buckets.reduce((sum, bucket) => sum + bucket.reading_count, 0);
  const totalFaults = buckets.reduce((sum, bucket) => sum + bucket.fault_count, 0);
  const bucketMinutes = HISTORY_RANGES[range].bucketSeconds / 60;

  return (
    <section className="panel chart-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">STORED HISTORY / TIMESCALEDB</p>
          <h2>Historical trends</h2>
        </div>
        <div className="range-picker" role="group" aria-label="History range">
          {(Object.keys(HISTORY_RANGES) as HistoryRange[]).map((key) => (
            <button
              key={key}
              type="button"
              className={key === range ? "range-active" : ""}
              aria-pressed={key === range}
              onClick={() => setRange(key)}
            >
              {key}
            </button>
          ))}
        </div>
      </div>
      <KpiRow kpis={kpis.data} range={range} />
      <div className="chart-wrap">
        {buckets.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={buckets} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#e9edf0" vertical={false} />
              <XAxis
                dataKey="bucket_start"
                tickFormatter={(value: string) => formatBucket(value, range)}
                tick={{ fill: "#82909a", fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                minTickGap={32}
              />
              <YAxis
                tick={{ fill: "#82909a", fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                width={56}
                tickFormatter={(value: number) => `${value} kW`}
              />
              <Tooltip
                labelFormatter={(label) => formatBucket(String(label), range)}
                formatter={(value, name) => [formatPower(Number(value)), String(name)]}
                contentStyle={{ borderRadius: 10, border: "1px solid #e8ecef" }}
              />
              <Line type="monotone" dataKey="avg_demand_kw" name="Avg demand" stroke="#e9a23b" strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="avg_solar_kw" name="Avg solar" stroke="#39b98a" strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="avg_net_grid_kw" name="Avg net grid" stroke="#4568d4" strokeWidth={2.5} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="empty-chart">
            <span className="empty-pulse" />
            <strong>
              {status === "error"
                ? "History unavailable"
                : status === "loading"
                  ? "Loading history"
                  : "No stored readings in this range"}
            </strong>
            <span>
              {status === "error"
                ? error
                : `Run the simulator to store readings for the last ${HISTORY_RANGES[range].label}.`}
            </span>
          </div>
        )}
      </div>
      <div className="chart-footer">
        <span>
          {buckets.length} buckets of {bucketMinutes} min · {totalReadings} readings · {totalFaults} injected faults
        </span>
        <span>
          {status === "error" || kpis.status === "error"
            ? `Last refresh failed: ${error ?? kpis.error}`
            : "Refreshes every minute and on reconnect"}
        </span>
      </div>
    </section>
  );
}
