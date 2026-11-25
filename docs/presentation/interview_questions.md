# RepoPilot Fix — Technical Interview Questions & Answers

This document prepares candidates and engineers to discuss the architecture, engineering trade-offs, security model, and performance of RepoPilot Fix during technical screens and system design interviews.

---

## Category 1: System Design & Concurrency

### Q1: How does RepoPilot Fix prevent race conditions and duplicate executions across multiple worker instances?
**Answer:**  
RepoPilot Fix implements a **distributed worker lease pattern** using PostgreSQL row-level locks and atomic fencing tokens:
1. **Atomic Lease Acquisition:** The background worker polls `verification_runs` using `SELECT ... WHERE status = 'queued' FOR UPDATE SKIP LOCKED LIMIT 1`. This ensures multiple worker instances never claim the same job simultaneously without requiring an external Redis lock manager.
2. **Heartbeats & Leases:** When a worker claims a run, it sets `worker_id`, `lease_expires_at = now() + interval '30 seconds'`, and increments `fencing_token`.
3. **Heartbeat Loop:** While executing the Docker verification, a background asyncio heartbeat task renews `lease_expires_at` every 5 seconds.
4. **Stale Lease Recovery:** If a worker crashes or encounters an Out-Of-Memory (OOM) error, its lease expires. The recovery loop queries runs where `status = 'running'` and `lease_expires_at < now()`, resets the status to `queued`, and increments the retry count.
5. **Fencing Protection:** All status updates require `WHERE id = :id AND fencing_token = :token`, preventing late-arriving updates from zombie workers.

---

### Q2: What happens during container termination or rolling deployments? How is graceful shutdown handled?
**Answer:**  
RepoPilot Fix implements graceful worker draining:
- The `WorkerLoop` listens for `SIGTERM` and `SIGINT` signals.
- When a shutdown signal is received, `worker.stop(cancel_active=False)` is invoked.
- The worker stops polling for new runs but allows the currently running verification job to complete its container execution and record its verdict to the database.
- Docker Compose defines a `stop_grace_period: 30s` for the worker container, ensuring active test suites can complete before the daemon sends `SIGKILL`.

---

## Category 2: AST Code Intelligence & Retrieval

### Q3: Why did you choose AST symbol indexing over standard vector embedding RAG?
**Answer:**  
Vector embeddings suffer from several critical shortcomings in code repair:
1. **Lack of Syntactic Precision:** Vector embeddings capture vague semantic similarity (e.g., "authentication logic"), but cannot distinguish exact function signatures, parameter orders, or caller/callee relationships.
2. **Citation Hallucination:** Embedding chunks often slice code across arbitrary token boundaries, leading to broken syntax and hallucinated line citations.
3. **Cross-File Impact Blindness:** Embeddings cannot trace the blast radius of a function change across module boundaries.

**RepoPilot Fix's Approach:**
- We parse source files directly into Abstract Syntax Trees using Python's `ast` module and Tree-sitter.
- We extract exact symbol declarations (classes, methods, functions) with 1-indexed start/end lines and track `import` statements and call sites.
- This enables **static blast radius calculation**: given an issue in `src/calculator.py`, our graph traversal immediately identifies callers in `src/main.py` and test suites in `tests/test_calculator.py`.
- In our benchmarks, this achieved **100% Recall@5** and a **0.925 Mean Reciprocal Rank (MRR)**.

---

### Q4: How does your dependency graph handle circular imports?
**Answer:**  
Our dependency graph builder implements **Tarjan/DFS recursion stack cycle detection**:
- When traversing file import edges, the engine maintains an active recursion stack.
- If an edge targets a node already in the current stack, a cycle is detected and recorded in `circular_chains`.
- Rather than crashing or entering infinite loops during blast radius calculation, graph traversal depth is bounded (`max_depth=3`).
- In the frontend SVG visualizer, nodes participating in circular import chains are annotated with an amber warning badge (`↻ cycle`).

---

## Category 3: Security & Sandboxing Architecture

