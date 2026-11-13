import { describe, expect, it } from "vitest";

import { resolveApiBaseUrl, resolveSocketUrl } from "@/lib/endpoints";

const httpsPage = { protocol: "https:", host: "shweta0284-gridpulse.hf.space" };
const httpPage = { protocol: "http:", host: "localhost:7860" };

describe("resolveApiBaseUrl", () => {
  it("defaults to the local API when not configured", () => {
    expect(resolveApiBaseUrl(undefined)).toBe("http://localhost:8000");
  });

  it("uses relative paths when configured as empty (same origin)", () => {
    expect(resolveApiBaseUrl("")).toBe("");
  });

  it("uses an explicit URL as given", () => {
    expect(resolveApiBaseUrl("http://localhost:18000")).toBe("http://localhost:18000");
  });
});

describe("resolveSocketUrl", () => {
  it("defaults to the local API when not configured", () => {
    expect(resolveSocketUrl(undefined, httpsPage)).toBe("ws://localhost:8000/ws/readings");
  });

  it("uses wss on an HTTPS page when same origin", () => {
    expect(resolveSocketUrl("", httpsPage)).toBe(
      "wss://shweta0284-gridpulse.hf.space/ws/readings",
    );
  });

  it("uses ws and keeps the port on an HTTP page when same origin", () => {
    expect(resolveSocketUrl("", httpPage)).toBe("ws://localhost:7860/ws/readings");
  });

  it("uses an explicit URL as given", () => {
    expect(resolveSocketUrl("ws://example.test/ws/readings", httpsPage)).toBe(
      "ws://example.test/ws/readings",
    );
  });
});
