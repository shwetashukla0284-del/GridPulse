/**
 * Where the browser finds the API.
 *
 * - Variable not set: local development defaults (API on localhost:8000).
 * - Variable set to "" (empty): same origin. A reverse proxy serves the
 *   dashboard and the API at one address, so the browser uses relative REST
 *   paths and a WebSocket URL built from the page's own address. This is how
 *   the single-container (Hugging Face Spaces) deployment works.
 * - Any other value: used as given.
 */

const DEFAULT_API_URL = "http://localhost:8000";
const DEFAULT_WS_URL = "ws://localhost:8000/ws/readings";
const WS_PATH = "/ws/readings";

/** The part of `window.location` needed to build a same-origin URL. */
export type PageLocation = Pick<Location, "protocol" | "host">;

/** Prefix for REST paths; "" means relative paths on the current site. */
export function resolveApiBaseUrl(configured: string | undefined): string {
  return configured ?? DEFAULT_API_URL;
}

export function resolveSocketUrl(
  configured: string | undefined,
  page: PageLocation,
): string {
  if (configured === undefined) return DEFAULT_WS_URL;
  if (configured !== "") return configured;
  // An HTTPS page may only open secure WebSockets (wss), so match the page.
  const scheme = page.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${page.host}${WS_PATH}`;
}
