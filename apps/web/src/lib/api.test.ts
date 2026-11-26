import { describe, it, afterEach } from "node:test";
import assert from "node:assert/strict";
import { resolveApiUrl, getApiUrl } from "./api.ts";

describe("API Base URL Resolver", () => {
  const originalNodeEnv = process.env.NODE_ENV;
  const originalApiUrl = process.env.NEXT_PUBLIC_API_BASE_URL;

  const setNodeEnv = (val: string) => {
    Object.defineProperty(process.env, "NODE_ENV", {
      value: val,
      configurable: true,
      writable: true,
      enumerable: true,
    });
  };

  afterEach(() => {
    if (originalNodeEnv !== undefined) {
      setNodeEnv(originalNodeEnv);
    } else {
      delete (process.env as Record<string, string | undefined>).NODE_ENV;
    }
    if (originalApiUrl !== undefined) {
      process.env.NEXT_PUBLIC_API_BASE_URL = originalApiUrl;
    } else {
      delete process.env.NEXT_PUBLIC_API_BASE_URL;
    }
  });

  it("returns empty string in production when NEXT_PUBLIC_API_BASE_URL is unset", () => {
    setNodeEnv("production");
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    assert.equal(resolveApiUrl(), "");
    assert.equal(getApiUrl(), "");
  });

  it("returns empty string in production when NEXT_PUBLIC_API_BASE_URL points to localhost", () => {
    setNodeEnv("production");
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000";
    assert.equal(resolveApiUrl(), "");
  });

  it("returns empty string in production when NEXT_PUBLIC_API_BASE_URL points to 127.0.0.1", () => {
    setNodeEnv("production");
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000";
    assert.equal(resolveApiUrl(), "");
  });

  it("returns custom HTTPS URL in production when explicitly provided", () => {
    setNodeEnv("production");
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://custom-api.example.com/";
    assert.equal(resolveApiUrl(), "https://custom-api.example.com");
  });

  it("falls back to localhost in test/development Node environment when unset", () => {
    setNodeEnv("test");
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    assert.equal(resolveApiUrl(), "http://localhost:8000");
  });

  it("respects explicit NEXT_PUBLIC_API_BASE_URL in test/development", () => {
    setNodeEnv("development");
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:9000/";
    assert.equal(resolveApiUrl(), "http://localhost:9000");
  });
});
