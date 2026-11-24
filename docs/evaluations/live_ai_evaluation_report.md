# Live AI Autonomous Bug Repair — Evaluation Report

- **Date:** 2026-11-24
- **Branch:** `feat/live-ai-proof-of-fix`
- **Component:** RepoPilot Fix Autonomous Software Repair Engine
- **Report Status:** Validated & Sanitized

---

## 1. Executive Summary & Model Identity

RepoPilot Fix was evaluated as an evidence-grounded, autonomous software repair agent. The agent investigates issues using repository indexing and AST symbol retrieval, synthesizes structured root-cause hypotheses, and generates unified diff patches. All patches are gated by mandatory human approval and verified inside isolated dual Docker sandbox containers.

### Model & Provider Configuration
- **Live AI Provider:** Google AI Studio (OpenAI-compatible API `https://generativelanguage.googleapis.com/v1beta/openai`)
- **Live Model ID:** `models/gemini-flash-lite-latest`
- **Quota & Cost:** Confirmed Free Tier (Zero billing enabled, $0.00 spend)
- **Rate Limit Policy:** 15 requests per minute, handled with 4 retries, exponential backoff (base 0.01s, max 10.0s), and jitter
- **Offline Fallback Engine:** Deterministic `MockProvider` (heuristic pattern matching, 1ms latency, 0 tokens)
- **Telemetry Recorded:** `model`, `latency_ms`, `token_usage` (prompt/completion/total), `generation_mode` (`live_ai` vs `mock_ai`)

---

## 2. Evaluation Methodology

1. **Independent Patch Synthesis:**
   - The model is supplied **only** with the untrusted issue title, body, and retrieved source code blocks with explicit 1-indexed line numbers.
   - **No predefined solution diff or golden patch is provided to the model.**
   - All three test patches were synthesized independently by `models/gemini-flash-lite-latest`.

2. **Server-Side Validation & Strict Citation Enforcement:**
   - Relevant file citations must strictly belong to retrieved evidence.
   - Citation line numbers are strictly checked against actual file length (`1 <= start_line <= total_lines` and `start_line <= end_line <= total_lines`).
   - Out-of-bounds citations are **strictly rejected** as invalid (`ValueError`) rather than silently clamped.
   - Diffs are pre-flighted using in-memory dry-run application (`apply_unified_diff`).

3. **Dual-Sandbox Docker Verification:**
   - Baseline container: Runs allowlisted test suite against unpatched repository to confirm failure reproduction.
   - Patched container: Applies approved unified diff in an identical isolated container and reruns tests.
   - Engine computes passed, failed, and newly introduced failures (`new_failed_test_ids`).
   - Verdicts: `VERIFIED_FIX`, `FIX_FAILED`, `REGRESSION_DETECTED`, `PATCH_APPLICATION_ERROR`.

4. **Cryptographic Integrity & Human Approval Gating:**
   - Specification hash: `SHA256(commit_sha, patch_hash, test_command, sandbox_image)`.
   - PR Creation: Strictly requires `verdict == VERIFIED_FIX` and exact match with `approved_spec_hash`. Unsuccessful fixes or regressions are rejected with HTTP 400.

---

## 3. Trusted Evaluation Scenarios & Live Results

### Scenario 1: Calculator Power Operator Bug
- **Repository Context:** `demo-calculator`
- **Target File:** `src/calculator.py` (21 lines)
- **Issue #1:** `power(2, 3)` returns 6 instead of 8. Multiplication operator used instead of exponentiation.
- **Live AI Generation Telemetry:**
  - **Generation Mode:** `live_ai`
  - **Model:** `models/gemini-flash-lite-latest`
  - **Latency:** 2,270 ms
  - **Token Usage:** 505 prompt, 360 completion, 865 total tokens
- **Source Citation:** `src/calculator.py:17-21` (Exact match, 100% within 21 lines, 0 clamping)
- **Model Hypothesis:** `"Hypothesis: The power function was implemented using the multiplication operator (*) rather than the exponentiation operator (**)."`
- **Independently Synthesized Diff:**
  ```diff
  --- src/calculator.py
  +++ src/calculator.py
  @@ -18,4 +18,4 @@
   def power(base: int, exponent: int) -> int:
       """Return base raised to exponent."""
       # Bug: returns multiplication instead of exponentiation
  -    return base * exponent
  +    return base ** exponent
  ```
- **Dual Container Verification:**
  - Baseline execution: 2 failed (`test_power_positive`, `test_power_zero`), 4 passed. Exit code: 1.
  - Patched execution: 0 failed, 6 passed. Exit code: 0.
  - New failures: 0.
  - Verdict: `VERIFIED_FIX` (Confidence: `high`).

---

### Scenario 2: Calculator Package Addition Defect
- **Target File:** `src/calc.py` (4 lines)
- **Issue #2:** `add(2, 3)` returns -1 instead of 5. Subtraction operator used instead of addition.
- **Live AI Generation Telemetry:**
  - **Generation Mode:** `live_ai`
  - **Model:** `models/gemini-flash-lite-latest`
  - **Latency:** 3,750 ms
  - **Token Usage:** 385 prompt, 383 completion, 768 total tokens
