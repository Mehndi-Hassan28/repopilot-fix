const apiUrl = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export function getApiUrl(): string {
  return apiUrl;
}

export async function apiRequest(path: string, init: RequestInit = {}, csrfToken = ""): Promise<Response> {
  const headers = new Headers(init.headers);
  const method = (init.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  return fetch(`${apiUrl}${path}`, { ...init, headers, credentials: "include" });
}
