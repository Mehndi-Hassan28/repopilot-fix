# Security Policy

RepoPilot Fix takes the security of autonomous software execution and AI agent workflows seriously. This document describes our security model, trust boundaries, vulnerability reporting procedure, and known limitations.

---

## 1. Reporting a Vulnerability

If you discover a security vulnerability in RepoPilot Fix, please **do not open a public GitHub issue**. Instead, report it privately to our security team:

- **Email:** `mehndihassan1010@gmail.com`
- **Response Target:** Initial acknowledgment within 48 hours; status update within 7 days.
- **Responsible Disclosure:** We ask that you give us reasonable time to investigate, reproduce, and patch the issue before any public disclosure.

---

## 2. Threat Model & Security Boundaries

RepoPilot Fix operates on untrusted codebases, issue descriptions, and LLM-generated diffs. The architecture enforces four strict isolation tiers:

### 2.1 Private Worker Boundary & Docker Isolation
- **No Public Socket Exposure:** The Docker daemon socket (`/var/run/docker.sock`) is strictly isolated and **never mounted** to the public API or Web containers.
- **Private Worker Boundary:** Sandbox execution is strictly handled by a background worker residing on an internal bridge network (`backend-net`).
- **Hardened Sandbox Parameters:**
  - Complete network air-gapping: `--network none`
  - Immutable root filesystem: `--read-only`
  - In-memory temporary directory: `--tmpfs /tmp:rw,noexec,nosuid,size=64m`
  - Unprivileged user execution: `--user 1000:1000`
  - Hardware resource caps: `--memory 512m`, `--cpus 1.0`, `--pids-limit 100`
  - Managed label tagging: `--label repopilot.managed=true` for scoped garbage collection

### 2.2 Cryptographic Human Approval Gating
- Automated PR creation and external repository writes are strictly prohibited without prior human sign-off.
- Every approved run is bound to an immutable SHA-256 specification hash:
  $$\text{SpecHash} = \text{SHA256}(\text{CommitSHA} \parallel \text{PatchHash} \parallel \text{TestCommand} \parallel \text{SandboxImage})$$
- Modifying the patch diff, commit SHA, or execution parameters invalidates the hash and aborts execution.

### 2.3 Single-Instance Protection & Prompt Injection
- **In-Memory Rate Limiting:** Single-instance token bucket rate limiting defends public endpoints against resource exhaustion.
- **Prompt Framing:** Untrusted issue descriptions and retrieved source files are segregated as raw evidence inside boundary delimiters. Prompt framing is defense-in-depth and does not guarantee complete injection prevention; therefore, the air-gapped Docker sandbox is the ultimate barrier against malicious execution.

---

## 3. Supported Versions

| Version | Supported | Security Notes |
|:---|:---:|:---|
| **1.0.x** | **Yes** | Active security support and vulnerability patching. |
| < 1.0.0 | No | Pre-release experimental builds; please upgrade to v1.0.0+. |
