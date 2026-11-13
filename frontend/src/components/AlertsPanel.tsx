"use client";

import { useStoredAlerts } from "@/hooks/useHistory";
import { MAX_ALERTS } from "@/hooks/useReadingsSocket";
import { alertKey, mergeAlerts } from "@/lib/alerts";
import { AnomalyAlert } from "@/types/readings";

function formatTime(value: string): string {
  return new Date(value).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

type AlertsPanelProps = {
  liveAlerts: AnomalyAlert[];
  refreshKey: number;
};

export function AlertsPanel({ liveAlerts, refreshKey }: AlertsPanelProps) {
  const stored = useStoredAlerts(MAX_ALERTS, refreshKey);
  const alerts = mergeAlerts([liveAlerts, stored.data ?? []], MAX_ALERTS);

  return (
    <section className="panel meters-panel alerts-panel" aria-live="polite">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">ROLLING Z-SCORE DETECTOR</p>
          <h2>
            Anomaly alerts <span className={`count-pill ${alerts.length ? "count-alert" : ""}`}>{alerts.length}</span>
          </h2>
        </div>
        <span className="table-caption">
          {stored.status === "error"
            ? `Alert history unavailable (${stored.error}); showing live alerts only`
            : "Live alerts plus stored alerts from the last 24 hours"}
        </span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Detected reading</th>
              <th>Meter</th>
              <th>Demand</th>
              <th>Normal (mean ± std)</th>
              <th>Z-score</th>
            </tr>
          </thead>
          <tbody>
            {alerts.map((alert) => (
              <tr key={alertKey(alert)}>
                <td className="muted-cell">{formatTime(alert.timestamp)}</td>
                <td className="meter-name">{alert.meter_id}</td>
                <td>{alert.demand_kw.toFixed(2)} kW</td>
                <td className="muted-cell">
                  {alert.baseline_mean_kw.toFixed(2)} ± {alert.baseline_std_kw.toFixed(2)} kW
                </td>
                <td>
                  <span className="status-pill fault-pill">
                    <i /> {alert.z_score > 0 ? "+" : ""}
                    {alert.z_score.toFixed(1)}σ {alert.z_score > 0 ? "above" : "below"}
                  </span>
                </td>
              </tr>
            ))}
            {!alerts.length && (
              <tr>
                <td className="empty-row" colSpan={5}>
                  No anomalies detected. Alerts appear when a meter&apos;s demand is far outside its recent range.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
