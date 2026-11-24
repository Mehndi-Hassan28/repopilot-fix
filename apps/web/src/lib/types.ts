export type Repository = {
  id: number;
  owner: string;
  name: string;
  full_name: string;
  url: string;
  description: string | null;
  default_branch: string;
  stars: number;
};

export type IndexJobStatus =
  | "queued"
  | "pending"
  | "running"
  | "completed"
  | "ready"
  | "failed"
  | (string & {});

export type IndexStatus = {
  id?: number;
  status: IndexJobStatus;
  file_count: number;
  symbol_count?: number;
  languages?: string[];
  commit_sha: string;
  error?: string | null;
};

export type CodeSymbolItem = {
  kind: string;
  name: string;
  line: number;
};

export type IndexedFileItem = {
  path: string;
  language: string;
  size_bytes: number;
  symbols?: CodeSymbolItem[];
};

export type FileContentResponse = {
  path: string;
  language: string;
  size_bytes: number;
  content: string;
  symbols: CodeSymbolItem[];
};

export type OpenTab = {
  path: string;
  content?: string;
  symbols?: CodeSymbolItem[];
  language?: string;
  size_bytes?: number;
  highlightRange?: { start: number; end: number };
  loading?: boolean;
  error?: string;
};

export type Citation = {
  path: string;
  start_line: number;
  end_line: number;
};

export type ChatMessage = {
  id: number;
  role: string;
  content: string;
  citations: Citation[];
  mode?: string;
};

export type ChatSession = {
  id: number;
  repository_id: number;
  title: string;
  commit_sha: string;
  messages?: ChatMessage[];
  created_at?: string;
  updated_at?: string;
};

export type AuthUser = {
  id: number;
  login: string;
  avatar_url?: string;
};

export type FileCitation = {
  path: string;
  start_line: number;
  end_line: number;
  explanation?: string;
};

export type PatchProposal = {
  file_path: string;
  explanation: string;
  diff: string;
};

export type ProposedFix = {
  explanation: string;
  patches: PatchProposal[];
};

export type IssueAnalysis = {
  summary: string;
  root_cause_hypothesis: string;
  relevant_files: FileCitation[];
  proposed_fix: ProposedFix;
  confidence: "low" | "medium" | "high";
  confidence_reasoning?: string;
  limitations: string[];
  provider?: string;
  is_mock?: boolean;
};

export type IssueAnalysisResponse = {
  repository_id: number;
  commit_sha: string;
  issue_number: number;
  issue_title: string;
  issue_body: string;
  issue_url: string;
  query: string;
  provider?: string;
  is_mock?: boolean;
  evidence: Array<{
    path: string;
    start_line: number;
    end_line: number;
    content: string;
    score: number;
  }>;
  analysis: IssueAnalysis;
};

export type WorkspaceView =
  | "overview"
  | "demo"
  | "repos"
  | "chat"
  | "issues"
  | "patch"
  | "verification"
  | "activity"
  | "settings";

export type DemoIssue = {
  number: number;
  title: string;
  body: string;
  state: string;
  html_url: string;
  author: string;
  labels: string[];
};

export type DemoStatusResponse = {
  initialized: boolean;
  repository_id?: number | null;
  repository_name?: string | null;
  repository_full_name?: string | null;
  default_branch?: string;
  commit_sha?: string | null;
  docker_available: boolean;
  docker_status_message: string;
  sandbox_image: string;
  preconfigured_issue_number: number;
  has_active_run: boolean;
  latest_run_id?: number | null;
  latest_run_status?: string | null;
  latest_report_verdict?: string | null;
  approved_spec_hash?: string | null;
};

export type DemoInitializeResponse = {
  repository: Repository;
  indexing_job: {
    id: number;
    status: string;
    file_count: number;
    commit_sha: string;
  };
  issue: DemoIssue;
  test_command: string[];
  sandbox_image: string;
  docker_available: boolean;
  docker_status_message: string;
  is_mock_ai: boolean;
};

export type ToastMessage = {
  id: string;
  type: "success" | "error" | "info";
  message: string;
};