### Q5: What security measures prevent an untrusted repository or malicious patch from compromising the host?
**Answer:**  
RepoPilot Fix follows a strict defense-in-depth model across four distinct trust boundaries:
1. **Private Worker Boundary:** The public-facing FastAPI backend and Next.js frontend containers have **zero Docker socket access**. Docker socket access is strictly reserved for the private worker container residing on an isolated internal bridge network (`backend-net`).
2. **Air-Gapped Sandboxes:** Sandbox containers are executed with:
   - `--network none`: Complete network air-gapping prevents external data exfiltration or reverse shells.
   - `--read-only`: The root container filesystem is immutable.
   - `--tmpfs /tmp:rw,noexec,nosuid,size=64m`: In-memory temporary filesystem prevents disk persistence and binary execution.
   - `--user 1000:1000`: Execution as an unprivileged non-root user.
   - `--memory 512m --cpus 1.0 --pids-limit 100`: Hardware quotas prevent fork bombs and resource exhaustion.
3. **Scoped Cleanup:** Every container is labeled with `repopilot.managed=true` and cleaned up immediately in `finally` blocks, with orphaned container pruning on worker startup.

---

### Q6: What is the Spec Hash, and why is human approval cryptographically enforced?
**Answer:**  
The **Spec Hash** is an immutable cryptographic fingerprint computed prior to sandbox dispatch:
$$\text{SpecHash} = \text{SHA256}(\text{CommitSHA} \parallel \text{PatchHash} \parallel \text{TestCommand} \parallel \text{SandboxImage})$$

**Why It Matters:**
- **Zero-Trust AI:** We never automatically deploy or PR code proposed by an LLM.
- **Tamper Prevention:** If an attacker modifies the candidate patch diff or alters the test command after human review, the computed spec hash changes, causing the execution guard to reject the run with an HTTP 400 error.
- **Auditability:** Every human approval is recorded in `approval_audit_logs` with user ID, timestamp, and the exact spec hash that was signed off.

---

## Category 4: Benchmark Methodology & Empirical Results

### Q7: Can you explain your 85% benchmark success rate and how your evaluation was conducted?
**Answer:**  
We evaluated RepoPilot Fix across **20 curated defect-pattern fixtures** distilled from mature Python libraries (Click, Requests, Bottle, Marshmallow, Pydantic, HTTPX, Jinja, Werkzeug, etc.):
- **Methodology Transparency:** The historical 20-case benchmark was evaluated using isolated host-subprocess execution across self-contained defect fixtures. In production, host execution is disabled; verification requires our Docker sandbox.
- **Zero Solution Leakage:** The LLM was supplied only with the issue title, description, and retrieved AST context. No predefined golden patch was provided in the prompt.
- **Reproduction First:** All 20 cases confirmed 100% reproduction (failing baseline tests) before patch application.
- **Results:**
  - **17/20 (85.0%)** synthesized patches that passed the full test suite with **0 new regressions**.
  - **100% Recall@5** on defect file retrieval with **0.925 MRR**.
  - **3/3 (100%)** successful multi-file repairs.
  - Average inference latency: **10,906 ms**; average token consumption: **1,053 tokens**.

---

### Q8: How do you differentiate between real Docker tests and mocked sandbox tests in your CI suite?
**Answer:**  
We strictly categorize and separate our test suites:
- **Mocked Sandbox Tests (28 tests):** Located in [`apps/api/tests/test_sandbox.py`](file:///home/mehndi/Projects/repopilot-fix/apps/api/tests/test_sandbox.py). These mock Docker CLI subprocesses to rigorously verify argument formation (`--network none`, `--memory 512m`, PID limits), timeout handling, and exit code parsing without requiring a running Docker daemon.
- **Real Docker Sandbox Tests (2 tests):** Located in [`apps/api/tests/test_proof_of_fix_e2e_docker.py`](file:///home/mehndi/Projects/repopilot-fix/apps/api/tests/test_proof_of_fix_e2e_docker.py). These execute against the real host Docker daemon, launching real containers, confirming baseline reproduction, applying candidate diffs, and verifying proof reports.
- In our release CI, all 316 backend tests and 32 frontend tests pass with 0 failures.
