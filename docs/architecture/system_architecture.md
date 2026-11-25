# RepoPilot Fix — System Architecture & Technical Specifications

**Version:** 1.0.0 (Release)  
**Classification:** Technical System Architecture Documentation  

---

## 1. High-Level Architectural Model

RepoPilot Fix is architectured around the principle of **untrusted inputs, isolated execution, and cryptographically verified outputs**. The application comprises four distinct operational tiers:

```mermaid
graph TB
    subgraph ClientTier["Client Tier (Unprivileged Browser)"]
        Browser["Next.js 15 Client App Router"]
        SVGVisualizer["Native SVG Dependency Visualizer"]
        DiffViewer["Unified Diff Inspector"]
    end

    subgraph APITier["Public API Tier (Port 8000)"]
        FastAPIApp["FastAPI Application"]
        TokenBucket["In-Memory Rate Limiter"]
        AuthModule["Session Authenticator & CSRF Guard"]
        ASTParser["Tree-sitter & AST Symbol Indexer"]
        PatchSynthesizer["Prompt Formatter & Unified Diff Patcher"]
    end

    subgraph PersistenceTier["Persistence Tier (PostgreSQL 16)"]
        DB[("PostgreSQL 16 Database")]
        ReposTable["repositories"]
        SymbolsTable["code_symbols & file_imports"]
        RunsTable["verification_runs (Leases & Fencing)"]
        AuditTable["approval_audit_logs"]
        PRTable["github_pull_requests"]
    end

    subgraph ExecutionTier["Private Worker Execution Tier"]
        WorkerService["Durable Worker Loop (asyncio)"]
        LeaseManager["Atomic Row Lock & Heartbeat Renew"]
        DockerSpawner["Hardened Docker Sandbox Controller"]
        subgraph Sandboxes["Air-Gapped Sandboxes"]
            BaseBox["Baseline Container\n(--network none, --read-only)"]
            PatchBox["Patched Container\n(--network none, --read-only)"]
        end
    end

    Browser -->|HTTP JSON + CSRF Token| FastAPIApp
    FastAPIApp --> TokenBucket
    TokenBucket --> AuthModule
    AuthModule --> ASTParser
    ASTParser --> PatchSynthesizer
    FastAPIApp -->|SQLAlchemy 2.0 Pool| DB
    WorkerService -->|SELECT FOR UPDATE SKIP LOCKED| RunsTable
    WorkerService --> LeaseManager
    WorkerService --> DockerSpawner
    DockerSpawner --> BaseBox
    DockerSpawner --> PatchBox
    BaseBox -->|Reproduction Exit Code| WorkerService
    PatchBox -->|Patched Exit Code & Delta Failures| WorkerService
    WorkerService -->|Store Verdict & Evidence| DB
```

---

## 2. Trust Boundaries & Security Enclaves

RepoPilot Fix strictly segregates privileges to prevent untrusted code execution or prompt injection from escalating privileges:

```mermaid
flowchart LR
    subgraph PublicBoundary["Public / Untrusted Boundary"]
        UserBrowser["User Browser"]
        UntrustedRepo["Untrusted Code & Issue Text"]
    end

    subgraph ServiceBoundary["Public Service Boundary"]
        WebContainer["Web Container (Non-root 1001)"]
        APIContainer["API Container (Non-root 1000)"]
    end

    subgraph PrivateBoundary["Private Worker Boundary"]
        WorkerContainer["Worker Container (Non-root 1000)"]
        DockerSock["Docker Daemon Socket\n(/var/run/docker.sock)"]
    end

    subgraph SandboxBoundary["Air-Gapped Sandbox Boundary"]
        BaselineBox["Baseline Sandbox\n(Network: NONE, Memory: 512M)"]
        PatchedBox["Patched Sandbox\n(Network: NONE, Memory: 512M)"]
    end

    UserBrowser -->|Public HTTP| WebContainer
    WebContainer -->|Internal HTTP| APIContainer
    UntrustedRepo -->|Parsed as AST Only| APIContainer
    APIContainer -.->|NO DOCKER ACCESS| DockerSock
    WorkerContainer -->|Exclusive Mount| DockerSock
    WorkerContainer -->|Spawns Air-Gapped| BaselineBox
    WorkerContainer -->|Spawns Air-Gapped| PatchedBox
```

### Trust Boundary Rules
1. **Zero Public Socket Mounts:** The Docker socket `/var/run/docker.sock` is **never mounted** to the public API container or the Next.js web container.
2. **Air-Gapped Network:** Sandboxes run with `--network none`. They cannot connect to internal databases, cloud APIs, or the host.
3. **Non-Root Sandboxes:** Container processes execute as `1000:1000`. Root operations inside sandboxes are blocked.
4. **Temporary Filesystem Caps:** Temporary directories use `--tmpfs /tmp:rw,noexec,nosuid,size=64m`. Binary execution from `/tmp` is prohibited.
5. **Scoped Resource Cleanup:** Every container is labeled with `repopilot.managed=true`. A strict cleanup supervisor terminates orphaned containers on boot and upon run completion.

