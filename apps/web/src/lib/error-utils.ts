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
  let message = fallback;
  if (!err) return fallback;
  if (typeof err === "string") {
    message = err;
  } else if (err instanceof Error) {
    if (err.message && err.message !== "[object Object]") {
      message = err.message;
    } else {
      const anyErr = err as { detail?: unknown };
      if (anyErr.detail) {
        message = formatApiDetail(anyErr.detail);
      }
    }
  } else if (typeof err === "object") {
    const obj = err as Record<string, unknown>;
    if (obj.detail) {
      message = formatApiDetail(obj.detail);
    } else if (typeof obj.message === "string" && obj.message !== "[object Object]") {
      message = obj.message;
    }
  }

  if (message.includes("(current state: 'queued')")) {
    return "Verification job is queued awaiting an available Docker worker. Start a private Docker worker or use Cloud Preview Mode.";
  }
  return message;
}
