"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { AlertsPanel } from "@/components/AlertsPanel";
import { HistoryPanel } from "@/components/HistoryPanel";
import { useReadingsSocket } from "@/hooks/useReadingsSocket";

function formatPower(value: number): string {
  return `${value.toFixed(2)} kW`;
}

function formatTime(value: string): string {
  return new Date(value).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function LiveDashboard() {
  const { status, latestByMeter, chart, invalidMessages, liveAlerts, connectionCount } =
    useReadingsSocket();
  const meters = Object.values(latestByMeter).sort((a, b) =>
    a.meter_id.localeCompare(b.meter_id),
  );
  const latest = chart.at(-1);
  const totalDemand = meters.reduce((sum, reading) => sum + reading.demand_kw, 0);
  const totalSolar = meters.reduce((sum, reading) => sum + reading.solar_kw, 0);
  const activeFaults = meters.filter((reading) => reading.is_fault).length;

  return (
    <main className="dashboard">
      <header className="topbar">
        <a className="brand" href="#" aria-label="GridPulse home">
          <span className="brand-mark">G</span>
          <span>GRIDPULSE</span>
        </a>
        <div className={`connection connection-${status}`} role="status">
          <span className="connection-dot" />
          {status === "connected"
            ? "LIVE CONNECTION"
            : status === "reconnecting"
              ? "RECONNECTING"
              : status === "disconnected"
                ? "DISCONNECTED"
                : "CONNECTING"}
        </div>
      </header>

      <section className="page-heading">
        <div>
          <p className="eyebrow">ENERGY OPERATIONS / LIVE FEED</p>
          <h1>Grid overview</h1>
          <p className="subheading">
            Real-time demand, solar generation, and meter status.
          </p>
        </div>
        <div className="updated">
          <span className="updated-label">LATEST SAMPLE</span>
          <span>{latest ? formatTime(latest.timestamp) : "Waiting for readings"}</span>
        </div>
      </section>

      <section className="metrics" aria-label="Live key metrics">
        <article className="metric-card">
          <span className="metric-label">NET GRID LOAD</span>
          <strong>{latest ? formatPower(latest.net_grid_kw) : "—"}</strong>
          <span className="metric-note">Across latest meter batch</span>
        </article>
        <article className="metric-card">
          <span className="metric-label">TOTAL DEMAND</span>
          <strong>{meters.length ? formatPower(totalDemand) : "—"}</strong>
          <span className="metric-note">Latest reading per meter</span>
        </article>
        <article className="metric-card">
          <span className="metric-label">SOLAR GENERATION</span>
          <strong>{meters.length ? formatPower(totalSolar) : "—"}</strong>
          <span className="metric-note">Latest reading per meter</span>
        </article>
        <article className={`metric-card ${activeFaults ? "metric-alert" : ""}`}>
          <span className="metric-label">INJECTED FAULTS</span>
          <strong>{activeFaults}</strong>
          <span className="metric-note">Simulator ground truth, latest snapshots</span>
        </article>
      </section>

      <AlertsPanel liveAlerts={liveAlerts} refreshKey={connectionCount} />

      <section className="panel chart-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">SYSTEM POWER</p>
            <h2>Live energy flow</h2>
          </div>
          <div className="legend" aria-label="Chart legend">
            <span><i className="legend-demand" /> Demand</span>
            <span><i className="legend-solar" /> Solar</span>
            <span><i className="legend-net" /> Net grid</span>
          </div>
        </div>
        <div className="chart-wrap">
          {chart.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chart} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="demandFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#e9a23b" stopOpacity={0.2} />
                    <stop offset="100%" stopColor="#e9a23b" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="solarFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#39b98a" stopOpacity={0.18} />
                    <stop offset="100%" stopColor="#39b98a" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#e9edf0" vertical={false} />
                <XAxis
                  dataKey="timestamp"
                  tickFormatter={formatTime}
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
                  labelFormatter={(label) => formatTime(String(label))}
                  formatter={(value, name) => [formatPower(Number(value)), String(name)]}
                  contentStyle={{
                    borderRadius: 10,
                    border: "1px solid #e8ecef",
                    boxShadow: "0 8px 24px #172b3a12",
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="demand_kw"
                  name="Demand"
                  stroke="#e9a23b"
                  fill="url(#demandFill)"
                  strokeWidth={2}
                  isAnimationActive={false}
                />
                <Area
                  type="monotone"
                  dataKey="solar_kw"
                  name="Solar"
                  stroke="#39b98a"
                  fill="url(#solarFill)"
                  strokeWidth={2}
                  isAnimationActive={false}
                />
                <Area
                  type="monotone"
                  dataKey="net_grid_kw"
                  name="Net grid"
                  stroke="#4568d4"
                  fill="none"
                  strokeWidth={2.5}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="empty-chart">
              <span className="empty-pulse" />
              <strong>Waiting for live meter data</strong>
              <span>Start the simulator to populate the energy chart.</span>
            </div>
          )}
        </div>
        <div className="chart-footer">
          <span>Showing latest {chart.length} time points</span>
          <span>{invalidMessages ? `${invalidMessages} invalid message(s) rejected` : "Stream data validated"}</span>
        </div>
      </section>

      <HistoryPanel refreshKey={connectionCount} />

      <section className="panel meters-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">CONNECTED ASSETS</p>
            <h2>Meter readings <span className="count-pill">{meters.length}</span></h2>
          </div>
          <span className="table-caption">Latest reading per meter</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Meter</th>
                <th>Timestamp</th>
                <th>Demand</th>
                <th>Solar</th>
                <th>Net grid</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {meters.map((reading) => (
                <tr key={reading.meter_id}>
                  <td className="meter-name">{reading.meter_id}</td>
                  <td className="muted-cell">{formatTime(reading.timestamp)}</td>
                  <td>{formatPower(reading.demand_kw)}</td>
                  <td>{formatPower(reading.solar_kw)}</td>
                  <td>{formatPower(reading.net_grid_kw)}</td>
                  <td>
                    <span className={`status-pill ${reading.is_fault ? "fault-pill" : "normal-pill"}`}>
                      <i /> {reading.is_fault ? "Fault injected" : "Normal"}
                    </span>
                  </td>
                </tr>
              ))}
              {!meters.length && (
                <tr>
                  <td className="empty-row" colSpan={6}>
                    Meter readings will appear here when the simulator is publishing.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <footer className="footer">
        <span>GRIDPULSE / SMART GRID ANALYTICS</span>
        <span>Live stream powered by FastAPI WebSockets</span>
      </footer>
    </main>
  );
}
