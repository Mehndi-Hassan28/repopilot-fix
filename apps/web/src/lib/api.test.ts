import { describe, it, afterEach } from "node:test";
import assert from "node:assert/strict";
import { resolveApiUrl, getApiUrl } from "./api.ts";

describe("API Base URL Resolver", () => {
  const originalEnv = { ...process.env };

  afterEach(() => {
    process.env = { ...originalEnv };
  });

  it("returns empty string in production when NEXT_PUBLIC_API_BASE_URL is unset", () => {
    process.env.NODE_ENV = "production";
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    assert.equal(resolveApiUrl(), "");
    assert.equal(getApiUrl(), "");
  });

  it("returns empty string in production when NEXT_PUBLIC_API_BASE_URL points to localhost", () => {
    process.env.NODE_ENV = "production";
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000";
    assert.equal(resolveApiUrl(), "");
  });

  it("returns empty string in production when NEXT_PUBLIC_API_BASE_URL points to 127.0.0.1", () => {
    process.env.NODE_ENV = "production";
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000";
    assert.equal(resolveApiUrl(), "");
  });

  it("returns custom HTTPS URL in production when explicitly provided", () => {
    process.env.NODE_ENV = "production";
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://custom-api.example.com/";
    assert.equal(resolveApiUrl(), "https://custom-api.example.com");
  });

  it("falls back to localhost in test/development Node environment when unset", () => {
    process.env.NODE_ENV = "test";
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    assert.equal(resolveApiUrl(), "http://localhost:8000");
  });

  it("respects explicit NEXT_PUBLIC_API_BASE_URL in test/development", () => {
    process.env.NODE_ENV = "development";
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:9000/";
    assert.equal(resolveApiUrl(), "http://localhost:9000");
  });
});
