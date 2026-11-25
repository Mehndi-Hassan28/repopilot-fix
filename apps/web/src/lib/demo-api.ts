import type { DemoInitializeResponse, DemoStatusResponse } from "./types.ts";

function formatApiDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          const loc = Array.isArray(item.loc)
            ? item.loc.filter((x: unknown) => x !== "body").join(".")
            : "";
          const msg = item.msg || item.message || JSON.stringify(item);
          return loc ? `${loc}: ${msg}` : msg;
        }
        return String(item);
      })
      .filter(Boolean)
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    if (typeof record.msg === "string") return record.msg;
    if (typeof record.message === "string") return record.message;
    return JSON.stringify(detail);
  }
  return String(detail || "");
}

const apiUrl = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export class DemoApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "DemoApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  csrfToken = ""
): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method || "GET").toUpperCase();

  if (!headers.has("Content-Type") && init.body && typeof init.body === "string") {
    headers.set("Content-Type", "application/json");
  }

  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const response = await fetch(`${apiUrl}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  if (!response.ok) {
    let detail = "Demo request failed.";
    try {
      const data = await response.json();
      if (data && data.detail !== undefined) {
        detail = formatApiDetail(data.detail);
      } else if (data && typeof data.message === "string") {
        detail = data.message;
      }
    } catch {
      detail = await response.text().catch(() => detail);
    }
    throw new DemoApiError(response.status, detail);
  }

  return response.json() as Promise<T>;
}

export async function getDemoStatus(): Promise<DemoStatusResponse> {
  return request<DemoStatusResponse>("/api/demo/status", {
    method: "GET",
    cache: "no-store",
  });
}

export async function initializeDemo(
  reset = false,
  csrfToken = ""
): Promise<DemoInitializeResponse> {
  const query = reset ? "?reset=true" : "";
  return request<DemoInitializeResponse>(
    `/api/demo/initialize${query}`,
    {
      method: "POST",
    },
    csrfToken
  );
}

export async function resetDemo(csrfToken = ""): Promise<DemoStatusResponse> {
  return request<DemoStatusResponse>(
    "/api/demo/reset",
    {
      method: "POST",
    },
    csrfToken
  );
}

export async function createGuestSession(): Promise<{
  authenticated: boolean;
  user: { id: number; login: string; is_guest?: boolean };
  csrf_token: string;
}> {
  return request<{
    authenticated: boolean;
    user: { id: number; login: string; is_guest?: boolean };
    csrf_token: string;
  }>("/api/auth/guest", {
    method: "POST",
  });
}
