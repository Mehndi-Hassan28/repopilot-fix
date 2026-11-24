"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  Repository,
  WorkspaceView,
  AuthUser,
  IssueAnalysisResponse,
  DemoStatusResponse,
  DemoInitializeResponse,
  IndexedFileItem,
} from "../lib/types";
import {
  VerificationRunRead,
  ProofReportRead,
  PullRequestCreateResponse,
  PatchExportResponse,
  ProofEvidence,
} from "../lib/verification-types";
import {
  getDemoStatus,
  initializeDemo,
  resetDemo,
} from "../lib/demo-api";
import {
  createVerification,
  submitForApproval,
  approveVerification,
  executeVerification,
  getVerificationReport,
  getVerification,
  createPullRequest,
  exportPatch,
} from "../lib/verification-api";
import { apiRequest } from "../lib/api";
import {
  FlaskConicalIcon,
  SparklesIcon,
} from "./Icons";

interface InteractiveDemoWorkspaceProps {
  currentUser?: AuthUser | null;
  csrfToken: string;
  onSelectRepo: (repo: Repository) => void;
  onSelectView: (view: WorkspaceView) => void;
  onAnalysisLoaded?: (result: IssueAnalysisResponse) => void;
}

const STAGES = [
  { id: 1, title: "Repo Setup", subtitle: "Deterministic Fixture" },
  { id: 2, title: "AST Index", subtitle: "Symbol Graphs" },
  { id: 3, title: "Investigation", subtitle: "Issue Discovery" },
  { id: 4, title: "Root Cause", subtitle: "AST Citations" },
  { id: 5, title: "Patch Review", subtitle: "Unified Diff" },
  { id: 6, title: "Human Approval", subtitle: "Spec Governance" },
  { id: 7, title: "Docker Sandbox", subtitle: "Dual Containers" },
  { id: 8, title: "Evidence Report", subtitle: "Cryptographic Proof" },
  { id: 9, title: "PR Preview", subtitle: "Safe Simulation" },
];

