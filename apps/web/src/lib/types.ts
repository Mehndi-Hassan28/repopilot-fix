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

export type IndexStatus = {
  status: string;
  file_count: number;
  symbol_count: number;
  languages: string[];
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
};

export type IssueAnalysisResponse = {
  repository_id: number;
  commit_sha: string;
  issue_number: number;
  issue_title: string;
  issue_body: string;
  issue_url: string;
  query: string;
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
  | "repos"
  | "chat"
  | "issues"
  | "patch"
  | "verification"
  | "activity"
  | "settings";

export type ToastMessage = {
  id: string;
  type: "success" | "error" | "info";
  message: string;
};
