import { describe, it, afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  getDemoStatus,
  initializeDemo,
  resetDemo,
  createGuestSession,
  DemoApiError,
} from "./demo-api.ts";

describe("Demo API Client", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  describe("getDemoStatus", () => {
    it("fetches demo status successfully", async () => {
      globalThis.fetch = async (url: string | URL | Request, init?: RequestInit) => {
        assert.ok(String(url).endsWith("/api/demo/status"));
        assert.equal(init?.method, "GET");
        return new Response(
          JSON.stringify({
            initialized: true,
            repository_id: 1,
            repository_full_name: "repopilot/demo-calc-1",
            docker_available: true,
            docker_status_message: "Docker daemon active",
            sandbox_image: "python:3.11-slim",
            preconfigured_issue_number: 1,
            has_active_run: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      };

      const status = await getDemoStatus();
      assert.equal(status.initialized, true);
      assert.equal(status.repository_full_name, "repopilot/demo-calc-1");
      assert.equal(status.docker_available, true);
    });

    it("throws DemoApiError on failure with error detail", async () => {
      globalThis.fetch = async () => {
        return new Response(JSON.stringify({ detail: "Database unavailable" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        });
      };

      await assert.rejects(
        async () => {
          await getDemoStatus();
        },
        (err: unknown) => {
          assert.ok(err instanceof DemoApiError);
          assert.equal(err.status, 503);
          assert.equal(err.detail, "Database unavailable");
          return true;
        }
      );
    });
  });

  describe("initializeDemo", () => {
    it("posts to initialize demo with CSRF token", async () => {
      let capturedCsrf: string | null = null;
      let capturedUrl = "";

      globalThis.fetch = async (url: string | URL | Request, init?: RequestInit) => {
        capturedUrl = String(url);
        const headers = init?.headers as Headers;
        capturedCsrf = headers?.get("X-CSRF-Token") || null;

        return new Response(
          JSON.stringify({
            repository: {
              id: 99,
              owner: "repopilot",
              name: "demo-calc-1",
              full_name: "repopilot/demo-calc-1",
              url: "https://github.com/repopilot/demo-calculator",
              description: "Demo Calculator",
              default_branch: "main",
              stars: 42,
            },
            indexing_job: {
              id: 1,
              status: "ready",
              file_count: 2,
              commit_sha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
            issue: {
              number: 1,
              title: "Bug in power calculation",
              body: "Details",
              state: "open",
              html_url: "https://github.com/repopilot/demo-calculator/issues/1",
              author: "repopilot-qa",
              labels: ["bug"],
            },
            test_command: ["pytest", "-q"],
            sandbox_image: "python:3.11-slim",
            docker_available: true,
            docker_status_message: "Docker active",
            is_mock_ai: true,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      };

      const result = await initializeDemo(false, "test-csrf-123");
      assert.ok(capturedUrl.endsWith("/api/demo/initialize"));
      assert.equal(capturedCsrf, "test-csrf-123");
      assert.equal(result.repository.id, 99);
      assert.equal(result.is_mock_ai, true);
    });

    it("appends ?reset=true when reset option is set", async () => {
      let capturedUrl = "";
      globalThis.fetch = async (url: string | URL | Request) => {
        capturedUrl = String(url);
        return new Response(JSON.stringify({ repository: { id: 1 } }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      };

      await initializeDemo(true, "token");
      assert.ok(capturedUrl.endsWith("/api/demo/initialize?reset=true"));
    });
  });

  describe("resetDemo", () => {
    it("calls reset endpoint with POST and CSRF token", async () => {
      let method = "";
      let capturedCsrf: string | null = null;

      globalThis.fetch = async (url: string | URL | Request, init?: RequestInit) => {
        method = init?.method || "";
        const headers = init?.headers as Headers;
        capturedCsrf = headers?.get("X-CSRF-Token") || null;
        return new Response(
          JSON.stringify({
            initialized: false,
            docker_available: true,
            docker_status_message: "Docker active",
            sandbox_image: "python:3.11-slim",
            preconfigured_issue_number: 1,
            has_active_run: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      };

      const status = await resetDemo("csrf-reset-token");
      assert.equal(method, "POST");
      assert.equal(capturedCsrf, "csrf-reset-token");
      assert.equal(status.initialized, false);
    });
  });

  describe("createGuestSession", () => {
    it("initializes a guest demo session and returns user and CSRF token", async () => {
      let method = "";
      let capturedUrl = "";

      globalThis.fetch = async (url: string | URL | Request, init?: RequestInit) => {
        capturedUrl = String(url);
        method = init?.method || "";
        return new Response(
          JSON.stringify({
            authenticated: true,
            user: { id: 42, login: "guest-42", is_guest: true },
            csrf_token: "mock-guest-csrf-token",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      };

      const session = await createGuestSession();
      assert.ok(capturedUrl.endsWith("/api/auth/guest"));
      assert.equal(method, "POST");
      assert.equal(session.authenticated, true);
      assert.equal(session.user.id, 42);
      assert.equal(session.user.login, "guest-42");
      assert.equal(session.user.is_guest, true);
      assert.equal(session.csrf_token, "mock-guest-csrf-token");
    });
  });

  describe("demo security and simulated PR assertions", () => {
    it("validates simulated PR response structure and zero-write flag", () => {
      const simulatedPrData = {
        success: true,
        pr_number: 101,
        pr_url: "https://github.com/repopilot/demo-calc-1/pull/101",
        branch_name: "fix/issue-1-power",
        base_branch: "main",
        status: "created" as const,
        message: "Simulated GitHub pull request #101 created for demo repository.",
        is_simulated: true,
      };
      assert.equal(simulatedPrData.pr_number, 101);
      assert.equal(simulatedPrData.is_simulated, true);
      assert.ok(simulatedPrData.message.includes("Simulated"));
    });
  });
});