---

## 3. Database Architecture & Schema Relationships

```mermaid
erDiagram
    repositories ||--o{ code_intelligence_jobs : "indexes"
    repositories ||--o{ repository_files : "contains"
    repositories ||--o{ verification_runs : "evaluates"
    repositories ||--o{ chat_sessions : "hosts"
    repository_files ||--o{ code_symbols : "declares"
    repository_files ||--o{ file_imports : "references"
    chat_sessions ||--o{ chat_messages : "records"
    verification_runs ||--o| proof_reports : "generates"
    verification_runs ||--o{ approval_audit_logs : "audits"
    verification_runs ||--o| github_pull_requests : "exports"

    repositories {
        int id PK
        string full_name
        string default_branch
        string commit_sha
        datetime created_at
    }

    verification_runs {
        uuid id PK
        int repository_id FK
        string status
        string spec_hash
        string approved_spec_hash
        uuid approved_by_user_id
        string worker_id
        int fencing_token
        datetime lease_expires_at
    }

    proof_reports {
        uuid id PK
        uuid verification_run_id FK
        string verdict
        int baseline_exit_code
        int patched_exit_code
        json evidence_payload
    }

    github_pull_requests {
        uuid id PK
        uuid verification_run_id FK "UNIQUE (0009)"
        string pr_url
        int pr_number
        string branch_name
    }
```

### Schema Migration Integrity
- Schema migrations are managed through Alembic and are **never executed automatically on application startup**.
- **Migration 0009 (`0009_github_pull_requests_unique_run.py`):** Adds a unique constraint on `github_pull_requests.verification_run_id`. Before altering the schema, it runs a pre-flight duplicate query:
  ```sql
  SELECT verification_run_id, COUNT(*) 
  FROM github_pull_requests 
  GROUP BY verification_run_id 
  HAVING COUNT(*) > 1;
  ```
  If duplicates are found, it raises a descriptive `RuntimeError` rather than silently dropping records.

---

## 4. Distributed Concurrency & Worker Lease State Machine

```mermaid
sequenceDiagram
    autonumber
    participant Worker as Background Worker
    participant DB as PostgreSQL DB
    participant Engine as Proof Engine
    participant Sandbox as Docker Sandbox

    loop Every Poll Interval (2.0s)
        Worker->>DB: BEGIN TRANSACTION
        Worker->>DB: SELECT * FROM verification_runs WHERE status='queued' FOR UPDATE SKIP LOCKED LIMIT 1
        alt Job Available
            Worker->>DB: UPDATE verification_runs SET status='running', worker_id=:wid, fencing_token=fencing_token+1, lease_expires_at=now()+'30s'
            Worker->>DB: COMMIT TRANSACTION
            Worker->>Engine: execute_run(run)
            par Periodic Heartbeat (every 5s)
                loop While Running
                    Worker->>DB: UPDATE verification_runs SET lease_expires_at=now()+'30s' WHERE id=:id AND fencing_token=:token
                end
            and Execute Sandboxes
                Engine->>Sandbox: Spawn Baseline Container
                Sandbox-->>Engine: Baseline Result (Reproduces Bug)
                Engine->>Sandbox: Spawn Patched Container
                Sandbox-->>Engine: Patched Result (Tests Pass)
            end
            Engine->>DB: INSERT INTO proof_reports (verdict, evidence)
            Worker->>DB: UPDATE verification_runs SET status='completed' WHERE id=:id AND fencing_token=:token
        else No Job
            Worker->>DB: ROLLBACK
            Worker->>Worker: Sleep 2.0s
        end
    end
```

---

## 5. Blast Radius & AST Dependency Intelligence

When an issue or suspect file is analyzed, the AST Engine executes the following static pipeline:

```mermaid
flowchart TD
    Issue[Issue Title & Description] --> Lexer[Tokenize & Extract Identifiers]
    FileTarget[Target File: e.g. src/calc.py] --> ASTInspect[Parse AST Nodes & Symbol Tree]
    ASTInspect --> ExtractCallers[Query Call Sites in Other Files]
    ASTInspect --> ExtractCallees[Query Dependencies & Imports]
    Lexer --> ContextMatch[Match Target Symbols in File]
    ExtractCallers --> ImpactSet[Assemble Downstream Blast Radius]
    ExtractCallees --> ImpactSet
    ContextMatch --> ContextWindow[Select 11-Line Context Windows]
    ImpactSet --> TestFinder[Locate Suggestive Test Suites]
    ContextWindow --> PatchPrompt[Construct Patch Synthesis Prompt]
    TestFinder --> PatchPrompt
```

This ensures that the LLM is supplied with exact, bounded 1-indexed line windows and accurate caller/callee context, eliminating out-of-bounds citations and context blindness.
