import type { IndexStatus } from "./types";

export type IndexDisplayState =
  | "ready"
  | "running"
  | "queued"
  | "failed"
  | "unindexed";

export interface IndexStatusBadgeInfo {
  state: IndexDisplayState;
  label: string;
  colorClass: string;
  dotClass: string;
  isAnimated: boolean;
}

export interface BadgeOptions {
  unindexedLabel?: string;
  completedLabel?: string;
}

/**
 * Checks if the indexing status represents a successfully completed / ready index.
 * Maps both backend "completed" and frontend/legacy "ready".
 */
export function isIndexCompleted(status?: string | null): boolean {
  if (!status) return false;
  const s = status.trim().toLowerCase();
  return s === "completed" || s === "ready";
}

/**
 * Synonym for isIndexCompleted.
 */
export function isIndexReady(status?: string | null): boolean {
  return isIndexCompleted(status);
}

/**
 * Checks if indexing is actively running.
 */
export function isIndexRunning(status?: string | null): boolean {
  if (!status) return false;
  return status.trim().toLowerCase() === "running";
}

/**
 * Checks if indexing is queued or pending execution.
 */
export function isIndexPending(status?: string | null): boolean {
  if (!status) return false;
  const s = status.trim().toLowerCase();
  return s === "queued" || s === "pending";
}

/**
 * Checks if indexing is in an active (in-progress) state: either queued/pending or running.
 */
export function isIndexActive(status?: string | null): boolean {
  return isIndexRunning(status) || isIndexPending(status);
}

/**
 * Checks if indexing failed.
 */
export function isIndexFailed(status?: string | null): boolean {
  if (!status) return false;
  return status.trim().toLowerCase() === "failed";
}

/**
 * Normalizes any raw status string into a canonical lifecycle state.
 */
export function normalizeIndexStatus(status?: string | null): IndexDisplayState {
  if (!status) return "unindexed";
  const s = status.trim().toLowerCase();
  if (s === "completed" || s === "ready") return "ready";
  if (s === "running") return "running";
  if (s === "queued" || s === "pending") return "queued";
  if (s === "failed") return "failed";
  return "unindexed";
}

/**
 * Returns badge styling, label, and animation properties for a given indexing status.
 */
export function getIndexStatusBadge(
  status?: string | null,
  isLocallyIndexing?: boolean,
  options?: BadgeOptions
): IndexStatusBadgeInfo {
  if (isLocallyIndexing || isIndexRunning(status)) {
    return {
      state: "running",
      label: "Indexing…",
      colorClass: "text-amber-400",
      dotClass: "bg-amber-400",
      isAnimated: true,
    };
  }

  if (isIndexPending(status)) {
    return {
      state: "queued",
      label: "Queued",
      colorClass: "text-purple-300",
      dotClass: "bg-purple-400",
      isAnimated: false,
    };
  }

  if (isIndexCompleted(status)) {
    return {
      state: "ready",
      label: options?.completedLabel || "Ready",
      colorClass: "text-[#BFFF00]",
      dotClass: "bg-[#BFFF00]",
      isAnimated: false,
    };
  }

  if (isIndexFailed(status)) {
    return {
      state: "failed",
      label: "Failed",
      colorClass: "text-rose-400",
      dotClass: "bg-rose-500",
      isAnimated: false,
    };
  }

  return {
    state: "unindexed",
    label: options?.unindexedLabel || "Not Indexed",
    colorClass: "text-zinc-400",
    dotClass: "bg-zinc-500",
    isAnimated: false,
  };
}

/**
 * Counts how many repositories have a ready/completed index.
 */
export function countReadyIndexes(
  indexes: Record<number | string, IndexStatus | null | undefined> | (IndexStatus | null | undefined)[]
): number {
  const items = Array.isArray(indexes) ? indexes : Object.values(indexes);
  return items.filter((item) => Boolean(item && isIndexCompleted(item.status))).length;
}
