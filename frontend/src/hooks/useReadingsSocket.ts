"use client";

import { useEffect, useState } from "react";

import { mergeAlerts } from "@/lib/alerts";
import { resolveSocketUrl } from "@/lib/endpoints";
import { AnomalyAlert, ChartPoint, isReadingBatch, Reading } from "@/types/readings";

const MAX_CHART_POINTS = 60;
export const MAX_ALERTS = 50;
const INITIAL_RETRY_MS = 500;
const MAX_RETRY_MS = 10_000;

export type ConnectionStatus = "connecting" | "connected" | "reconnecting" | "disconnected";

export type ReadingsState = {
  status: ConnectionStatus;
  latestByMeter: Record<string, Reading>;
  chart: ChartPoint[];
  invalidMessages: number;
  /** Alerts received live in this session, newest first (bounded). */
  liveAlerts: AnomalyAlert[];
  /** Increments on every successful (re)connect so other data can refresh. */
  connectionCount: number;
};

/** Sum all meters per timestamp into one system-level chart point, oldest first. */
export function aggregateBatch(readings: Reading[]): ChartPoint[] {
  const byTimestamp = new Map<string, ChartPoint>();

  for (const reading of readings) {
    const point = byTimestamp.get(reading.timestamp) ?? {
      timestamp: reading.timestamp,
      demand_kw: 0,
      solar_kw: 0,
      net_grid_kw: 0,
    };
    point.demand_kw += reading.demand_kw;
    point.solar_kw += reading.solar_kw;
    point.net_grid_kw += reading.net_grid_kw;
    byTimestamp.set(reading.timestamp, point);
  }

  return [...byTimestamp.values()].sort(
    (left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp),
  );
}

export function useReadingsSocket(): ReadingsState {
  const [state, setState] = useState<ReadingsState>({
    status: "connecting",
    latestByMeter: {},
    chart: [],
    invalidMessages: 0,
    liveAlerts: [],
    connectionCount: 0,
  });

  useEffect(() => {
    const socketUrl = resolveSocketUrl(
      process.env.NEXT_PUBLIC_WS_URL,
      window.location,
    );
    let socket: WebSocket | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    let attempts = 0;

    const connect = () => {
      if (disposed) return;
      setState((current) => ({
        ...current,
        status: attempts === 0 ? "connecting" : "reconnecting",
      }));

      socket = new WebSocket(socketUrl);

      socket.onopen = () => {
        attempts = 0;
        setState((current) => ({
          ...current,
          status: "connected",
          connectionCount: current.connectionCount + 1,
        }));
      };

      socket.onmessage = (event: MessageEvent<string>) => {
        let payload: unknown;
        try {
          payload = JSON.parse(event.data);
        } catch (error) {
          console.error("WebSocket sent invalid JSON", error);
          setState((current) => ({
            ...current,
            invalidMessages: current.invalidMessages + 1,
          }));
          return;
        }

        if (!isReadingBatch(payload)) {
          console.error("WebSocket sent an invalid reading batch", payload);
          setState((current) => ({
            ...current,
            invalidMessages: current.invalidMessages + 1,
          }));
          return;
        }

        setState((current) => {
          const latestByMeter = { ...current.latestByMeter };
          for (const reading of payload.readings) {
            latestByMeter[reading.meter_id] = reading;
          }

          const chart = [
            ...current.chart,
            ...aggregateBatch(payload.readings),
          ]
            .sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp))
            .slice(-MAX_CHART_POINTS);

          const liveAlerts = payload.alerts?.length
            ? mergeAlerts([payload.alerts, current.liveAlerts], MAX_ALERTS)
            : current.liveAlerts;

          return { ...current, latestByMeter, chart, liveAlerts };
        });
      };

      socket.onclose = () => {
        if (disposed) return;
        attempts += 1;
        setState((current) => ({ ...current, status: "reconnecting" }));
        const delay = Math.min(INITIAL_RETRY_MS * 2 ** (attempts - 1), MAX_RETRY_MS);
        reconnectTimer = setTimeout(connect, delay);
      };

      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
      setState((current) => ({ ...current, status: "disconnected" }));
    };
  }, []);

  return state;
}
