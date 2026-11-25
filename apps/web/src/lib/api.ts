/**
 * Resolves the API base URL.
 * - In production: defaults to same-origin relative URLs ("") to route through the Next.js rewrite proxy,
 *   preventing accidental localhost leakage in hosted environments (e.g., Vercel).
 * - In development / testing: allows explicit NEXT_PUBLIC_API_BASE_URL, or falls back to "" in browser
 *   and "http://localhost:8000" in headless / Node test runner environments.
 */
export function resolveApiUrl(): string {
  const configured = (process.env.NEXT_PUBLIC_API_BASE_URL || "").trim().replace(/\/$/, "");

  if (process.env.NODE_ENV === "production") {
    // In production, prevent accidental localhost leakage
    if (!configured || configured.includes("localhost") || configured.includes("127.0.0.1")) {
      return "";
    }
    return configured;
  }

  // Non-production (development / test)
  if (configured) {
    return configured;
  }

  // Browser in development: relative URL works through Next.js proxy
  if (typeof window !== "undefined") {
    return "";
  }

  // Node test runner fallback
  return "http://localhost:8000";
}

export function getApiUrl(): string {
  return resolveApiUrl();
}

export async function apiRequest(path: string, init: RequestInit = {}, csrfToken = ""): Promise<Response> {
  const apiUrl = resolveApiUrl();
  const headers = new Headers(init.headers);
  const method = (init.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  return fetch(`${apiUrl}${path}`, { ...init, headers, credentials: "include" });
}
