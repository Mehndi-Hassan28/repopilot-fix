import type {
  VerificationApprovalRequest,
  VerificationAuditEventRead,
  VerificationCancelRequest,
  VerificationCreateRequest,
  VerificationRejectionRequest,
  VerificationRunRead,
  ProofReportRead,
  PullRequestCreateRequest,
  PullRequestCreateResponse,
  PatchExportResponse,
  PullRequestStatusResponse,
} from "./verification-types.ts";

export function formatApiDetail(detail: unknown): string {
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

export function formatErrorMessage(err: unknown, fallback: string): string {
  if (!err) return fallback;
  if (typeof err === "string") return err;
  if (err instanceof Error) {
    if (err.message && err.message !== "[object Object]") {
      return err.message;
    }
    const anyErr = err as { detail?: unknown };
    if (anyErr.detail) {
      return formatApiDetail(anyErr.detail);
    }
  }
  if (typeof err === "object") {
    const obj = err as Record<string, unknown>;
    if (obj.detail) return formatApiDetail(obj.detail);
    if (typeof obj.message === "string" && obj.message !== "[object Object]") return obj.message;
  }
  return fallback;
}

const apiUrl = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export class VerificationApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "VerificationApiError";
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
    let detail = "Verification request failed.";
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
    throw new VerificationApiError(response.status, detail);
  }

  if (response.status === 204) {
    return {} as T;
  }

  return response.json() as Promise<T>;
}

export async function createVerification(
  repositoryId: number,
  payload: VerificationCreateRequest,
  csrfToken: string
): Promise<VerificationRunRead> {
  return request<VerificationRunRead>(
    `/api/repositories/${repositoryId}/verifications`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    csrfToken
  );
}

export async function listVerifications(
  repositoryId: number,
  statusFilter?: string
): Promise<VerificationRunRead[]> {
  const query = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
  return request<VerificationRunRead[]>(
    `/api/repositories/${repositoryId}/verifications${query}`,
    { method: "GET", cache: "no-store" }
  );
}

export async function getVerification(
  repositoryId: number,
  verificationId: number
): Promise<VerificationRunRead> {
  return request<VerificationRunRead>(
    `/api/repositories/${repositoryId}/verifications/${verificationId}`,
    { method: "GET", cache: "no-store" }
  );
}

export async function submitForApproval(
  repositoryId: number,
  verificationId: number,
  csrfToken: string
): Promise<VerificationRunRead> {
  return request<VerificationRunRead>(
    `/api/repositories/${repositoryId}/verifications/${verificationId}/submit`,
    { method: "POST" },
    csrfToken
  );
}

export async function approveVerification(
  repositoryId: number,
  verificationId: number,
  payload: VerificationApprovalRequest,
  csrfToken: string
): Promise<VerificationRunRead> {
  return request<VerificationRunRead>(
    `/api/repositories/${repositoryId}/verifications/${verificationId}/approve`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    csrfToken
  );
}

export async function rejectVerification(
  repositoryId: number,
  verificationId: number,
  payload: VerificationRejectionRequest,
  csrfToken: string
): Promise<VerificationRunRead> {
  return request<VerificationRunRead>(
    `/api/repositories/${repositoryId}/verifications/${verificationId}/reject`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    csrfToken
  );
}

export async function cancelVerification(
  repositoryId: number,
  verificationId: number,
  payload: VerificationCancelRequest,
  csrfToken: string
): Promise<VerificationRunRead> {
  return request<VerificationRunRead>(
    `/api/repositories/${repositoryId}/verifications/${verificationId}/cancel`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    csrfToken
  );
}

export async function executeVerification(
  repositoryId: number,
  verificationId: number,
  csrfToken: string
): Promise<unknown> {
  return request<unknown>(
    `/api/repositories/${repositoryId}/verifications/${verificationId}/execute`,
    { method: "POST" },
    csrfToken
  );
}

export async function getVerificationReport(
  repositoryId: number,
  verificationId: number
): Promise<ProofReportRead> {
  return request<ProofReportRead>(
    `/api/repositories/${repositoryId}/verifications/${verificationId}/report`,
    { method: "GET", cache: "no-store" }
  );
}

export async function getVerificationAuditEvents(
  repositoryId: number,
  verificationId: number
): Promise<VerificationAuditEventRead[]> {
  return request<VerificationAuditEventRead[]>(
    `/api/repositories/${repositoryId}/verifications/${verificationId}/events`,
    { method: "GET", cache: "no-store" }
  );
}

export async function createPullRequest(
  repositoryId: number,
  verificationId: number,
  payload: PullRequestCreateRequest,
  csrfToken: string
): Promise<PullRequestCreateResponse> {
  const headers: Record<string, string> = {};
  if (payload.github_token) {
    headers["X-GitHub-Token"] = payload.github_token;
  }
  return request<PullRequestCreateResponse>(
    `/api/repositories/${repositoryId}/verifications/${verificationId}/create-pr`,
    {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    },
    csrfToken
  );
}

export async function exportPatch(
  repositoryId: number,
  verificationId: number
): Promise<PatchExportResponse> {
  return request<PatchExportResponse>(
    `/api/repositories/${repositoryId}/verifications/${verificationId}/export-patch`,
    { method: "GET", cache: "no-store" }
  );
}

export async function getPullRequestStatus(
  repositoryId: number,
  verificationId: number
): Promise<PullRequestStatusResponse> {
  return request<PullRequestStatusResponse>(
    `/api/repositories/${repositoryId}/verifications/${verificationId}/pr-status`,
    { method: "GET", cache: "no-store" }
  );
}
