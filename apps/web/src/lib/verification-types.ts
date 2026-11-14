export type VerificationJobStatus =
  | "draft"
  | "pending_approval"
  | "approved"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "timed_out"
  | "cancelled"
  | "rejected";

export type ProofVerdict =
  | "verified_fix"
  | "unreproduced"
  | "fix_failed"
  | "regression_detected"
  | "patch_error"
  | "timed_out"
  | "error"
  | "inconclusive";

export type TestMetrics = {
  total: number | null;
  passed: number | null;
  failed: number | null;
  errors: number | null;
  failed_test_ids: string[];
  failure_type: string | null;
  summary: string;
};

export type ProofEvidence = {
  repository_id: number;
  commit_sha: string;
  issue_number: number;
  patch_hash: string;
  sandbox_image: string;
  test_command: string[];
  baseline_exit_code: number | null;
  baseline_stdout: string;
  baseline_stderr: string;
  baseline_metrics: TestMetrics | null;
  baseline_failure_type: string | null;
  patched_exit_code: number | null;
  patched_stdout: string;
  patched_stderr: string;
  patched_metrics: TestMetrics | null;
  patched_failure_type: string | null;
  resolved_test_ids: string[];
  new_failed_test_ids: string[];
  unresolved_test_ids: string[];
  changed_files: string[];
  duration_seconds: number;
  uncertainty_reasons: string[];
};

export type ProofReportRead = {
  id: number;
  verification_run_id: number;
  verdict: ProofVerdict;
  hypothesis: string;
  summary: string;
  baseline_summary: string;
  patched_summary: string;
  confidence: "low" | "medium" | "high";
  evidence: ProofEvidence | Record<string, unknown>;
  created_at: string;
};

export type VerificationAuditEventRead = {
  id: number;
  verification_run_id: number;
  event_type: string;
  actor_user_id: number | null;
  from_status: string | null;
  to_status: string | null;
  details: Record<string, unknown>;
  created_at: string;
};

export type VerificationRunRead = {
  id: number;
  repository_id: number;
  commit_sha: string;
  issue_number: number;
  user_id: number | null;
  status: VerificationJobStatus;
  spec_hash: string | null;
  approved_spec_hash: string | null;
  approved_by_user_id: number | null;
  approved_at: string | null;
  rejection_reason: string | null;
  test_command: string[];
  sandbox_image: string;
  duration_seconds: number;
  baseline_exit_code: number | null;
  patched_exit_code: number | null;
  patch_hash: string | null;
  retry_count: number;
  max_retries: number;
  error_message: string | null;
  cancelled_at: string | null;
  cancelled_by_user_id: number | null;
  cancellation_reason: string | null;
  report: ProofReportRead | null;
  created_at: string;
  updated_at: string;
};

export type PatchProposal = {
  file_path: string;
  explanation?: string;
  diff: string;
};

export type VerificationCreateRequest = {
  commit_sha: string;
  issue_number: number;
  test_command: string[];
  patches: PatchProposal[];
  hypothesis?: string;
  sandbox_image?: string;
  timeout_seconds?: number;
  auto_submit_approval?: boolean;
  idempotency_key?: string;
};

export type VerificationApprovalRequest = {
  expected_spec_hash?: string;
  auto_queue?: boolean;
  comment?: string;
};

export type VerificationRejectionRequest = {
  reason?: string;
};

export type VerificationCancelRequest = {
  reason?: string;
};
