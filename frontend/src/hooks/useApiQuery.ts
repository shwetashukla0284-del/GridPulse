"use client";

import { useEffect, useState } from "react";

const REFRESH_MS = 60_000;

export type QueryState<T> = {
  status: "loading" | "ready" | "error";
  data?: T;
  error?: string;
};

/**
 * Fetch `path` for the last `minutes` and validate the JSON at runtime.
 * Refetches when inputs or `refreshKey` change, and every minute. Primitive
 * arguments keep the effect dependencies stable between renders.
 */
export function useApiQuery<T>(
  path: string,
  minutes: number,
  extraParams: string,
  isValid: (value: unknown) => value is T,
  refreshKey: number,
): QueryState<T> {
  const [state, setState] = useState<QueryState<T>>({ status: "loading" });

  useEffect(() => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    // Aborting cancels an in-flight request so a slow, outdated response
    // cannot overwrite data for newer inputs.
    let controller = new AbortController();

    const load = async () => {
      controller.abort();
      controller = new AbortController();
      const end = new Date();
      const start = new Date(end.getTime() - minutes * 60_000);
      const params = new URLSearchParams(extraParams);
      params.set("start", start.toISOString());
      params.set("end", end.toISOString());

      setState((current) => ({ ...current, status: "loading" }));
      try {
        const response = await fetch(`${apiUrl}${path}?${params}`, {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`API returned HTTP ${response.status}`);
        const payload: unknown = await response.json();
        if (!isValid(payload)) throw new Error("API returned invalid data");
        setState({ status: "ready", data: payload });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState((current) => ({
          ...current,
          status: "error",
          error: error instanceof Error ? error.message : "Unknown error",
        }));
      }
    };

    void load();
    const timer = setInterval(() => void load(), REFRESH_MS);
    return () => {
      clearInterval(timer);
      controller.abort();
    };
  }, [path, minutes, extraParams, isValid, refreshKey]);

  return state;
}