- **Source Citation:** `src/calc.py:1-4` (Exact match, 100% within 4 lines, 0 clamping)
- **Model Hypothesis:** `"Hypothesis: The implementation of add in src/calc.py uses the subtraction operator (-) instead of the addition operator (+)."`
- **Independently Synthesized Diff:**
  ```diff
  --- src/calc.py
  +++ src/calc.py
  @@ -1,4 +1,4 @@
   def add(a: int, b: int) -> int:
       """Add two numbers."""
  -    # Bug: subtraction instead of addition
  -    return a - b
  +    # Fixed: use addition
  +    return a + b
  ```
- **Dual Container Verification:**
  - Baseline execution: 1 failed (`test_add`), 0 passed. Exit code: 1.
  - Patched execution: 0 failed, 1 passed. Exit code: 0.
  - New failures: 0.
  - Verdict: `VERIFIED_FIX` (Confidence: `high`).

---

### Scenario 3: Range Validator Inclusive Upper Bound Bug
- **Target File:** `src/validator.py` (9 lines)
- **Issue #3:** `is_in_range(10, 1, 10)` returns False instead of True. Upper boundary comparison is exclusive (`<`) instead of inclusive (`<=`).
- **Live AI Generation Telemetry:**
  - **Generation Mode:** `live_ai`
  - **Model:** `models/gemini-flash-lite-latest`
  - **Latency:** 4,440 ms
  - **Token Usage:** 442 prompt, 429 completion, 871 total tokens
- **Source Citation:** `src/validator.py:1-5` (Exact match, 100% within 9 lines, 0 clamping)
- **Model Hypothesis:** `"Hypothesis: The function is_in_range currently uses a strict inequality (< max_val) for the upper bound check instead of an inclusive inequality (<= max_val)."`
- **Independently Synthesized Diff:**
  ```diff
  --- src/validator.py
  +++ src/validator.py
  @@ -1,5 +1,5 @@
   def is_in_range(val: int, min_val: int, max_val: int) -> bool:
       """Check if val is within [min_val, max_val] inclusive."""
       # Bug: uses exclusive check instead of inclusive on upper bound
  -    return min_val <= val < max_val
  +    return min_val <= val <= max_val
  ```
- **Real Docker Sandbox Dual-Container Execution:**
  - Runner: `DockerSandboxRunner` (Hardened non-root runtime, tmpfs scratch root).
  - Baseline Container: 1 failed (`test_in_range_upper_bound`), 4 passed. Exit code: 1.
  - Patched Container: 0 failed, 5 passed. Exit code: 0.
  - Verdict: `VERIFIED_FIX`.
- **Regression Detection Verification:**
  - When an intentional regression patch was applied (`return min_val < val <= max_val` breaking lower bound):
  - Patched Container failed `test_in_range_lower_bound`.
  - Verdict: `REGRESSION_DETECTED`.
  - PR Creation Gate: Blocked with `GitHubVerificationPrerequisiteError`.

---

## 4. Live AI vs. Offline Mock Fallback Comparison

| Metric / Dimension | Live AI (`models/gemini-flash-lite-latest`) | Offline Fallback (`MockProvider`) |
| :--- | :--- | :--- |
| **Execution Mode** | `live_ai` | `mock_ai` |
| **Model String** | `models/gemini-flash-lite-latest` | `mock` |
| **Patch Synthesis** | Fully dynamic via LLM reasoning | Deterministic heuristic pattern matching |
| **Average Latency** | 3.48 seconds | 1 millisecond |
| **Token Consumption** | ~444 prompt / 390 completion tokens | 0 tokens (offline) |
| **External Network** | Yes (HTTPS outbound to Google AI Studio) | None (100% local air-gapped) |
| **UI Transparency** | Marked with green `Live AI (gemini-flash-lite)` badge | Marked with yellow `Mock AI` badge |
| **Evaluation Inclusion** | Evaluated for reasoning and patch synthesis | Evaluated only as offline fallback contract |

---

## 5. Security, Sandbox & Gating Verification

1. **Sandbox Restrictions Preserved:**
   - Root filesystem mounted read-only (`--read-only`).
   - Non-root user execution (`nobody:nogroup` / UID 65534).
   - Scratch directory mounted as isolated `tmpfs`.
   - Network connectivity disabled (`--network none`).
   - Linux capabilities dropped (`--cap-drop ALL`, `--security-opt no-new-privileges`).
2. **Approval Enforcement:**
   - Patches cannot be verified without explicit human approval.
   - Any tampering with `commit_sha`, `patch_diff`, `test_command`, or `sandbox_image` invalidates `approved_spec_hash` and aborts verification before sandbox execution.
3. **PR Creation Gate:**
   - Unverified runs, failed fixes, or regressions are strictly prohibited from PR creation.
   - Zero external GitHub write operations occur during automated testing or demo workflows.
4. **Credential Sanitization:**
   - No API keys, OAuth tokens, or secrets are logged, persisted in audit events, or included in client payloads.
