# RepoPilot Fix — 5-Minute Demonstration Script

**Target Time:** 5 Minutes (0:00 - 5:00)  
**Presenter:** Engineering Lead / Hackathon Presenter  
**Prerequisites:** Services running locally (`http://localhost:3000` web, `http://localhost:8000` API) or via Docker Compose.

---

## Timeline & Step-by-Step Actions

### Minute 0:00 - 0:45 | Introduction & Problem Hook
- **Action:** Open `http://localhost:3000/`. The sleek dark theme with neon lime (`#BFFF00`) accents is displayed.
- **Presenter Narrative:**
  > *"Welcome to RepoPilot Fix. Every developer has experienced AI assistants that hallucinate fake functions or write patches that break the rest of the codebase. Today, I'm going to show you an evidence-driven software repair agent that doesn't just guess code—it proves the fix inside isolated Docker sandboxes before a PR is opened."*

---

### Minute 0:45 - 1:45 | Repository Ingestion & AST Dependency Intelligence
- **Action:** Click **"Load Demo Repository"** on the dashboard.
- **Behind the Scenes:** The backend calls `POST /api/demo/initialize?reset=true`, seeding the deterministic calculator repository (`repopilot/demo-calc-1`) and indexing symbols.
- **Action:** Click on the **"Dependency Graph"** tab in the workspace.
- **Visual Presentation:** An interactive pure-SVG directed graph displays file nodes (`src/calculator.py`, `tests/test_calculator.py`), function symbols (`power`, `add`, `multiply`), and directional import edges.
- **Presenter Narrative:**
  > *"Instead of flattening code into random vector embeddings, RepoPilot Fix uses Tree-sitter and Python's standard AST module to build an exact call graph. Notice how our pure-SVG engine renders cross-file relationships with zero heavy charting libraries. When an issue arrives, we know exactly who calls what."*

---

### Minute 1:45 - 2:45 | Issue Diagnosis & Blast Radius Calculation
- **Action:** Switch to the **"Issue Investigation"** tab.
- **Action:** Select Issue #1: *"power(2, 3) returns 6 instead of 8: multiplication operator used instead of exponentiation"*.
- **Action:** Click **"Analyze Issue"**.
- **Visual Presentation:**
  - The AST Blast Radius component identifies `src/calculator.py` as the 100% root-cause target.
  - The caller-callee tracer recommends executing `tests/test_calculator.py`.
  - The candidate diff is generated in the unified diff viewer, highlighting:
    ```diff
    -    return a * b
    +    return a ** b
    ```
- **Presenter Narrative:**
  > *"The agent analyzes the AST call site, extracts the blast radius, and synthesizes a minimal, atomic unified diff. Notice that this was synthesized completely independently from the issue description without leaking golden solution patches."*

---

### Minute 2:45 - 3:45 | Cryptographic Approval & Dual Docker Verification
- **Action:** Point out the **"Pending Approval"** badge and the computed **Spec Hash** (`SHA256(...)`).
- **Action:** Click **"Approve Verification Run"**. The state changes to **"QUEUED"**.
- **Action:** Click **"Run Sandbox Verification"** (dispatched to the private worker).
- **Visual Presentation:** The UI displays real-time execution steps:
  1. *Spawning Baseline Sandbox:* Air-gapped container (`--network none`, `--read-only`, non-root user).
  2. *Baseline Result:* Fails on `power(2, 3) != 8` (Reproduction Confirmed!).
  3. *Spawning Patched Sandbox:* Applies unified diff inside a fresh twin sandbox.
  4. *Patched Result:* All 4 tests pass with 0 new regressions (`new_failed_test_ids: []`).
  5. *Status Update:* Badge flips to glowing neon lime **`VERIFIED_FIX`**.
- **Presenter Narrative:**
  > *"This is the Proof-of-Fix evidence engine in action. We don't trust an LLM's word. We spin up two air-gapped Docker sandboxes: the baseline container proves the bug actually exists, and the patched container proves the patch fixes it without breaking any existing tests. Notice the status: VERIFIED_FIX."*

---

### Minute 3:45 - 4:30 | Proof-of-Fix Evidence Report & Patch Export
- **Action:** Click **"View Proof-of-Fix Evidence Report"**.
- **Visual Presentation:** An audit modal shows:
  - Baseline exit code: `1` (Reproduction confirmed)
  - Patched exit code: `0` (Clean pass)
  - Execution duration: `1.2s`
  - Delta regressions: `0`
- **Action:** Click **"Export Patch"**. A standard `.patch` file is downloaded with command-line `git apply` instructions.
- **Presenter Narrative:**
  > *"For teams with strict CI/CD or air-gapped environments, the engineer can download the cryptographically sealed patch file with full verification telemetry attached."*

---

### Minute 4:30 - 5:00 | Summary & Teardown
- **Action:** Click **"Reset Demo"**. The backend atomically cleans up all fixture runs and sessions (`POST /api/demo/reset`).
- **Presenter Narrative:**
  > *"RepoPilot Fix delivers true closed-loop software repair: AST intelligence, dual Docker sandbox validation, and cryptographic human sign-off. It's production-ready, fully open-source under the MIT license, and runs 100% on free-tier infrastructure. Thank you!"*