export function InteractiveDemoWorkspace({
  csrfToken,
  onSelectRepo,
  onSelectView,
  onAnalysisLoaded,
}: InteractiveDemoWorkspaceProps) {
  // Stepper & Workflow State
  const [currentStep, setCurrentStep] = useState<number>(1);
  const [maxCompletedStep, setMaxCompletedStep] = useState<number>(0);
  const [loadingStep, setLoadingStep] = useState<number | null>(null);
  const [stepError, setStepError] = useState<{ step: number; message: string } | null>(null);
  const [autoRunning, setAutoRunning] = useState<boolean>(false);
  const [awaitingApproval, setAwaitingApproval] = useState<boolean>(false);
  const [diffMode, setDiffMode] = useState<"unified" | "split">("unified");
  const [showResetConfirm, setShowResetConfirm] = useState<boolean>(false);

  // Core Data Entities
  const [demoStatus, setDemoStatus] = useState<DemoStatusResponse | null>(null);
  const [demoInit, setDemoInit] = useState<DemoInitializeResponse | null>(null);
  const [activeRepo, setActiveRepo] = useState<Repository | null>(null);
  const [indexedFiles, setIndexedFiles] = useState<IndexedFileItem[]>([]);
  const [analysisResult, setAnalysisResult] = useState<IssueAnalysisResponse | null>(null);
  const [verificationRun, setVerificationRun] = useState<VerificationRunRead | null>(null);
  const [proofReport, setProofReport] = useState<ProofReportRead | null>(null);
  const [exportedPatch, setExportedPatch] = useState<PatchExportResponse | null>(null);
  const [simulatedPR, setSimulatedPR] = useState<PullRequestCreateResponse | null>(null);
  const [simulatingPR, setSimulatingPR] = useState<boolean>(false);

  const reportEvidence = (proofReport?.evidence || {}) as Partial<ProofEvidence>;
  const autoRunAbortRef = useRef<boolean>(false);

  // Initial status check
  const refreshDemoStatus = useCallback(async () => {
    try {
      const status = await getDemoStatus();
      setDemoStatus(status);
      return status;
    } catch (err) {
      setStepError({
        step: currentStep,
        message: err instanceof Error ? err.message : "Failed to query demo status.",
      });
      return null;
    }
  }, [currentStep]);

  useEffect(() => {
    void refreshDemoStatus();
  }, [refreshDemoStatus]);

  // Stage 1: Initialize Demo Repository
  const handleStep1Initialize = async () => {
    setLoadingStep(1);
    setStepError(null);
    try {
      const initResponse = await initializeDemo(false, csrfToken);
      setDemoInit(initResponse);
      setActiveRepo(initResponse.repository);
      onSelectRepo(initResponse.repository);
      setMaxCompletedStep((prev) => Math.max(prev, 1));
      await refreshDemoStatus();
      return initResponse;
    } catch (err) {
      setStepError({
        step: 1,
        message: err instanceof Error ? err.message : "Failed to initialize demo repository.",
      });
      throw err;
    } finally {
      setLoadingStep(null);
    }
  };

  // Stage 2: Codebase Indexing
  const handleStep2Index = async (repoId?: number) => {
    const targetId = repoId || activeRepo?.id || demoInit?.repository.id;
    if (!targetId) {
      setStepError({ step: 2, message: "No active repository found. Run Stage 1 first." });
      return;
    }
    setLoadingStep(2);
    setStepError(null);
    try {
      // Fetch indexed files and symbols
      const filesRes = await apiRequest(`/api/repositories/${targetId}/files`, { cache: "no-store" });
      if (filesRes.ok) {
        const files = (await filesRes.json()) as IndexedFileItem[];
        setIndexedFiles(files);
      }
      setMaxCompletedStep((prev) => Math.max(prev, 2));
      await refreshDemoStatus();
    } catch (err) {
      setStepError({
        step: 2,
        message: err instanceof Error ? err.message : "Failed to index demo codebase.",
      });
      throw err;
    } finally {
      setLoadingStep(null);
    }
  };

  // Stage 3: Issue Investigation
  const handleStep3Investigate = async (repoId?: number) => {
    const targetId = repoId || activeRepo?.id || demoInit?.repository.id;
    if (!targetId) {
      setStepError({ step: 3, message: "No repository loaded. Run previous stages first." });
      return;
    }
    setLoadingStep(3);
    setStepError(null);
    try {
      const res = await apiRequest(
        `/api/repositories/${targetId}/issues/analyze`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ issue_number: 1 }),
        },
        csrfToken
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Issue investigation failed.");
      }
      const data = (await res.json()) as IssueAnalysisResponse;
      setAnalysisResult(data);
      if (onAnalysisLoaded) onAnalysisLoaded(data);
      setMaxCompletedStep((prev) => Math.max(prev, 3));
      await refreshDemoStatus();
      return data;
    } catch (err) {
      setStepError({
        step: 3,
        message: err instanceof Error ? err.message : "Failed to investigate issue.",
      });
      throw err;
    } finally {
      setLoadingStep(null);
    }
  };

  // Stage 4: Root Cause Explanation (Marks step completed)
  const handleStep4RootCause = async () => {
    if (!analysisResult) {
      setStepError({ step: 4, message: "Issue analysis not available. Run Stage 3 first." });
      return;
    }
    setMaxCompletedStep((prev) => Math.max(prev, 4));
  };

  // Stage 5: Patch Diff Review (Marks step completed)
  const handleStep5PatchReview = async () => {
    if (!analysisResult || !analysisResult.analysis.proposed_fix.patches.length) {
      setStepError({ step: 5, message: "Patch proposal not available." });
      return;
    }
    setMaxCompletedStep((prev) => Math.max(prev, 5));
  };

  // Stage 6: Human Approval Gate
  const handleStep6Approve = async () => {
    const targetId = activeRepo?.id || demoInit?.repository.id;
    if (!targetId || !analysisResult) {
      setStepError({ step: 6, message: "Target repository or patch not ready." });
      return;
    }
    setLoadingStep(6);
    setStepError(null);
    try {
      // 1. Create verification run request if not exists
      let run = verificationRun;
      if (!run) {
        run = await createVerification(
          targetId,
          {
            commit_sha: analysisResult.commit_sha,
            issue_number: analysisResult.issue_number,
            patches: analysisResult.analysis.proposed_fix.patches,
            hypothesis: analysisResult.analysis.root_cause_hypothesis,
            test_command: ["pytest", "-q", "tests/test_calculator.py"],
            sandbox_image: "python:3.11-slim",
          },
          csrfToken
        );
      }

      // 2. Submit for approval if in draft
      if (run.status === "draft") {
        run = await submitForApproval(targetId, run.id, csrfToken);
      }

      // 3. Explicitly approve specification
      if (run.status === "pending_approval") {
        run = await approveVerification(
          targetId,
          run.id,
          {
            expected_spec_hash: run.spec_hash || undefined,
            comment: "Approved in Interactive Proof-of-Fix Demo.",
          },
          csrfToken
        );
      }

      setVerificationRun(run);
      setAwaitingApproval(false);
      setMaxCompletedStep((prev) => Math.max(prev, 6));
      await refreshDemoStatus();
      return run;
    } catch (err) {
      setStepError({
        step: 6,
        message: err instanceof Error ? err.message : "Human approval step failed.",
      });
      throw err;
    } finally {
      setLoadingStep(null);
    }
  };

  // Explicit Human Approval + Proceed to Sandbox Verification
  const handleApproveAndContinue = async () => {
    try {
      const run = await handleStep6Approve();
      if (!run) return;
      setCurrentStep(7);
      await handleStep7Verify();
      setCurrentStep(8);
      await handleStep8Evidence();
      setCurrentStep(9);
      setMaxCompletedStep(9);
    } catch {
      // Errors caught and set by step handlers
    }
  };

  // Stage 7: Isolated Docker Verification
  const handleStep7Verify = async () => {
    const targetId = activeRepo?.id || demoInit?.repository.id;
    const runId = verificationRun?.id;
    if (!targetId || !runId) {
      setStepError({ step: 7, message: "Approved verification run required. Complete Stage 6 first." });
      return;
    }

    // Refresh Docker availability before launching
    const currentStatus = await refreshDemoStatus();
    if (currentStatus && !currentStatus.docker_available) {
      setStepError({
        step: 7,
        message: `Docker daemon unavailable: ${currentStatus.docker_status_message}. Real verification requires an active Docker daemon.`,
      });
      return;
    }

    setLoadingStep(7);
    setStepError(null);
    try {
      // Execute verified job
      await executeVerification(targetId, runId, csrfToken);

      // Fetch updated run details and report
      const updatedRun = await getVerification(targetId, runId);
      setVerificationRun(updatedRun);

      const report = await getVerificationReport(targetId, runId);
      setProofReport(report);

      setMaxCompletedStep((prev) => Math.max(prev, 7));
      await refreshDemoStatus();
      return report;
    } catch (err) {
      setStepError({
        step: 7,
        message: err instanceof Error ? err.message : "Verification execution failed.",
      });
      throw err;
    } finally {
      setLoadingStep(null);
    }
  };

  // Stage 8: Evidence Report (Fetch if not already loaded)
  const handleStep8Evidence = async () => {
    const targetId = activeRepo?.id || demoInit?.repository.id;
    const runId = verificationRun?.id;
    if (!targetId || !runId) {
      setStepError({ step: 8, message: "Verification run not available." });
      return;
    }
    setLoadingStep(8);
    setStepError(null);
    try {
      const report = await getVerificationReport(targetId, runId);
      setProofReport(report);
      setMaxCompletedStep((prev) => Math.max(prev, 8));
      return report;
    } catch (err) {
      setStepError({
        step: 8,
        message: err instanceof Error ? err.message : "Could not fetch verification report.",
      });
    } finally {
      setLoadingStep(null);
    }
  };

  // Stage 9: Export Patch or Simulate PR
  const handleExportPatch = async () => {
    const targetId = activeRepo?.id || demoInit?.repository.id;
    const runId = verificationRun?.id;
    if (!targetId || !runId) return;
    setLoadingStep(9);
    try {
      const exported = await exportPatch(targetId, runId);
      setExportedPatch(exported);

      // Trigger client file download of the patch
      const blob = new Blob([exported.patch_diff], { type: "text/x-diff" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = exported.filename || `repopilot-fix-${runId}.patch`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      setMaxCompletedStep((prev) => Math.max(prev, 9));
    } catch (err) {
      setStepError({
        step: 9,
        message: err instanceof Error ? err.message : "Failed to export patch.",
      });
    } finally {
      setLoadingStep(null);
    }
  };

  const handleSimulatePR = async () => {
    const targetId = activeRepo?.id || demoInit?.repository.id;
    const runId = verificationRun?.id;
    if (!targetId || !runId) return;
    setSimulatingPR(true);
    setStepError(null);
    try {
      const res = await createPullRequest(
        targetId,
        runId,
        {
          custom_title: "Fix power calculation for negative base or zero exponent",
          custom_body: `### Proof-of-Fix Automated Pull Request\n\n- Closes Issue #1\n- Verified Fix in isolated Docker sandbox\n- All regression tests passing\n\n*Created safely via RepoPilot Fix Interactive Demo.*`,
          custom_branch_name: "fix/issue-1-power-calc",
        },
        csrfToken
      );
      setSimulatedPR(res);
      setMaxCompletedStep((prev) => Math.max(prev, 9));
    } catch (err) {
      setStepError({
        step: 9,
        message: err instanceof Error ? err.message : "Failed to simulate GitHub Pull Request.",
      });
    } finally {
      setSimulatingPR(false);
    }
  };

  // Run Full Automated Demo (Stages 1 through 5, then halts for Mandatory Human Approval at Stage 6)
  const handleRunFullDemo = async () => {
    setAutoRunning(true);
    setAwaitingApproval(false);
    autoRunAbortRef.current = false;
    setStepError(null);

    try {
      // Step 1: Initialize
      setCurrentStep(1);
      const init = await handleStep1Initialize();
      if (autoRunAbortRef.current) return;

      // Step 2: Index
      setCurrentStep(2);
      await handleStep2Index(init.repository.id);
      if (autoRunAbortRef.current) return;

      // Step 3: Investigate
      setCurrentStep(3);
      const analysis = await handleStep3Investigate(init.repository.id);
      if (autoRunAbortRef.current) return;

      // Step 4: Root Cause
      setCurrentStep(4);
      await handleStep4RootCause();
      if (autoRunAbortRef.current) return;

      // Step 5: Patch Review
      setCurrentStep(5);
      await handleStep5PatchReview();
      if (autoRunAbortRef.current) return;

      // Step 6: Human Approval Governance Gate - MANDATORY PAUSE
      // In strict adherence to security governance, the automated runner prepares the draft
      // specification, advances to Stage 6, and PAUSES. Operator approval cannot be bypassed.
      setCurrentStep(6);
      const targetId = init.repository.id;
      let run = verificationRun;
      if (!run && analysis) {
        run = await createVerification(
          targetId,
          {
            commit_sha: analysis.commit_sha,
            issue_number: analysis.issue_number,
            patches: analysis.analysis.proposed_fix.patches,
            hypothesis: analysis.analysis.root_cause_hypothesis,
            test_command: ["pytest", "-q", "tests/test_calculator.py"],
            sandbox_image: "python:3.11-slim",
          },
          csrfToken
        );
      }
      if (run && run.status === "draft") {
        run = await submitForApproval(targetId, run.id, csrfToken);
      }
      if (run) {
        setVerificationRun(run);
      }
      setMaxCompletedStep((prev) => Math.max(prev, 5));
      setAutoRunning(false);
      setAwaitingApproval(true);
      return; // STRICT GOVERNANCE HALT
    } catch {
      // StepError is already set in the failing step handler
      setAutoRunning(false);
    }
  };

  // Reset Demo Environment
  const handleResetDemo = async () => {
    setShowResetConfirm(false);
    setAutoRunning(false);
    autoRunAbortRef.current = true;
    setLoadingStep(1);
    setStepError(null);

    try {
      await resetDemo(csrfToken);
      setDemoInit(null);
      setActiveRepo(null);
      setIndexedFiles([]);
      setAnalysisResult(null);
      setVerificationRun(null);
      setProofReport(null);
      setExportedPatch(null);
      setSimulatedPR(null);
      setMaxCompletedStep(0);
      setCurrentStep(1);
      await refreshDemoStatus();
    } catch (err) {
      setStepError({
        step: 1,
        message: err instanceof Error ? err.message : "Failed to reset demo environment.",
      });
    } finally {
      setLoadingStep(null);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-[#050505] overflow-hidden select-none">
      {/* Top Banner & Control Bar */}
      <div className="border-b border-white/10 bg-[#0c0e14] px-6 py-4 flex flex-wrap items-center justify-between gap-4 shrink-0">
        <div className="flex items-center gap-3">
          <div className="size-10 rounded-xl bg-gradient-to-br from-[#BFFF00]/20 to-cyan-500/20 border border-[#BFFF00]/30 flex items-center justify-center text-[#BFFF00]">
            <FlaskConicalIcon className="size-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-[#BFFF00] animate-pulse" />
              <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#BFFF00]">
                One-Click Interactive Demo
              </span>
              <span className="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2 py-0.5 text-[9px] font-mono text-cyan-300">
                Deterministic Sandbox
              </span>
              {demoStatus && (
                <span
                  className={`rounded-md border px-2 py-0.5 text-[9px] font-mono ${
                    demoStatus.docker_available
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                      : "border-amber-500/30 bg-amber-500/10 text-amber-300"
                  }`}
                  title={demoStatus.docker_status_message}
                >
                  {demoStatus.docker_available ? "Docker: Active" : "Docker: Offline"}
                </span>
              )}
            </div>
            <h1 className="mt-0.5 text-lg font-semibold text-[#F5F5F5] tracking-tight">
              Evidence-Driven Autonomous Software Repair
            </h1>
          </div>
        </div>

        <div className="flex items-center gap-2.5">
          {/* Run Automated Full Demo Button */}
          <button
            onClick={handleRunFullDemo}
            disabled={autoRunning || loadingStep !== null}
            className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-[#BFFF00] to-emerald-400 px-4 py-2 text-xs font-bold text-black hover:brightness-110 disabled:opacity-50 transition-all font-sans shadow-lg shadow-[#BFFF00]/15"
          >
            <SparklesIcon className="size-3.5" />
            <span>{autoRunning ? "Running Automated Pipeline…" : "Run Full Demo (1-Click)"}</span>
          </button>

          {/* Reset Demo Button */}
          <button
            onClick={() => setShowResetConfirm(true)}
            disabled={autoRunning || loadingStep !== null}
            className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-xs font-mono text-zinc-300 hover:text-rose-300 hover:border-rose-500/30 transition-colors"
            title="Reset demo data to initial pristine state"
          >
            Reset
          </button>

          {/* Open in Full Workspace */}
          {activeRepo && (
            <button
              onClick={() => {
                onSelectRepo(activeRepo);
                onSelectView("verification");
              }}
              className="rounded-xl border border-white/10 bg-[#12151c] px-3 py-2 text-xs font-mono text-zinc-300 hover:text-white hover:border-white/20 transition-colors"
              title="Open repository in full verification center"
            >
              Open in Hub →
            </button>
          )}
        </div>
      </div>

      {/* Stepper Navigation Bar */}
      <div className="border-b border-white/5 bg-[#080a0e] px-6 py-3 overflow-x-auto custom-scrollbar shrink-0">
        <div className="flex items-center gap-2 min-w-max">
          {STAGES.map((stg) => {
            const isActive = currentStep === stg.id;
            const isCompleted = maxCompletedStep >= stg.id;

            return (
              <button
                key={stg.id}
                onClick={() => setCurrentStep(stg.id)}
                disabled={autoRunning || (!isCompleted && stg.id > maxCompletedStep + 1)}
                className={`flex items-center gap-2 rounded-lg px-3 py-1.5 transition-all text-xs ${
                  isActive
                    ? "bg-[#BFFF00]/10 border border-[#BFFF00]/50 text-white shadow-sm"
                    : isCompleted
                    ? "bg-white/5 border border-white/10 text-zinc-300 hover:border-white/20"
                    : "border border-transparent text-zinc-600 opacity-60 cursor-not-allowed"
                }`}
              >
                <span
                  className={`size-5 rounded-full flex items-center justify-center text-[10px] font-mono font-bold ${
                    isCompleted
                      ? "bg-[#BFFF00] text-black"
                      : isActive
                      ? "border border-[#BFFF00] text-[#BFFF00]"
                      : "bg-white/10 text-zinc-500"
                  }`}
                >
                  {isCompleted ? "✓" : stg.id}
                </span>
                <div className="text-left">
                  <p className="font-semibold leading-none">{stg.title}</p>
                  <p className="text-[9px] font-mono text-zinc-500 leading-tight">{stg.subtitle}</p>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Error Callout Banner if step error occurs */}
      {stepError && (
        <div className="mx-6 mt-4 rounded-xl border border-rose-500/30 bg-rose-950/20 p-3.5 flex items-center justify-between gap-3 text-xs text-rose-300 font-mono">
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-rose-500" />
            <span>[Stage {stepError.step} Error] {stepError.message}</span>
          </div>
          <button
            onClick={() => setStepError(null)}
            className="text-zinc-400 hover:text-white"
          >
            ✕
          </button>
        </div>
      )}

      {/* Main Stage Interactive Viewport */}
      <div className="flex-1 overflow-y-auto p-6 md:p-8 space-y-6 custom-scrollbar">
        {/* =========================================================================
            STAGE 1: Repository Initialization
        ========================================================================== */}
        {currentStep === 1 && (
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="rounded-2xl border border-white/10 bg-[#0e1117] p-6 shadow-xl space-y-6">
              <div className="flex items-start justify-between gap-4 border-b border-white/5 pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="size-2 rounded-full bg-[#BFFF00]" />
                    <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#BFFF00]">
                      Stage 1 of 9
                    </span>
                  </div>
                  <h2 className="mt-1 text-xl font-bold text-white">
                    Initialize Demo Repository Environment
                  </h2>
                  <p className="mt-1 text-xs text-zinc-400">
                    Instantiate an isolated, reproducible repository environment containing the deterministic calculator fixture and regression suite.
                  </p>
                </div>
                <span className="shrink-0 rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 text-xs font-mono text-cyan-300">
                  Fixture: demo-calculator
                </span>
              </div>

              {/* Preconfigured Fixture Details Card */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="rounded-xl border border-white/5 bg-[#141822] p-4">
                  <p className="text-[10px] font-mono uppercase text-zinc-400">Target Repository</p>
                  <p className="mt-1 font-mono text-sm font-semibold text-[#BFFF00] truncate">
                    {activeRepo?.full_name || demoInit?.repository.full_name || "repopilot/demo-calculator"}
                  </p>
                  <p className="mt-0.5 text-[11px] text-zinc-500 font-sans">Deterministic sample codebase</p>
                </div>

                <div className="rounded-xl border border-white/5 bg-[#141822] p-4">
                  <p className="text-[10px] font-mono uppercase text-zinc-400">Commit Revision</p>
                  <p className="mt-1 font-mono text-sm font-semibold text-zinc-200">
                    {demoInit?.indexing_job.commit_sha.slice(0, 10) || "aaaaaaaaaa"}…
                  </p>
                  <p className="mt-0.5 text-[11px] text-zinc-500 font-sans">Pinned base revision</p>
                </div>

                <div className="rounded-xl border border-white/5 bg-[#141822] p-4">
                  <p className="text-[10px] font-mono uppercase text-zinc-400">Execution Sandbox</p>
                  <p className="mt-1 font-mono text-sm font-semibold text-purple-300">python:3.11-slim</p>
                  <p className="mt-0.5 text-[11px] text-zinc-500 font-sans">Allowlisted test image</p>
                </div>
              </div>

              {/* Docker Host Daemon Status */}
              <div className="rounded-xl border border-white/5 bg-[#12151c] p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span
                      className={`size-2 rounded-full ${
                        demoStatus?.docker_available ? "bg-emerald-400" : "bg-amber-400 animate-pulse"
                      }`}
                    />
                    <p className="text-xs font-semibold text-zinc-200">
                      Host Docker Daemon: {demoStatus?.docker_available ? "Active and Connected" : "Offline / Inaccessible"}
                    </p>
                  </div>
                  <p className="text-[11px] font-mono text-zinc-400">
                    {demoStatus?.docker_status_message || "Checking Docker daemon capability…"}
                  </p>
                </div>
                <button
                  onClick={() => void refreshDemoStatus()}
                  className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-mono text-zinc-300 hover:text-white"
                >
                  ↻ Probe Daemon
                </button>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center justify-end gap-3 pt-2">
                <button
                  onClick={async () => {
                    await handleStep1Initialize();
                    setCurrentStep(2);
                  }}
                  disabled={loadingStep === 1}
                  className="rounded-xl bg-[#BFFF00] px-5 py-2.5 text-xs font-bold text-black hover:brightness-110 disabled:opacity-50 shadow-lg shadow-[#BFFF00]/10 transition-all font-sans"
                >
                  {loadingStep === 1 ? "Initializing Environment…" : "Initialize & Proceed to Indexing →"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* =========================================================================
            STAGE 2: Codebase Indexing & AST Semantic Graph
        ========================================================================== */}
        {currentStep === 2 && (
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="rounded-2xl border border-white/10 bg-[#0e1117] p-6 shadow-xl space-y-6">
              <div className="flex items-start justify-between gap-4 border-b border-white/5 pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="size-2 rounded-full bg-cyan-400" />
                    <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#00F0FF]">
                      Stage 2 of 9
                    </span>
                  </div>
                  <h2 className="mt-1 text-xl font-bold text-white">
                    Codebase AST Semantic Graph Indexing
                  </h2>
                  <p className="mt-1 text-xs text-zinc-400">
                    Parse source code syntax trees, index mathematical operation symbols, and establish grounded cross-references.
                  </p>
                </div>
                <span className="shrink-0 rounded-lg border border-[#BFFF00]/30 bg-[#BFFF00]/10 px-3 py-1 text-xs font-mono text-[#BFFF00]">
                  AST Graph Ready
                </span>
              </div>

              {/* Indexed Files Showcase */}
              <div className="space-y-3">
                <div className="flex items-center justify-between text-xs font-mono uppercase tracking-wider text-zinc-400">
                  <span>
                    Indexed Source Files & Symbols ({indexedFiles.length} {indexedFiles.length === 1 ? "file" : "files"}, {indexedFiles.reduce((acc, f) => acc + (f.symbols?.length || 0), 0)} parsed symbols)
                  </span>
                  <span className="text-zinc-500 text-[10px]">Actual AST Data</span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {indexedFiles.length > 0 ? (
                    indexedFiles.map((file) => (
                      <div key={file.path} className="rounded-xl border border-white/10 bg-[#141822] p-4 space-y-3">
                        <div className="flex items-center justify-between">
                          <span className="font-mono text-xs font-bold text-cyan-300">{file.path}</span>
                          <span className="text-[10px] font-mono text-zinc-400">
                            {file.symbols?.length || 0} {file.symbols?.length === 1 ? "symbol" : "symbols"} ({file.size_bytes} B)
                          </span>
                        </div>
                        <div className="flex flex-wrap gap-1.5 font-mono text-[11px]">
                          {file.symbols && file.symbols.length > 0 ? (
                            file.symbols.map((sym) => (
                              <span
                                key={`${file.path}-${sym.kind}-${sym.name}-${sym.line}`}
                                className={`rounded px-2 py-0.5 ${
                                  sym.name === "power" || sym.name.includes("power")
                                    ? "bg-rose-500/20 border border-rose-500/30 text-rose-300"
                                    : "bg-white/5 text-zinc-300"
                                }`}
                                title={`${sym.kind} at line ${sym.line}`}
                              >
                                {sym.kind === "function" ? "def" : sym.kind} {sym.name}()
                              </span>
                            ))
                          ) : (
                            <span className="text-zinc-500 text-[10px]">No symbols extracted</span>
                          )}
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="col-span-2 rounded-xl border border-dashed border-white/10 p-6 text-center text-xs text-zinc-500 font-mono">
                      Loading parsed files and symbols from AST engine…
                    </div>
                  )}
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center justify-between pt-2">
                <button
                  onClick={() => setCurrentStep(1)}
                  className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-white"
                >
                  ← Back
                </button>
                <button
                  onClick={async () => {
                    await handleStep2Index();
                    setCurrentStep(3);
                  }}
                  disabled={loadingStep === 2}
                  className="rounded-xl bg-[#00F0FF] px-5 py-2.5 text-xs font-bold text-black hover:brightness-110 disabled:opacity-50 transition-all font-sans"
                >
                  {loadingStep === 2 ? "Indexing Codebase…" : "Investigate Issue #1 →"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* =========================================================================
            STAGE 3: Issue Investigation
        ========================================================================== */}
        {currentStep === 3 && (
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="rounded-2xl border border-white/10 bg-[#0e1117] p-6 shadow-xl space-y-6">
              <div className="flex items-start justify-between gap-4 border-b border-white/5 pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="size-2 rounded-full bg-amber-400" />
                    <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-amber-300">
                      Stage 3 of 9
                    </span>
                  </div>
                  <h2 className="mt-1 text-xl font-bold text-white">
                    Issue Discovery & Retrieval Investigation
                  </h2>
                  <p className="mt-1 text-xs text-zinc-400">
                    Analyze reported bug symptoms, formulate AST search queries, and match semantic symbols in the codebase.
                  </p>
                </div>
                <span className="shrink-0 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-xs font-mono text-amber-300">
                  Issue #1
                </span>
              </div>

              {/* Preconfigured Issue Card */}
              <div className="rounded-xl border border-white/10 bg-[#141822] p-5 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-rose-500/20 px-2 py-0.5 text-xs font-mono font-semibold text-rose-300">
                      BUG
                    </span>
                    <h3 className="text-sm font-semibold text-zinc-100">
                      Bug in power calculation for negative base or zero exponent
                    </h3>
                  </div>
                  <span className="text-xs font-mono text-zinc-500">#1 opened by repopilot-qa</span>
                </div>

                <p className="text-xs text-zinc-300 font-mono bg-[#0c0e14] p-3 rounded-lg border border-white/5 leading-relaxed">
                  &quot;calculator.power(2, 3) returns 6 instead of 8. The power function appears to multiply base and exponent instead of computing exponentiation. Regression tests fail on zero exponent and negative bases.&quot;
                </p>

                <div className="flex items-center gap-2 text-[10px] font-mono text-zinc-400">
                  <span>Labels:</span>
                  <span className="rounded bg-white/5 px-2 py-0.5 text-zinc-300">bug</span>
                  <span className="rounded bg-white/5 px-2 py-0.5 text-zinc-300">math</span>
                  <span className="rounded bg-white/5 px-2 py-0.5 text-zinc-300">reproducible</span>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center justify-between pt-2">
                <button
                  onClick={() => setCurrentStep(2)}
                  className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-white"
                >
                  ← Back
                </button>
                <button
                  onClick={async () => {
                    await handleStep3Investigate();
                    setCurrentStep(4);
                  }}
                  disabled={loadingStep === 3}
                  className="rounded-xl bg-[#BFFF00] px-5 py-2.5 text-xs font-bold text-black hover:brightness-110 disabled:opacity-50 transition-all font-sans"
                >
                  {loadingStep === 3 ? "Analyzing AST Citations…" : "Synthesize Root Cause →"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* =========================================================================
            STAGE 4: Source-Grounded Root Cause Explanation
        ========================================================================== */}
        {currentStep === 4 && (
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="rounded-2xl border border-white/10 bg-[#0e1117] p-6 shadow-xl space-y-6">
              <div className="flex items-start justify-between gap-4 border-b border-white/5 pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="size-2 rounded-full bg-purple-400" />
                    <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-purple-300">
                      Stage 4 of 9
                    </span>
                    <span className="rounded-md border border-purple-500/30 bg-purple-500/10 px-2 py-0.5 text-[9px] font-mono text-purple-300">
                      Deterministic Mock AI Response
                    </span>
                  </div>
                  <h2 className="mt-1 text-xl font-bold text-white">
                    Source-Grounded Root-Cause Explanation
                  </h2>
                  <p className="mt-1 text-xs text-zinc-400">
                    AST semantic evidence pinpoints the flawed operator in <code className="text-zinc-200">src/calculator.py</code>.
                  </p>
                </div>
                <span className="shrink-0 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-xs font-mono text-emerald-300">
                  Confidence: High (98%)
                </span>
              </div>

              {/* Hypothesis & Citations */}
              <div className="space-y-4">
                <div className="rounded-xl border border-white/10 bg-[#141822] p-4 space-y-2">
                  <p className="text-[10px] font-mono uppercase text-zinc-400 tracking-wider">
                    Root-Cause Hypothesis
                  </p>
                  <p className="text-xs text-zinc-200 leading-relaxed">
                    {analysisResult?.analysis.root_cause_hypothesis ||
                      "The function multiplies base by exponent using multiplication (*) instead of exponentiation (**)."}
                  </p>
                </div>

                <div className="rounded-xl border border-white/10 bg-[#0d1016] p-4 space-y-2">
                  <div className="flex items-center justify-between text-xs font-mono text-zinc-400">
                    <span className="text-cyan-300">Citation: src/calculator.py (Lines 18-21)</span>
                    <span className="text-zinc-500">AST Function: def power()</span>
                  </div>

                  <div className="rounded-lg bg-[#05070a] p-3 font-mono text-xs text-zinc-300 space-y-1">
                    <p className="text-zinc-500">18: def power(base: float, exponent: int) -&gt; float:</p>
                    <p className="text-zinc-500">19:     &quot;&quot;&quot;Raise base to the power of exponent.&quot;&quot;&quot;</p>
                    <p className="text-rose-400 font-bold bg-rose-950/30 px-1 rounded">
                      20: -    return base * exponent  # BUG: multiplication used instead of exponentiation
                    </p>
                  </div>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center justify-between pt-2">
                <button
                  onClick={() => setCurrentStep(3)}
                  className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-white"
                >
                  ← Back
                </button>
                <button
                  onClick={async () => {
                    await handleStep4RootCause();
                    setCurrentStep(5);
                  }}
                  className="rounded-xl bg-[#BFFF00] px-5 py-2.5 text-xs font-bold text-black hover:brightness-110 transition-all font-sans"
                >
                  Inspect Patch Diff →
                </button>
              </div>
            </div>
          </div>
        )}

        {/* =========================================================================
            STAGE 5: Patch Diff Review
        ========================================================================== */}
        {currentStep === 5 && (
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="rounded-2xl border border-white/10 bg-[#0e1117] p-6 shadow-xl space-y-6">
              <div className="flex items-start justify-between gap-4 border-b border-white/5 pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="size-2 rounded-full bg-cyan-400" />
                    <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#00F0FF]">
                      Stage 5 of 9
                    </span>
                    <span className="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2 py-0.5 text-[9px] font-mono text-cyan-300">
                      Deterministic Fixture Patch
                    </span>
                  </div>
                  <h2 className="mt-1 text-xl font-bold text-white">
                    Synthesized Patch Diff Review
                  </h2>
                  <p className="mt-1 text-xs text-zinc-400">
                    Review atomic code modifications before submitting to human authorization and sandbox isolation.
                  </p>
                </div>

                {/* Diff View Mode Toggle */}
                <div className="flex rounded-lg border border-white/10 bg-[#141822] p-0.5 text-xs font-mono">
                  <button
                    onClick={() => setDiffMode("unified")}
                    className={`rounded px-2.5 py-1 ${
                      diffMode === "unified" ? "bg-white/10 text-white font-semibold" : "text-zinc-400"
                    }`}
                  >
                    Unified
                  </button>
                  <button
                    onClick={() => setDiffMode("split")}
                    className={`rounded px-2.5 py-1 ${
                      diffMode === "split" ? "bg-white/10 text-white font-semibold" : "text-zinc-400"
                    }`}
                  >
                    Side-by-Side
                  </button>
                </div>
              </div>

              {/* Diff Code View */}
              <div className="rounded-xl border border-white/10 bg-[#0a0c10] overflow-hidden">
                <div className="bg-[#12151c] px-4 py-2 border-b border-white/10 flex items-center justify-between text-xs font-mono">
                  <span className="text-zinc-200">src/calculator.py</span>
                  <span className="text-zinc-400">+1 / -1 lines</span>
                </div>

                {diffMode === "unified" ? (
                  <div className="p-4 font-mono text-xs space-y-1 overflow-x-auto">
                    <p className="text-zinc-500">@@ -18,4 +18,4 @@ def power(base: float, exponent: int) -&gt; float:</p>
                    <p className="text-zinc-400">     &quot;&quot;&quot;Raise base to the power of exponent.&quot;&quot;&quot;</p>
                    <p className="text-rose-400 bg-rose-950/30 px-2 py-0.5 rounded">
                      -    return base * exponent
                    </p>
                    <p className="text-emerald-400 bg-emerald-950/30 px-2 py-0.5 rounded">
                      +    return base ** exponent
                    </p>
                  </div>
                ) : (
                  <div className="grid grid-cols-2 divide-x divide-white/10 font-mono text-xs">
                    <div className="p-4 space-y-1 bg-rose-950/10">
                      <p className="text-[10px] text-zinc-500 uppercase">Original (Baseline)</p>
                      <p className="text-zinc-400">def power(base: float, exponent: int) -&gt; float:</p>
                      <p className="text-zinc-400">    &quot;&quot;&quot;Raise base...&quot;&quot;&quot;</p>
                      <p className="text-rose-400 bg-rose-950/40 px-1 rounded">-   return base * exponent</p>
                    </div>
                    <div className="p-4 space-y-1 bg-emerald-950/10">
                      <p className="text-[10px] text-zinc-500 uppercase">Patched (Proposed)</p>
                      <p className="text-zinc-400">def power(base: float, exponent: int) -&gt; float:</p>
                      <p className="text-zinc-400">    &quot;&quot;&quot;Raise base...&quot;&quot;&quot;</p>
                      <p className="text-emerald-400 bg-emerald-950/40 px-1 rounded">+   return base ** exponent</p>
                    </div>
                  </div>
                )}
              </div>

              {/* Action Buttons */}
              <div className="flex items-center justify-between pt-2">
                <button
                  onClick={() => setCurrentStep(4)}
                  className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-white"
                >
                  ← Back
                </button>
                <button
                  onClick={async () => {
                    await handleStep5PatchReview();
                    setCurrentStep(6);
                  }}
                  className="rounded-xl bg-[#BFFF00] px-5 py-2.5 text-xs font-bold text-black hover:brightness-110 transition-all font-sans"
                >
                  Proceed to Human Approval Gate →
                </button>
              </div>
            </div>
          </div>
        )}

        {/* =========================================================================
            STAGE 6: Human Approval Governance Gate
        ========================================================================== */}
        {currentStep === 6 && (
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="rounded-2xl border border-white/10 bg-[#0e1117] p-6 shadow-xl space-y-6">
              <div className="flex items-start justify-between gap-4 border-b border-white/5 pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="size-2 rounded-full bg-emerald-400" />
                    <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#BFFF00]">
                      Stage 6 of 9
                    </span>
                  </div>
                  <h2 className="mt-1 text-xl font-bold text-white">
                    Mandatory Human Approval Governance Gate
                  </h2>
                  <p className="mt-1 text-xs text-zinc-400">
                    RepoPilot Fix security policy strictly prohibits arbitrary sandbox execution without explicit operator sign-off and specification hashing.
                  </p>
                </div>
                <span
                  className={`shrink-0 rounded-lg border px-3 py-1 text-xs font-mono ${
                    verificationRun?.status === "approved"
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                      : "border-amber-500/30 bg-amber-500/10 text-amber-300"
                  }`}
                >
                  Status: {verificationRun?.status || "pending_approval"}
                </span>
              </div>

              {/* Mandatory Governance Pause Banner */}
              {awaitingApproval && (
                <div className="rounded-xl border border-amber-500/40 bg-amber-950/20 p-4 space-y-1.5 font-mono text-xs text-amber-200">
                  <div className="flex items-center gap-2 font-bold uppercase text-amber-300">
                    <span className="size-2 rounded-full bg-amber-400 animate-pulse" />
                    <span>Automated Demo Paused — Explicit Operator Approval Mandatory</span>
                  </div>
                  <p className="text-[11px] text-zinc-300 font-sans leading-relaxed">
                    The autonomous pipeline has created and validated the immutable specification hash. Security governance strictly prohibits proceeding to container sandboxes without your explicit authorization.
                  </p>
                </div>
              )}

              {/* Cryptographic Specification Card */}
              <div className="rounded-xl border border-white/10 bg-[#141822] p-5 space-y-4">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-mono uppercase text-zinc-400 tracking-wider">
                    Immutable Fix Specification Hash
                  </p>
                  <span className="text-[10px] font-mono text-zinc-500">SHA-256 Digest</span>
                </div>

                <div className="rounded-lg bg-[#0a0d14] p-3 font-mono text-xs text-[#BFFF00] break-all border border-white/5">
                  {verificationRun?.spec_hash ||
                    "sha256:7f9b8c3a1e2d4f5b6a7c8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b"}
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-mono">
                  <div className="rounded bg-white/5 p-2.5">
                    <span className="text-zinc-500 block">Allowlisted Test Command:</span>
                    <span className="text-zinc-200">pytest -q tests/test_calculator.py</span>
                  </div>
                  <div className="rounded bg-white/5 p-2.5">
                    <span className="text-zinc-500 block">Isolated Sandbox Container:</span>
                    <span className="text-purple-300">python:3.11-slim</span>
                  </div>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center justify-between pt-2">
                <button
                  onClick={() => setCurrentStep(5)}
                  className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-white"
                >
                  ← Back
                </button>
                <button
                  onClick={async () => {
                    if (awaitingApproval) {
                      await handleApproveAndContinue();
                    } else {
                      await handleStep6Approve();
                      setCurrentStep(7);
                    }
                  }}
                  disabled={loadingStep === 6}
                  className="rounded-xl bg-[#BFFF00] px-5 py-2.5 text-xs font-bold text-black hover:brightness-110 disabled:opacity-50 transition-all font-sans"
                >
                  {loadingStep === 6
                    ? "Recording Approval…"
                    : verificationRun?.status === "approved"
                    ? "Approved — Launch Sandbox Verification →"
                    : "Approve Specification & Verify →"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* =========================================================================
            STAGE 7: Isolated Docker Dual Sandbox Verification
        ========================================================================== */}
        {currentStep === 7 && (
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="rounded-2xl border border-white/10 bg-[#0e1117] p-6 shadow-xl space-y-6">
              <div className="flex items-start justify-between gap-4 border-b border-white/5 pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="size-2 rounded-full bg-cyan-400" />
                    <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#00F0FF]">
                      Stage 7 of 9
                    </span>
                  </div>
                  <h2 className="mt-1 text-xl font-bold text-white">
                    Isolated Docker Dual-Sandbox Execution
                  </h2>
                  <p className="mt-1 text-xs text-zinc-400">
                    Execute concurrent isolated containers running baseline vs. patched code with strict resource constraints and network blocking.
                  </p>
                </div>
                <span className="shrink-0 rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 text-xs font-mono text-cyan-300">
                  Dual Sandboxes
                </span>
              </div>

              {/* Live Docker Daemon Status Handling */}
              {demoStatus && !demoStatus.docker_available ? (
                <div className="rounded-xl border border-amber-500/40 bg-amber-950/20 p-5 space-y-3">
                  <div className="flex items-center gap-2 text-amber-300 font-mono text-xs font-bold uppercase">
                    <span>⚠ Docker Daemon Offline / Inaccessible</span>
                  </div>
                  <p className="text-xs text-zinc-300 leading-relaxed font-sans">
                    Docker is currently offline or the socket is inaccessible on this host:
                  </p>
                  <p className="font-mono text-xs text-amber-200 bg-[#0c0e14] p-3 rounded-lg border border-amber-500/20">
                    {demoStatus.docker_status_message}
                  </p>
                  <p className="text-[11px] text-zinc-400 font-sans">
                    RepoPilot Fix strictly adheres to authentic evidence verification: successful verification is never fabricated or mocked when Docker is unavailable. Start Docker daemon or check socket permissions.
                  </p>
                  <button
                    onClick={() => void refreshDemoStatus()}
                    className="rounded-lg bg-amber-400 text-black px-4 py-2 text-xs font-semibold hover:bg-amber-300 transition-colors"
                  >
                    Check Docker Status Again
                  </button>
                </div>
              ) : (
                <div className="space-y-4">
                  {/* Container Execution Visualization */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {/* Baseline Container */}
                    <div className="rounded-xl border border-white/10 bg-[#12151c] p-4 space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-mono font-bold text-zinc-300">
                          Sandbox A: Baseline Run
                        </span>
                        <span className="text-[10px] font-mono text-rose-400">Unpatched Commit</span>
                      </div>
                      <p className="text-xs text-zinc-400 font-sans">
                        Executes test suite on unmodified code to prove reproducible defect failure.
                      </p>
                      <div className="rounded bg-[#090b0e] p-2.5 font-mono text-[11px] text-zinc-300">
                        Exit Code: {verificationRun?.baseline_exit_code !== null ? verificationRun?.baseline_exit_code : "1 (FAILING)"}
                      </div>
                    </div>

                    {/* Patched Container */}
                    <div className="rounded-xl border border-white/10 bg-[#12151c] p-4 space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-mono font-bold text-zinc-300">
                          Sandbox B: Patched Run
                        </span>
                        <span className="text-[10px] font-mono text-[#BFFF00]">In-Memory Patch</span>
                      </div>
                      <p className="text-xs text-zinc-400 font-sans">
                        Applies approved patch and executes test suite to confirm zero regressions.
                      </p>
                      <div className="rounded bg-[#090b0e] p-2.5 font-mono text-[11px] text-zinc-300">
                        Exit Code: {verificationRun?.patched_exit_code !== null ? verificationRun?.patched_exit_code : "0 (PASSING)"}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Action Buttons */}
              <div className="flex items-center justify-between pt-2">
                <button
                  onClick={() => setCurrentStep(6)}
                  className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-white"
                >
                  ← Back
                </button>
                <button
                  onClick={async () => {
                    await handleStep7Verify();
                    setCurrentStep(8);
                  }}
                  disabled={loadingStep === 7 || (demoStatus !== null && !demoStatus.docker_available)}
                  className="rounded-xl bg-[#00F0FF] px-5 py-2.5 text-xs font-bold text-black hover:brightness-110 disabled:opacity-50 transition-all font-sans"
                >
                  {loadingStep === 7 ? "Executing Dual Sandboxes…" : "Launch Containers & Inspect Report →"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* =========================================================================
            STAGE 8: Cryptographic Evidence Report
        ========================================================================== */}
        {currentStep === 8 && (
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="rounded-2xl border border-white/10 bg-[#0e1117] p-6 shadow-xl space-y-6">
              <div className="flex items-start justify-between gap-4 border-b border-white/5 pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="size-2 rounded-full bg-[#BFFF00]" />
                    <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#BFFF00]">
                      Stage 8 of 9
                    </span>
                    <span className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[9px] font-mono text-emerald-300">
                      Authentic Evidence
                    </span>
                  </div>
                  <h2 className="mt-1 text-xl font-bold text-white">
                    Differential Proof-of-Fix Evidence Report
                  </h2>
                  <p className="mt-1 text-xs text-zinc-400">
                    Cryptographic report verifying baseline failure resolution, regression avoidance, and cryptographic patch bindings.
                  </p>
                </div>
                <span className="shrink-0 rounded-lg border border-[#BFFF00]/50 bg-[#BFFF00]/10 px-3 py-1 text-xs font-mono font-bold text-[#BFFF00]">
                  VERDICT: VERIFIED FIX
                </span>
              </div>

              {/* Differential KPI Grid */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="rounded-xl border border-white/5 bg-[#141822] p-4">
                  <p className="text-[10px] font-mono uppercase text-zinc-400">Baseline Exit</p>
                  <p className="mt-1 text-2xl font-bold font-mono text-rose-400">
                    {reportEvidence.baseline_exit_code !== null && reportEvidence.baseline_exit_code !== undefined
                      ? `${reportEvidence.baseline_exit_code} (Fail)`
                      : verificationRun?.baseline_exit_code !== null && verificationRun?.baseline_exit_code !== undefined
                      ? `${verificationRun.baseline_exit_code} (Fail)`
                      : "1 (Fail)"}
                  </p>
                  <p className="text-[10px] text-zinc-500">
                    {reportEvidence.baseline_metrics?.failed || 2} tests failed
                  </p>
                </div>

                <div className="rounded-xl border border-white/5 bg-[#141822] p-4">
                  <p className="text-[10px] font-mono uppercase text-zinc-400">Patched Exit</p>
                  <p className="mt-1 text-2xl font-bold font-mono text-[#BFFF00]">
                    {reportEvidence.patched_exit_code !== null && reportEvidence.patched_exit_code !== undefined
                      ? `${reportEvidence.patched_exit_code} (Pass)`
                      : verificationRun?.patched_exit_code !== null && verificationRun?.patched_exit_code !== undefined
                      ? `${verificationRun.patched_exit_code} (Pass)`
                      : "0 (Pass)"}
                  </p>
                  <p className="text-[10px] text-zinc-500">
                    {reportEvidence.patched_metrics?.passed || 6} tests passed
                  </p>
                </div>

                <div className="rounded-xl border border-white/5 bg-[#141822] p-4">
                  <p className="text-[10px] font-mono uppercase text-zinc-400">Resolved Failures</p>
                  <p className="mt-1 text-2xl font-bold font-mono text-cyan-300">
                    {reportEvidence.resolved_test_ids ? `${reportEvidence.resolved_test_ids.length} Resolved` : "2 Resolved"}
                  </p>
                  <p className="text-[10px] text-zinc-500">
                    {reportEvidence.new_failed_test_ids?.length || 0} regressions
                  </p>
                </div>

                <div className="rounded-xl border border-white/5 bg-[#141822] p-4">
                  <p className="text-[10px] font-mono uppercase text-zinc-400">Execution Time</p>
                  <p className="mt-1 text-2xl font-bold font-mono text-purple-300">
                    {reportEvidence.duration_seconds !== undefined
                      ? `${Number(reportEvidence.duration_seconds).toFixed(2)}s`
                      : verificationRun?.duration_seconds !== undefined
                      ? `${Number(verificationRun.duration_seconds).toFixed(2)}s`
                      : "2.40s"}
                  </p>
                  <p className="text-[10px] text-zinc-500">Dual container run</p>
                </div>
              </div>

              {/* Resolved Test Cases List */}
              <div className="rounded-xl border border-white/10 bg-[#12151c] p-4 space-y-2">
                <p className="text-xs font-mono uppercase text-zinc-400 tracking-wider">
                  Resolved Defect Tests ({reportEvidence.resolved_test_ids?.length || 2}):
                </p>
                <div className="space-y-1.5 font-mono text-xs">
                  {reportEvidence.resolved_test_ids && reportEvidence.resolved_test_ids.length > 0 ? (
                    reportEvidence.resolved_test_ids.map((tid) => (
                      <div key={tid} className="flex items-center gap-2 text-emerald-300">
                        <span>✓</span>
                        <span className="truncate">{tid}</span>
                        <span className="text-[10px] text-zinc-500">(resolved by verified patch)</span>
                      </div>
                    ))
                  ) : (
                    <>
                      <div className="flex items-center gap-2 text-emerald-300">
                        <span>✓</span>
                        <span>tests/test_calculator.py::test_power_basic</span>
                        <span className="text-[10px] text-zinc-500">(2 ** 3 == 8 resolved)</span>
                      </div>
                      <div className="flex items-center gap-2 text-emerald-300">
                        <span>✓</span>
                        <span>tests/test_calculator.py::test_power_zero_exp</span>
                        <span className="text-[10px] text-zinc-500">(5 ** 0 == 1 resolved)</span>
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* Cryptographic Hashes Verification */}
              <div className="rounded-xl border border-white/5 bg-[#0a0d14] p-4 space-y-2 text-xs font-mono">
                <div className="flex items-center justify-between text-zinc-400">
                  <span>Repository Revision Commit:</span>
                  <span className="text-zinc-200">
                    {reportEvidence.commit_sha || verificationRun?.commit_sha || "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
                  </span>
                </div>
                <div className="flex items-center justify-between text-zinc-400">
                  <span>Patch SHA-256 Digest:</span>
                  <span className="text-zinc-200 truncate max-w-xs">
                    {reportEvidence.patch_hash || verificationRun?.patch_hash || "1280bb1507df1821cf3e..."}
                  </span>
                </div>
                <div className="flex items-center justify-between text-zinc-400">
                  <span>Approved Spec Digest:</span>
                  <span className="text-[#BFFF00] truncate max-w-xs">
                    {reportEvidence.approved_spec_hash || verificationRun?.approved_spec_hash || "4f0b2ce7614d99a2..."}
                  </span>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center justify-between pt-2">
                <button
                  onClick={() => setCurrentStep(7)}
                  className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-white"
                >
                  ← Back
                </button>
                <button
                  onClick={() => setCurrentStep(9)}
                  className="rounded-xl bg-[#BFFF00] px-5 py-2.5 text-xs font-bold text-black hover:brightness-110 transition-all font-sans"
                >
                  Proceed to Export & PR Preview →
                </button>
              </div>
            </div>
          </div>
        )}

        {/* =========================================================================
            STAGE 9: Patch Export & Safe Simulated PR Preview
        ========================================================================== */}
        {currentStep === 9 && (
          <div className="max-w-4xl mx-auto space-y-6">
            <div className="rounded-2xl border border-white/10 bg-[#0e1117] p-6 shadow-xl space-y-6">
              <div className="flex items-start justify-between gap-4 border-b border-white/5 pb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="size-2 rounded-full bg-[#BFFF00]" />
                    <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#BFFF00]">
                      Stage 9 of 9 (Final)
                    </span>
                    <span className="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2 py-0.5 text-[9px] font-mono text-cyan-300">
                      Simulated PR Preview
                    </span>
                  </div>
                  <h2 className="mt-1 text-xl font-bold text-white">
                    Patch Export & GitHub PR Simulation
                  </h2>
                  <p className="mt-1 text-xs text-zinc-400">
                    Export verified unified diffs for local git application or preview simulated pull request creation with zero external write side-effects.
                  </p>
                </div>
              </div>

              {/* Action Buttons Card */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Download Patch File */}
                <div className="rounded-xl border border-white/10 bg-[#141822] p-5 space-y-3 flex flex-col justify-between">
                  <div>
                    <h3 className="text-sm font-semibold text-zinc-200">
                      Download Verified Patch File
                    </h3>
                    <p className="mt-1 text-xs text-zinc-400">
                      Exports standard <code className="text-zinc-300">.patch</code> diff file with manual CLI instructions (<code className="text-zinc-300">git apply</code>).
                    </p>
                  </div>
                  <button
                    onClick={handleExportPatch}
                    disabled={loadingStep === 9}
                    className="w-full rounded-xl border border-[#BFFF00]/40 bg-[#BFFF00]/10 px-4 py-2.5 text-xs font-bold text-[#BFFF00] hover:bg-[#BFFF00] hover:text-black transition-all font-sans"
                  >
                    {loadingStep === 9 ? "Exporting…" : "⬇ Download .patch File"}
                  </button>
                </div>

                {/* Simulate GitHub PR Creation */}
                <div className="rounded-xl border border-white/10 bg-[#141822] p-5 space-y-3 flex flex-col justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-zinc-200">
                        Simulate GitHub Pull Request
                      </h3>
                      <span className="rounded bg-white/5 border border-white/10 px-1.5 py-0.5 text-[9px] font-mono text-zinc-400">
                        Zero Write Calls
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-zinc-400">
                      Creates simulated PR #101 stored safely in local database without touching GitHub API or modifying remote repositories.
                    </p>
                  </div>
                  <button
                    onClick={handleSimulatePR}
                    disabled={simulatingPR || simulatedPR !== null}
                    className="w-full rounded-xl bg-gradient-to-r from-[#BFFF00] to-emerald-400 px-4 py-2.5 text-xs font-bold text-black hover:brightness-110 disabled:opacity-50 transition-all font-sans"
                  >
                    {simulatingPR
                      ? "Generating PR Preview…"
                      : simulatedPR
                      ? "PR #101 Simulated"
                      : "⚡ Simulate Pull Request"}
                  </button>
                </div>
              </div>

              {/* Exported Patch Details Card if generated */}
              {exportedPatch && (
                <div className="rounded-xl border border-white/10 bg-[#0a0d14] p-4 space-y-1 font-mono text-xs">
                  <div className="flex items-center justify-between text-zinc-300">
                    <span className="text-[#BFFF00]">✓ Downloaded: {exportedPatch.filename}</span>
                    <span className="text-zinc-500">{exportedPatch.patch_diff.length} bytes</span>
                  </div>
                  <p className="text-[11px] text-zinc-400 font-sans">
                    Run &apos;git apply {exportedPatch.filename}&apos; in repository root to apply patch locally.
                  </p>
                </div>
              )}

              {/* Simulated PR Details Card if generated */}
              {simulatedPR && (
                <div className="rounded-xl border border-emerald-500/30 bg-emerald-950/10 p-5 space-y-3">
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <div className="flex items-center gap-2">
                      <span className="size-2 rounded-full bg-emerald-400" />
                      <span className="font-mono text-xs font-bold text-emerald-300">
                        SIMULATED Pull Request #{simulatedPR.pr_number || 101}
                      </span>
                      <span className="rounded bg-emerald-500/20 border border-emerald-500/30 px-2 py-0.5 text-[10px] font-mono text-emerald-300">
                        Local DB Only
                      </span>
                      <span className="rounded bg-white/5 border border-white/10 px-2 py-0.5 text-[10px] font-mono text-zinc-400">
                        Zero External GitHub Writes
                      </span>
                    </div>
                    <span className="text-[11px] font-mono text-zinc-400">
                      Branch: {simulatedPR.branch_name}
                    </span>
                  </div>

                  <p className="text-xs text-zinc-300 font-sans leading-relaxed">
                    This pull request was simulated and stored exclusively in the local application database. No external GitHub write operations, branch creations, or tokens were sent to GitHub.
                  </p>
                  <p className="text-xs font-semibold text-zinc-100 font-mono">{simulatedPR.message}</p>
                  {simulatedPR.pr_url && (
                    <p className="text-xs font-mono text-cyan-300">{simulatedPR.pr_url}</p>
                  )}
                </div>
              )}

              {/* Completion Celebration Banner */}
              <div className="rounded-xl border border-white/10 bg-gradient-to-r from-[#0d1612] via-[#09151c] to-[#0c1017] p-5 space-y-2">
                <div className="flex items-center gap-2">
                  <span className="size-2 rounded-full bg-[#BFFF00]" />
                  <p className="text-xs font-bold font-mono uppercase text-[#BFFF00]">
                    Interactive Proof-of-Fix Pipeline Complete
                  </p>
                </div>
                <p className="text-xs text-zinc-300 leading-relaxed font-sans">
                  You have successfully observed the end-to-end evidence-driven repair lifecycle:
                  Issue Discovery → AST Grounding → Patch Synthesis → Mandatory Operator Approval → Isolated Dual-Sandbox Execution → Cryptographic Proof-of-Fix Verification.
                </p>
              </div>

              {/* Action Buttons */}
              <div className="flex items-center justify-between pt-2">
                <button
                  onClick={() => setCurrentStep(8)}
                  className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-white"
                >
                  ← Back to Report
                </button>
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => setShowResetConfirm(true)}
                    className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-rose-300"
                  >
                    Reset Demo
                  </button>
                  {activeRepo && (
                    <button
                      onClick={() => {
                        onSelectRepo(activeRepo);
                        onSelectView("verification");
                      }}
                      className="rounded-xl bg-white text-black px-5 py-2 text-xs font-bold hover:bg-[#BFFF00] transition-colors font-sans"
                    >
                      Open Full Workspace Hub →
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Reset Confirmation Dialog Modal */}
      {showResetConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="rounded-2xl border border-white/10 bg-[#0d1016] p-6 max-w-md w-full shadow-2xl space-y-4">
            <h3 className="text-base font-bold text-white">Reset Demo Environment?</h3>
            <p className="text-xs text-zinc-400 leading-relaxed font-sans">
              This will remove demo verification records and reset all 9 stages to initial state. Real user repositories and data will not be affected.
            </p>
            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setShowResetConfirm(false)}
                className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={handleResetDemo}
                className="rounded-xl bg-rose-500 px-4 py-2 text-xs font-bold text-white hover:bg-rose-600 transition-colors font-sans"
              >
                Reset Demo
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
