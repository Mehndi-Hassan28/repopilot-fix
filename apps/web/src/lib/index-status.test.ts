import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  isIndexCompleted,
  isIndexReady,
  isIndexRunning,
  isIndexPending,
  isIndexActive,
  isIndexFailed,
  normalizeIndexStatus,
  getIndexStatusBadge,
  countReadyIndexes,
} from "./index-status.ts";
import type { IndexStatus } from "./types";

describe("index-status utility", () => {
  describe("isIndexCompleted / isIndexReady", () => {
    it("returns true for backend 'completed' status", () => {
      assert.equal(isIndexCompleted("completed"), true);
      assert.equal(isIndexCompleted("COMPLETED"), true);
      assert.equal(isIndexReady("completed"), true);
    });

    it("returns true for frontend/legacy 'ready' status", () => {
      assert.equal(isIndexCompleted("ready"), true);
      assert.equal(isIndexCompleted("READY"), true);
      assert.equal(isIndexReady("ready"), true);
    });

    it("returns false for non-completed statuses", () => {
      assert.equal(isIndexCompleted("queued"), false);
      assert.equal(isIndexCompleted("pending"), false);
      assert.equal(isIndexCompleted("running"), false);
      assert.equal(isIndexCompleted("failed"), false);
      assert.equal(isIndexCompleted(null), false);
      assert.equal(isIndexCompleted(undefined), false);
      assert.equal(isIndexCompleted(""), false);
    });
  });

  describe("isIndexRunning", () => {
    it("identifies active running state", () => {
      assert.equal(isIndexRunning("running"), true);
      assert.equal(isIndexRunning("RUNNING"), true);
      assert.equal(isIndexRunning("completed"), false);
      assert.equal(isIndexRunning("queued"), false);
      assert.equal(isIndexRunning(null), false);
    });
  });

  describe("isIndexPending", () => {
    it("identifies queued or pending states", () => {
      assert.equal(isIndexPending("queued"), true);
      assert.equal(isIndexPending("pending"), true);
      assert.equal(isIndexPending("running"), false);
      assert.equal(isIndexPending("completed"), false);
      assert.equal(isIndexPending(undefined), false);
    });
  });

  describe("isIndexActive", () => {
    it("returns true for both queued and running states", () => {
      assert.equal(isIndexActive("queued"), true);
      assert.equal(isIndexActive("pending"), true);
      assert.equal(isIndexActive("running"), true);
      assert.equal(isIndexActive("completed"), false);
      assert.equal(isIndexActive("ready"), false);
      assert.equal(isIndexActive("failed"), false);
      assert.equal(isIndexActive(null), false);
    });
  });

  describe("isIndexFailed", () => {
    it("identifies failed state", () => {
      assert.equal(isIndexFailed("failed"), true);
      assert.equal(isIndexFailed("FAILED"), true);
      assert.equal(isIndexFailed("completed"), false);
      assert.equal(isIndexFailed(null), false);
    });
  });

  describe("normalizeIndexStatus", () => {
    it("maps all statuses to canonical states", () => {
      assert.equal(normalizeIndexStatus("completed"), "ready");
      assert.equal(normalizeIndexStatus("ready"), "ready");
      assert.equal(normalizeIndexStatus("running"), "running");
      assert.equal(normalizeIndexStatus("queued"), "queued");
      assert.equal(normalizeIndexStatus("pending"), "queued");
      assert.equal(normalizeIndexStatus("failed"), "failed");
      assert.equal(normalizeIndexStatus(null), "unindexed");
      assert.equal(normalizeIndexStatus("unknown"), "unindexed");
    });
  });

  describe("getIndexStatusBadge", () => {
    it("formats completed/ready status with lime theme", () => {
      const badge = getIndexStatusBadge("completed");
      assert.equal(badge.state, "ready");
      assert.equal(badge.label, "Ready");
      assert.equal(badge.colorClass, "text-[#BFFF00]");
      assert.equal(badge.dotClass, "bg-[#BFFF00]");
      assert.equal(badge.isAnimated, false);
    });

    it("respects custom completedLabel", () => {
      const badge = getIndexStatusBadge("completed", false, { completedLabel: "Completed" });
      assert.equal(badge.label, "Completed");
    });

    it("formats running state when status is running or isLocallyIndexing is true", () => {
      const badge1 = getIndexStatusBadge("running");
      assert.equal(badge1.state, "running");
      assert.equal(badge1.label, "Indexing…");
      assert.equal(badge1.isAnimated, true);
      assert.equal(badge1.dotClass, "bg-amber-400");

      const badge2 = getIndexStatusBadge("completed", true);
      assert.equal(badge2.state, "running");
      assert.equal(badge2.label, "Indexing…");
      assert.equal(badge2.isAnimated, true);
    });

    it("formats queued/pending state", () => {
      const badge = getIndexStatusBadge("queued");
      assert.equal(badge.state, "queued");
      assert.equal(badge.label, "Queued");
      assert.equal(badge.dotClass, "bg-purple-400");
    });

    it("formats failed state", () => {
      const badge = getIndexStatusBadge("failed");
      assert.equal(badge.state, "failed");
      assert.equal(badge.label, "Failed");
      assert.equal(badge.dotClass, "bg-rose-500");
    });

    it("formats unindexed state with default and custom label", () => {
      const defaultBadge = getIndexStatusBadge(null);
      assert.equal(defaultBadge.state, "unindexed");
      assert.equal(defaultBadge.label, "Not Indexed");
      assert.equal(defaultBadge.dotClass, "bg-zinc-500");

      const customBadge = getIndexStatusBadge(null, false, { unindexedLabel: "Unindexed" });
      assert.equal(customBadge.label, "Unindexed");

      const idleBadge = getIndexStatusBadge(undefined, false, { unindexedLabel: "Idle" });
      assert.equal(idleBadge.label, "Idle");
    });
  });

  describe("countReadyIndexes", () => {
    it("counts completed and ready statuses correctly across dictionary", () => {
      const indexes: Record<number, IndexStatus> = {
        1: { status: "completed", file_count: 10, symbol_count: 50, languages: ["python"], commit_sha: "abc" },
        2: { status: "ready", file_count: 5, symbol_count: 20, languages: ["typescript"], commit_sha: "def" },
        3: { status: "running", file_count: 0, symbol_count: 0, languages: [], commit_sha: "ghi" },
        4: { status: "failed", file_count: 0, symbol_count: 0, languages: [], commit_sha: "jkl" },
      };

      assert.equal(countReadyIndexes(indexes), 2);
    });

    it("handles arrays and null/undefined values safely", () => {
      const list = [
        { status: "completed", file_count: 10, symbol_count: 50, languages: ["python"], commit_sha: "abc" },
        null,
        undefined,
        { status: "queued", file_count: 0, symbol_count: 0, languages: [], commit_sha: "ghi" },
      ];

      assert.equal(countReadyIndexes(list as (IndexStatus | null | undefined)[]), 1);
    });

    it("returns 0 for empty record", () => {
      assert.equal(countReadyIndexes({}), 0);
    });
  });
});
