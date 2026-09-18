// ---------- Research Radar API client ----------
// Requests fail explicitly so the UI never presents mock records as real user
// data. Types are re-exported from ../data/mock while the design data is split
// out incrementally.

import { useEffect, useState, useCallback, useRef } from 'react';
import {
  type Project,
  type Paper,
  type Claim,
  type ActionItem,
  type VersionRec,
  type AuditRec,
  type MatrixRow,
  type RetrievalReceipt,
  type CitationHealth,
} from './data/mock';

// Types defined inline in mock.ts but not exported — derive them from the
// parent interfaces so the rest of the codebase can reference them.
export type CompetitorEntry = Project['competitors'][number];
export type EvidenceItem = Claim['evidence'][number];

// ---------------------------------------------------------------------------
// Re-export types so callers can import from a single source
// ---------------------------------------------------------------------------
export type { Project, Paper, Claim, ActionItem, VersionRec, AuditRec, MatrixRow, RetrievalReceipt, CitationHealth };

// ---------------------------------------------------------------------------
// Sub-types used by the API layer
// ---------------------------------------------------------------------------
export interface CaseSummary {
  id: string;
  name: string;
  short: string;
  question: string;
  version: string;
  file: string;
  claimsConfirmed: number;
  claimsTotal: number;
  lastScan: string;
  urgent: number;
  topics: string[];
}

export interface SourceSummary {
  id: string;
  title: string;
  authors: string[];
  source_kind: string;
  venue: string | null;
  doi: string | null;
  arxiv_id: string | null;
  ccf_rank: string | null;
  fields_of_study: string[];
  tags?: string[];
  snapshot_count: number;
  created_at: string | null;
}

export interface RemoveSourcesResult {
  removed_source_count: number;
}

export interface SourceBulkTagsResult {
  updated_source_ids: string[];
}

export interface SourceDetail {
  id: string;
  title: string;
  authors: string[];
  source_kind: string;
  venue: string | null;
  doi: string | null;
  arxiv_id: string | null;
  arxiv_primary_category: string | null;
  fields_of_study: string[];
  ccf_rank: string | null;
  pdf_url: string | null;
  cited_by_count: number | null;
  integrity_state: string;
  created_at: string | null;
  snapshots: {
    id: string;
    version_label: string;
    title: string;
    abstract: string;
    content_hash: string;
    observed_at: string | null;
  }[];
  related_impact_ids: string[];
  related_claim_ids: string[];
  tags?: string[];
}

export interface IntegrityRadar {
  flagged_sources: Array<{
    source_id: string;
    title: string;
    doi: string | null;
    url: string;
    integrity_state: 'retracted' | 'expression_of_concern' | 'corrected' | string;
    flags: string[];
    related_claim_ids: string[];
    related_impact_ids: string[];
    snapshot_count: number;
  }>;
  counts: { retracted: number; expression_of_concern: number; corrected: number; normal: number };
  citation_health: CitationHealth;
  retraction_impacts: Array<{ id: string; title: string; state: string; source_title: string }>;
}

export interface ScanStatus {
  id: string;
  mode: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  progress: {
    value?: number;
    message?: string;
  };
  stats: Record<string, unknown>;
  error_message: string | null;
  estimated_cost?: number;
}

/** Evidence-fidelity measurement (P0): the trust-gate and NLI signals of
 * the latest completed scan, aggregated into one block. */
export interface FidelityStats {
  pairs_assessed: number;
  span_failed: number;
  trust_blocked: number;
  filtered_no_change: number;
  verified: number;
  evidence_pass_rate: number | null;
  judged: number;
  faithful: number;
  unfaithful: number;
  judge_failed: number;
  judge_skipped_no_llm: number;
  nli_checked: number;
  nli_disagreement: number;
  nli_neutral: number;
  nli_unavailable: number;
  faithfulness_rate: number | null;
}

export interface SettingsData {
  llm: {
    configured: boolean;
    mode: 'local' | 'remote' | null;
    model: string | null;
    missing: string[];
    provider: string;
    base_url: string;
    has_api_key: boolean;
    thinking: 'enabled' | 'disabled';
    reasoning_effort: 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
  };
  fallback_llm?: {
    configured: boolean;
    provider: string;
    model: string;
    base_url: string;
    has_api_key: boolean;
    primary_timeout_seconds: number;
  };
  embedding: {
    configured: boolean;
    model: string;
    provider: string;
    base_url: string;
    has_api_key: boolean;
  };
  local_llm: {
    model: string;
    base_url: string;
  };
  model_catalog: Record<string, { id: string; label: string }[]>;
  pdf_parser_backend: string;
}

export interface ReferenceEntry {
  raw: string;
  bibtex_key: string | null;
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  arxiv_id: string | null;
  format: string;
}

export interface ReferenceMatch {
  source_kind: string;
  external_id: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  arxiv_id: string | null;
  url: string;
  matched_fields: string[];
  confidence: number;
}

export interface ReferenceCheckResult {
  entry: ReferenceEntry;
  status: 'verified' | 'unverified';
  confidence: number;
  reasons: string[];
  matched: ReferenceMatch | null;
  queried_sources: string[];
}

export interface ReferenceCheckBatch {
  results: ReferenceCheckResult[];
  checked_at: string;
  summary: Record<string, number>;
}

export interface CitationSupportInput {
  reference: ReferenceMatch;
  citation_sentence: string;
  full_text?: string | null;
  abstract?: string | null;
}

export interface CitationSupportResult {
  reference: ReferenceMatch;
  citation_sentence: string;
  normalized_claim: string;
  category: 'supported' | 'partially_supported' | 'unsupported' | 'uncertain';
  confidence: number;
  evidence_quote: string;
  locator: string;
  full_text_status: 'full_text' | 'abstract_only' | 'unavailable';
  reasons: string[];
  recommended_actions: string[];
}

export interface CitationSupportBatch {
  results: CitationSupportResult[];
  checked_at: string;
  summary: Record<string, number>;
}

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Low-level fetch helpers
// ---------------------------------------------------------------------------

const BASE_URL = '/api';
const TOKEN_KEY = 'radar_auth_token';
const DEFAULT_TIMEOUT_MS = 30000;

export function getAuthToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setAuthToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearAuthToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export interface ApiErrorEnvelope {
  detail: string | string[];
  error_code?: string;
  retryable?: boolean;
  trace_id?: string;
}

function formatApiError(res: Response, text: string): string {
  let traceId = res.headers.get('X-Trace-Id') || '';
  let errorCode = '';

  if (text) {
    try {
      const parsed = JSON.parse(text) as {
        detail?: unknown;
        message?: unknown;
        error_code?: string;
        trace_id?: string;
      };

      if (parsed.trace_id) traceId = parsed.trace_id;
      if (parsed.error_code) errorCode = parsed.error_code;

      let msg = '';
      if (typeof parsed.detail === 'string' && parsed.detail.trim()) {
        msg = parsed.detail;
      } else if (Array.isArray(parsed.detail)) {
        const messages = parsed.detail.map((item: unknown) => {
          if (typeof item === 'string') return item;
          if (item && typeof item === 'object') {
            const record = item as { loc?: unknown[]; msg?: string; message?: string };
            const loc = Array.isArray(record.loc) ? record.loc.filter((k) => k !== 'body').join('.') : '';
            const m = record.msg || record.message || JSON.stringify(item);
            return loc ? `${loc}: ${m}` : m;
          }
          return String(item);
        });
        if (messages.length > 0) msg = messages.join('; ');
      } else if (typeof parsed.message === 'string' && parsed.message.trim()) {
        msg = parsed.message;
      }

      if (msg) {
        const extra = [errorCode, traceId ? `Trace ID: ${traceId}` : null].filter(Boolean).join(' · ');
        return extra ? `${msg} (${extra})` : msg;
      }
    } catch {
      // Non-JSON text body
      if (text.length < 200 && text.trim()) {
        return traceId ? `${text.trim()} (Trace ID: ${traceId})` : text.trim();
      }
    }
  }

  let fallbackMsg = `请求失败（HTTP ${res.status}）`;
  if (res.status === 404) fallbackMsg = '请求的资源不存在（HTTP 404）';
  else if (res.status === 403) fallbackMsg = '无权限执行此操作（HTTP 403）';
  else if (res.status === 500) fallbackMsg = '服务器内部错误，请稍后重试（HTTP 500）';
  else if (res.status === 502 || res.status === 503 || res.status === 504) {
    fallbackMsg = `服务暂时不可用或网关超时（HTTP ${res.status}）`;
  }

  const extra = [errorCode, traceId ? `Trace ID: ${traceId}` : null].filter(Boolean).join(' · ');
  return extra ? `${fallbackMsg} (${extra})` : fallbackMsg;
}

interface RequestOptions {
  timeoutMs?: number;
  responseType?: 'json' | 'text';
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, responseType = 'json' } = options;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  const opts: RequestInit = {
    method,
    headers: {},
    signal: controller.signal,
  };

  const token = getAuthToken();
  if (token) {
    (opts.headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
  }

  if (body instanceof FormData) {
    opts.body = body;
  } else if (body !== undefined) {
    (opts.headers as Record<string, string>)['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }

  try {
    const res = await fetch(`${BASE_URL}${path}`, opts);
    if (res.status === 401 && !path.startsWith('/auth/')) {
      clearAuthToken();
      window.dispatchEvent(new Event('radar:unauthorized'));
    }
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(formatApiError(res, text));
    }
    if (responseType === 'text') {
      return (await res.text()) as unknown as T;
    }
    return (await res.json()) as Promise<T>;
  } catch (err: unknown) {
    if (err instanceof Error && err.name === 'AbortError') {
      throw new Error(`请求超时（超过 ${Math.round(timeoutMs / 1000)} 秒），请检查网络连接或稍后重试`);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function get<T>(path: string, options?: RequestOptions): Promise<T> {
  return request<T>('GET', path, undefined, options);
}

async function post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
  return request<T>('POST', path, body, options);
}

async function put<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
  return request<T>('PUT', path, body, options);
}

async function del<T>(path: string, options?: RequestOptions): Promise<T> {
  return request<T>('DELETE', path, undefined, options);
}

// ---------------------------------------------------------------------------
// Typed API functions — one per FastAPI route
// ---------------------------------------------------------------------------

/** List all research cases (lightweight). */
export async function getCases(): Promise<CaseSummary[]> {
  return get<CaseSummary[]>('/cases');
}

export interface ClaimMatrixContract {
  task: string;
  dataset: string;
  split: string;
  metric: string;
  comparator: string;
  scope: string;
}

export interface ClaimMatrixCase {
  id: string;
  title: string;
  short: string;
  question: string;
  group_key?: string | null;
}

export interface ClaimMatrixRow {
  case_id: string;
  case_title: string;
  group_key?: string | null;
  claim_id: string;
  stable_key: string;
  statement: string;
  contract: ClaimMatrixContract;
  review_state: string;
  urgency?: 'critical' | 'review' | 'supported' | 'normal';
  impact_count?: number;
  challenge_count?: number;
}

export interface ClaimMatrixSummary {
  total_cases: number;
  total_claims: number;
  critical_claims: number;
  review_claims: number;
  clean_claims: number;
}

export interface ClaimMatrix {
  cases: ClaimMatrixCase[];
  rows: ClaimMatrixRow[];
  summary?: ClaimMatrixSummary;
}

export async function getClaimMatrix(): Promise<ClaimMatrix> {
  return get<ClaimMatrix>('/lab/claim-matrix');
}

export interface ComplianceReportResponse {
  case_id: string;
  case_title: string;
  generated_at: string;
  format: string;
  filename: string;
  summary: {
    total_references: number;
    verified_dois: number;
    retraction_risks: number;
    total_claims: number;
    high_risk_claims: number;
    compliance_score: number;
    compliance_grade: string;
  };
  report_markdown: string;
  report_html: string;
}

export async function getComplianceReport(
  caseId: string,
  format: 'markdown' | 'html' = 'markdown',
): Promise<ComplianceReportResponse> {
  return get<ComplianceReportResponse>(`/cases/${caseId}/compliance-report?format=${format}`);
}

export async function downloadComplianceReport(
  caseId: string,
  format: 'markdown' | 'html' = 'markdown',
): Promise<void> {
  const token = localStorage.getItem('radar_token') || localStorage.getItem('token') || '';
  const res = await fetch(`/api/cases/${caseId}/compliance-report/download?format=${format}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error('下载合规审计报告失败');
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `compliance-audit-report-${caseId}.${format === 'html' ? 'html' : 'md'}`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Metrics (internal product monitoring dashboard)
// ---------------------------------------------------------------------------

export interface MetricsOverview {
  generated_at: string;
  window_days: number;
  tenant_count: number;
  users: { total: number; new_in_window: number; active_in_window: number };
  cost: {
    total_usd: number;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    llm_calls: number;
    avg_usd_per_scan: number;
    by_stage: { stage: string; usd: number }[];
    by_model: { model: string; tokens: number }[];
  };
  problems: {
    failed_scans: number;
    top_errors: { reason: string; count: number }[];
    failure_events: { event_type: string; count: number }[];
  };
  health: {
    total_scans: number;
    scans_by_status: Record<string, number>;
    success_rate: number | null;
    avg_scan_seconds: number | null;
    avg_llm_latency_ms: number | null;
  };
}

export async function getMetricsOverview(windowDays = 30): Promise<MetricsOverview> {
  return get<MetricsOverview>(`/metrics/overview?window_days=${windowDays}`);
}

export interface AdminUser {
  id: string;
  username: string;
  role: 'admin' | 'user';
  created_at: string;
}

export async function getAdminUsers(): Promise<AdminUser[]> {
  return get<AdminUser[]>('/admin/users');
}

export async function setUserRole(userId: string, role: AdminUser['role']): Promise<AdminUser> {
  return put<AdminUser>(`/admin/users/${userId}/role`, { role });
}

export async function createAdminUser(username: string, password: string): Promise<AdminUser> {
  return post<AdminUser>('/admin/users', { username, password });
}

/** Get full project detail for one case. */
export async function getCaseDetail(caseId: string): Promise<Project> {
  return get<Project>(`/cases/${caseId}`);
}

export async function getRetrievalReceipts(caseId: string): Promise<RetrievalReceipt[]> {
  return get<RetrievalReceipt[]>(`/cases/${caseId}/retrieval-receipts`);
}

export async function getCitationHealth(caseId: string): Promise<CitationHealth> {
  return get<CitationHealth>(`/cases/${caseId}/citation-health`);
}

// ---------------------------------------------------------------------------
// Internal consistency
//
// Layer 1 (numeric) is deterministic and always runs. Layer 2 (semantic) loads
// an NLI model and calls the LLM, so it is requested explicitly — never on
// page load. Both layers return the same finding shape so the UI renders them
// without branching; `source` and `confidence` are what distinguish them.
// ---------------------------------------------------------------------------

export interface ConsistencyOccurrence {
  /** null for semantic findings, which are not about a number. */
  value: number | null;
  unit: string;
  section: string;
  start_offset: number;
  end_offset: number;
  quote: string;
  scope: string;
  source: 'text' | 'table' | 'semantic';
}

export interface ConsistencyFinding {
  kind: string;
  metric: string;
  severity: 'critical' | 'review';
  summary: string;
  review_state: string;
  /** Present on semantic findings only: the judge's self-reported certainty. */
  confidence?: number;
  occurrences: ConsistencyOccurrence[];
}

/** What layer 2 considered and what it threw away, so a reviewer can calibrate. */
export interface SemanticConsistencyStats {
  sentences: number;
  pairs_generated: number;
  pairs_scored: number;
  pairs_judged: number;
  rejected_unquoted: number;
  rejected_low_confidence: number;
  truncated: boolean;
  nli_available: boolean;
  status: string;
  detail?: string;
}

export interface ConsistencyReport {
  checks_run: string[];
  finding_count: number;
  findings: ConsistencyFinding[];
  manuscript: { file_name: string; version_no: number; chars: number };
  semantic?: SemanticConsistencyStats;
}

export async function getConsistency(
  caseId: string,
  options: { semantic?: boolean } = {},
): Promise<ConsistencyReport> {
  const query = options.semantic ? '?semantic=true' : '';
  return get<ConsistencyReport>(`/cases/${caseId}/consistency${query}`);
}

export async function checkReferences(
  caseId: string,
  body: { format?: 'auto' | 'bibtex' | 'text'; references?: string; entries?: ReferenceEntry[] },
): Promise<ReferenceCheckBatch> {
  return post<ReferenceCheckBatch>(`/cases/${caseId}/references/check`, body);
}

export async function checkReferencesFile(
  caseId: string,
  file: File,
): Promise<ReferenceCheckBatch> {
  const form = new FormData();
  form.append('file', file);
  return request<ReferenceCheckBatch>('POST', `/cases/${caseId}/references/check-file`, form);
}

export async function checkCitationSupport(
  caseId: string,
  body: {
    inputs?: CitationSupportInput[];
    reference?: ReferenceMatch;
    citation_sentence?: string;
    full_text?: string | null;
    abstract?: string | null;
  },
): Promise<CitationSupportBatch> {
  return post<CitationSupportBatch>(`/cases/${caseId}/citations/support-check`, body);
}

export async function getIntegrityRadar(caseId: string): Promise<IntegrityRadar> {
  return get<IntegrityRadar>(`/cases/${caseId}/integrity-radar`);
}

export async function downloadWritingBrief(caseId: string): Promise<string> {
  return get<string>(`/cases/${caseId}/writing-brief`, { responseType: 'text' });
}

/** Create a new case with manuscript upload (multipart). */
export async function createCase(
  title: string,
  researchQuestion: string,
  manuscript: File,
): Promise<CaseSummary> {
  const fd = new FormData();
  fd.append('title', title);
  fd.append('research_question', researchQuestion);
  fd.append('manuscript', manuscript);
  return post<CaseSummary>('/cases', fd);
}

/** Upload a new manuscript version for an existing case. */
export async function uploadManuscript(caseId: string, file: File): Promise<Record<string, unknown>> {
  const fd = new FormData();
  fd.append('manuscript', file);
  return post<Record<string, unknown>>(`/cases/${caseId}/upload`, fd);
}

/** Re-run the multi-stage claim extraction pipeline for a manuscript version. */
export async function reExtractClaims(
  caseId: string,
  manuscriptVersionId: string,
): Promise<{ claims_extracted: number; stages_completed: string[]; dropped_anchors: string[] }> {
  const fd = new FormData();
  fd.append('manuscript_version_id', manuscriptVersionId);
  return post(`/cases/${caseId}/claims/extract-pipeline`, fd);
}

/** Re-extract claims on the current manuscript version (LLM re-run over rule fallbacks). */
export async function reExtractCurrentClaims(caseId: string): Promise<{
  manuscript_version_id: string;
  claims: number;
  confirmed: number;
}> {
  return post(`/cases/${caseId}/claims/re-extract`);
}

/** Permanently delete a research case and all associated data. */
export async function deleteCase(caseId: string): Promise<{ deleted: string; title: string }> {
  return del<{ deleted: string; title: string }>(`/cases/${caseId}`);
}

// -- Claims ----------------------------------------------------------------

export async function getClaims(caseId: string): Promise<Claim[]> {
  return get<Claim[]>(`/cases/${caseId}/claims`);
}

export interface ClaimGraphNode {
  id: string;
  type: 'claim' | 'source';
  label: string;
  data: Record<string, unknown>;
}

export interface ClaimGraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  data: { relation: string };
}

export interface ClaimGraph {
  nodes: ClaimGraphNode[];
  edges: ClaimGraphEdge[];
  computed?: { node_count: number; edge_count: number; claim_count: number; source_count: number; truncated: boolean };
}

export async function getClaimGraph(caseId: string): Promise<ClaimGraph> {
  return get<ClaimGraph>(`/cases/${caseId}/claim-graph`);
}

export async function confirmClaim(caseId: string, revId: string): Promise<Claim> {
  return post<Claim>(`/cases/${caseId}/claims/${revId}/confirm`);
}

export async function rejectClaim(caseId: string, revId: string): Promise<Claim> {
  return post<Claim>(`/cases/${caseId}/claims/${revId}/reject`);
}

export async function editClaim(
  caseId: string,
  revId: string,
  body: { statement?: string; centrality?: string; contract?: Record<string, string>; falsifiable_condition?: string },
): Promise<Claim> {
  return put<Claim>(`/cases/${caseId}/claims/${revId}`, body);
}

export interface ClaimChangesResult {
  new: number;
  modified: number;
  deleted: number;
  unchanged: number;
  total_current: number;
  modified_details: Array<{
    claim_id: string;
    stable_key: string;
    change_type: string;
    change_ratio: number;
  }>;
  section_changes: Array<Record<string, unknown>>;
  error?: string;
}

export async function trackClaimChanges(
  caseId: string,
  previousVersionId: string,
  currentVersionId: string,
): Promise<ClaimChangesResult> {
  const fd = new FormData();
  fd.append('previous_version_id', previousVersionId);
  fd.append('current_version_id', currentVersionId);
  return post<ClaimChangesResult>(`/cases/${caseId}/claims/track-changes`, fd);
}

export async function splitClaim(
  caseId: string,
  revId: string,
  statements: string[],
): Promise<Claim[]> {
  return post<Claim[]>(`/cases/${caseId}/claims/${revId}/split`, { statements });
}

// -- Scans -----------------------------------------------------------------

export async function startScan(
  caseId: string,
  maxResults = 32,
  analysisLimit = 3,
): Promise<{ scan_id: string; status: string }> {
  return post<{ scan_id: string; status: string }>(`/cases/${caseId}/scans`, {
    max_results: maxResults,
    analysis_limit: analysisLimit,
  });
}

export async function listScans(caseId: string): Promise<ScanStatus[]> {
  return get<ScanStatus[]>(`/cases/${caseId}/scans`);
}

export async function getScanStatus(caseId: string, scanId: string): Promise<ScanStatus> {
  return get<ScanStatus>(`/cases/${caseId}/scans/${scanId}`);
}

export async function cancelScan(
  caseId: string,
  scanId: string,
): Promise<{ scan_id: string; cancelled: boolean }> {
  return del<{ scan_id: string; cancelled: boolean }>(`/cases/${caseId}/scans/${scanId}`);
}

// -- Deep research ----------------------------------------------------------

export interface DeepResearchStartResult {
  scan_id: string;
  status: string;
  mode: string;
}

export interface DeepResearchBrief {
  title: string;
  executive_summary: string;
  sections: {
    heading: string;
    findings: string[];
    source_ids: string[];
  }[];
  key_insights: string[];
  contradictions: string[];
  research_gaps: string[];
  recommended_next_steps: string[];
  sources: {
    source_id: string;
    title: string;
    authors: string[];
    year: string | null;
    venue: string | null;
    url: string;
    doi: string | null;
    relevance: string;
    key_findings: string[];
    limitations: string[];
    contradictions: string[];
  }[];
  warnings: string[];
}

export async function startDeepResearch(
  caseId: string,
  body: { question?: string; depth?: number; max_sources?: number; max_subqueries?: number } = {},
): Promise<DeepResearchStartResult> {
  return post<DeepResearchStartResult>(`/cases/${caseId}/deep-research`, body);
}

export interface LiteratureQASource {
  source_id: string;
  title: string;
  url: string;
  year: string | null;
  venue: string | null;
  evidence_snippets: string[];
  authors?: string[] | string | null;
  doi?: string | null;
  relevance?: number | string | null;
  key_findings?: string[] | string | null;
}

export interface LiteratureQAResult {
  answer: string;
  sources: LiteratureQASource[];
  uncertainty: string;
  warnings: string[];
}

export interface ChatHistoryItem {
  role: 'user' | 'assistant';
  content: string;
}

export async function askLiterature(
  caseId: string,
  question: string,
  topK = 5,
  history: ChatHistoryItem[] = [],
): Promise<LiteratureQAResult> {
  return post<LiteratureQAResult>(`/cases/${caseId}/literature-qa`, {
    question,
    top_k: topK,
    history,
  });
}

export interface LiteratureQAStreamEvents {
  onSources?: (sources: LiteratureQASource[]) => void;
  onToken?: (delta: string, accumulated: string) => void;
  onDone?: (result: LiteratureQAResult) => void;
  onError?: (error: Error) => void;
}

export async function askLiteratureStream(
  caseId: string,
  question: string,
  topK = 5,
  events: LiteratureQAStreamEvents = {},
  signal?: AbortSignal,
  history: ChatHistoryItem[] = [],
): Promise<LiteratureQAResult> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE_URL}/cases/${caseId}/literature-qa/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ question, top_k: topK, history }),
    signal,
  });

  if (res.status === 401) {
    clearAuthToken();
    window.dispatchEvent(new Event('radar:unauthorized'));
  }

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(formatApiError(res, text));
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error('当前浏览器环境不支持流式响应读取');
  }

  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let accumulatedText = '';
  let sources: LiteratureQASource[] = [];
  let uncertainty = '';
  const warnings: string[] = [];

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';

      let currentEvent = 'message';
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        if (trimmed.startsWith('event:')) {
          currentEvent = trimmed.slice(6).trim();
        } else if (trimmed.startsWith('data:')) {
          const dataStr = trimmed.slice(5).trim();
          try {
            const payload = JSON.parse(dataStr) as {
              sources?: LiteratureQASource[];
              delta?: string;
              answer?: string;
              uncertainty?: string;
              error?: string;
            };
            if (currentEvent === 'sources') {
              sources = Array.isArray(payload.sources) ? payload.sources : [];
              events.onSources?.(sources);
            } else if (currentEvent === 'token') {
              const delta = typeof payload.delta === 'string' ? payload.delta : '';
              accumulatedText += delta;
              events.onToken?.(delta, accumulatedText);
            } else if (currentEvent === 'done') {
              if (typeof payload.answer === 'string') accumulatedText = payload.answer;
              if (Array.isArray(payload.sources)) sources = payload.sources;
              if (typeof payload.uncertainty === 'string') uncertainty = payload.uncertainty;
            } else if (currentEvent === 'error') {
              const err = new Error(payload.error || '流式问答异常');
              events.onError?.(err);
              throw err;
            }
          } catch (parseErr) {
            if (currentEvent === 'error') throw parseErr;
          }
        }
      }
    }
  } catch (err) {
    if (signal?.aborted) {
      const partialResult: LiteratureQAResult = {
        answer: accumulatedText || '（生成已由用户中止）',
        sources,
        uncertainty,
        warnings: ['aborted_by_user'],
      };
      events.onDone?.(partialResult);
      return partialResult;
    }
    events.onError?.(err instanceof Error ? err : new Error(String(err)));
    throw err;
  }

  const finalResult: LiteratureQAResult = {
    answer: accumulatedText,
    sources,
    uncertainty,
    warnings,
  };
  events.onDone?.(finalResult);
  return finalResult;
}

export interface SourceImportResult {
  source_id: string;
  title: string;
  url: string;
  doi: string | null;
  arxiv_id: string | null;
  source_kind: string;
}

export interface GetSourcesParams {
  limit?: number;
  offset?: number;
  source_kind?: string;
  ccf_rank?: string;
  tag?: string;
}

export async function getSources(
  caseId: string,
  params: GetSourcesParams = {},
): Promise<SourceSummary[]> {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set('limit', String(params.limit));
  if (params.offset !== undefined) query.set('offset', String(params.offset));
  if (params.source_kind !== undefined) query.set('source_kind', params.source_kind);
  if (params.ccf_rank !== undefined) query.set('ccf_rank', params.ccf_rank);
  if (params.tag !== undefined) query.set('tag', params.tag);
  const queryString = query.toString();
  return get<SourceSummary[]>(`/cases/${caseId}/sources${queryString ? `?${queryString}` : ''}`);
}

export async function exportBibtex(
  caseId: string,
): Promise<{ filename: string; content: string }> {
  return get<{ filename: string; content: string }>(`/cases/${caseId}/sources/bibtex`);
}

export async function removeSources(
  caseId: string,
  sourceIds: string[],
): Promise<RemoveSourcesResult> {
  return post<RemoveSourcesResult>(`/cases/${caseId}/sources/remove`, {
    source_ids: sourceIds,
  });
}

export async function getSourceDetail(caseId: string, sourceId: string): Promise<SourceDetail> {
  return get<SourceDetail>(`/cases/${caseId}/sources/${sourceId}`);
}

export async function setSourceTags(
  caseId: string,
  sourceId: string,
  tags: string[],
): Promise<{ source_id: string; tags: string[] }> {
  return put<{ source_id: string; tags: string[] }>(`/cases/${caseId}/sources/${sourceId}/tags`, {
    tags,
  });
}

export async function setSourceBulkTags(
  caseId: string,
  sourceIds: string[],
  tags: string[],
): Promise<SourceBulkTagsResult> {
  return post<SourceBulkTagsResult>(`/cases/${caseId}/sources/tags`, {
    source_ids: sourceIds,
    tags,
  });
}

export async function importSource(
  caseId: string,
  body: { url?: string; doi?: string },
): Promise<SourceImportResult> {
  return post<SourceImportResult>(`/cases/${caseId}/sources/import`, body);
}

export interface NotificationHistoryItem {
  id: string;
  event_type: string;
  created_at: string | null;
  payload: Record<string, unknown>;
}

export async function getNotificationHistory(
  caseId: string,
  eventType?: string,
): Promise<NotificationHistoryItem[]> {
  const query = eventType ? `?event_type=${encodeURIComponent(eventType)}` : '';
  return get<NotificationHistoryItem[]>(`/cases/${caseId}/notification-history${query}`);
}

export interface RadarDigest {
  case_id: string;
  title: string;
  generated_at: string;
  summary: {
    scanned_papers: number;
    impacts: number;
    supports: number;
    challenges: number;
    open_actions: number;
  };
  sections: {
    heading: string;
    items: string[];
  }[];
  markdown: string;
}

export async function getDigest(
  caseId: string,
  includeSections?: string[],
): Promise<RadarDigest> {
  const query = includeSections && includeSections.length > 0
    ? `?include_sections=${encodeURIComponent(includeSections.join(','))}`
    : '';
  return get<RadarDigest>(`/cases/${caseId}/digest${query}`);
}

export interface AutoScanConfig {
  enabled: boolean;
  interval_hours: number;
  next_run_at: string | null;
  last_error?: string | null;
  last_error_at?: string | null;
}

export async function getAutoScan(caseId: string): Promise<AutoScanConfig> {
  return get<AutoScanConfig>(`/cases/${caseId}/auto-scan`);
}

export async function setAutoScan(
  caseId: string,
  config: { enabled: boolean; interval_hours?: number; next_run_at?: string | null },
): Promise<AutoScanConfig> {
  return put<AutoScanConfig>(`/cases/${caseId}/auto-scan`, config);
}

export interface ScanFilters {
  ccf_rank: string | null;
  arxiv_categories: string[];
}

export async function getScanFilters(caseId: string): Promise<ScanFilters> {
  return get<ScanFilters>(`/cases/${caseId}/scan-filters`);
}

export interface MonitoringStats {
  total_scans: number;
  status_counts: Record<string, number>;
  auto_scan_start_failures: number;
  last_scan_at: string | null;
  monthly_cost_usd?: number;
  cost_alert_exceeded?: boolean;
}

export async function getMonitoringStats(caseId: string): Promise<MonitoringStats> {
  return get<MonitoringStats>(`/cases/${caseId}/monitoring-stats`);
}

export interface CostAlertConfig {
  enabled: boolean;
  monthly_budget_usd: number;
  webhook_url?: string;
}

export interface EmailNotifyConfig {
  enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  username: string;
  password: string;
  recipient: string;
  subject_prefix: string;
}

export async function getEmailNotify(caseId: string): Promise<EmailNotifyConfig> {
  return get<EmailNotifyConfig>(`/cases/${caseId}/email-notify`);
}

export async function setEmailNotify(
  caseId: string,
  config: {
    enabled: boolean;
    smtp_host?: string;
    smtp_port?: number;
    username?: string;
    password?: string;
    recipient?: string;
    subject_prefix?: string;
  },
): Promise<EmailNotifyConfig> {
  return put<EmailNotifyConfig>(`/cases/${caseId}/email-notify`, config);
}

export async function testEmailNotify(caseId: string): Promise<{ sent: boolean; recipient: string }> {
  return post(`/cases/${caseId}/email-notify/test`);
}

export interface CostBySourceItem {
  source_kind: string;
  cost_usd: number;
  run_count: number;
}

export interface CostBySourceResult {
  items: CostBySourceItem[];
  total_cost_usd: number;
}

export interface DigestWebhookConfig {
  enabled: boolean;
  webhook_url: string;
  schedule: string;
}

export async function getDigestWebhook(caseId: string): Promise<DigestWebhookConfig> {
  return get<DigestWebhookConfig>(`/cases/${caseId}/digest-webhook`);
}

export async function setDigestWebhook(
  caseId: string,
  config: { enabled: boolean; webhook_url?: string; schedule?: string },
): Promise<DigestWebhookConfig> {
  return put<DigestWebhookConfig>(`/cases/${caseId}/digest-webhook`, config);
}

export async function testDigestWebhook(caseId: string): Promise<{ sent: boolean; webhook_url: string; title: string }> {
  return post(`/cases/${caseId}/digest-webhook/test`);
}

export interface ScanWebhookConfig {
  enabled: boolean;
  webhook_url: string;
  notify_on: string;
}

export async function getScanWebhook(caseId: string): Promise<ScanWebhookConfig> {
  return get<ScanWebhookConfig>(`/cases/${caseId}/scan-webhook`);
}

export async function setScanWebhook(
  caseId: string,
  config: { enabled: boolean; webhook_url?: string; notify_on?: string },
): Promise<ScanWebhookConfig> {
  return put<ScanWebhookConfig>(`/cases/${caseId}/scan-webhook`, config);
}

export async function testScanWebhook(caseId: string): Promise<{ sent: boolean; webhook_url: string }> {
  return post(`/cases/${caseId}/scan-webhook/test`);
}

export async function getCostAlert(caseId: string): Promise<CostAlertConfig> {
  return get<CostAlertConfig>(`/cases/${caseId}/cost-alert`);
}

export async function getCostBySource(caseId: string): Promise<CostBySourceResult> {
  return get<CostBySourceResult>(`/cases/${caseId}/cost-by-source`);
}

export async function setCostAlert(
  caseId: string,
  config: { enabled: boolean; monthly_budget_usd?: number; webhook_url?: string },
): Promise<CostAlertConfig> {
  return put<CostAlertConfig>(`/cases/${caseId}/cost-alert`, config);
}

export async function testCostAlert(caseId: string): Promise<{ sent: boolean; webhook_url: string }> {
  return post(`/cases/${caseId}/cost-alert/test`);
}

export async function setScanFilters(
  caseId: string,
  filters: { ccf_rank?: string | null; arxiv_categories?: string[] },
): Promise<ScanFilters> {
  return put<ScanFilters>(`/cases/${caseId}/scan-filters`, filters);
}

// -- Impacts (papers) ------------------------------------------------------

export async function getImpacts(caseId: string): Promise<Paper[]> {
  return get<Paper[]>(`/cases/${caseId}/impacts`);
}

export async function confirmImpact(caseId: string, impactId: string): Promise<Paper> {
  return post<Paper>(`/cases/${caseId}/impacts/${impactId}/confirm`);
}

export async function dismissImpact(caseId: string, impactId: string): Promise<Paper> {
  return post<Paper>(`/cases/${caseId}/impacts/${impactId}/dismiss`);
}

export async function editImpact(
  caseId: string,
  impactId: string,
  body: Record<string, string | null>,
): Promise<Paper> {
  return put<Paper>(`/cases/${caseId}/impacts/${impactId}`, body);
}

// -- Actions ---------------------------------------------------------------

export async function getActions(caseId: string): Promise<ActionItem[]> {
  return get<ActionItem[]>(`/cases/${caseId}/actions`);
}

export async function updateActionStatus(
  caseId: string,
  actionId: string,
  status: string,
): Promise<ActionItem> {
  return put<ActionItem>(`/cases/${caseId}/actions/${actionId}/status`, { status });
}

// -- Patches / rewrites ----------------------------------------------------

export async function generatePatch(
  caseId: string,
  impactId: string,
): Promise<{ patchId: string; claimId: string; loc: string; before: string; after: string; checks: { label: string; ok: boolean }[] }> {
  const fd = new FormData();
  fd.append('impact_id', impactId);
  return post<{ patchId: string; claimId: string; loc: string; before: string; after: string; checks: { label: string; ok: boolean }[] }>(
    `/cases/${caseId}/patches`,
    fd,
  );
}

export async function approvePatch(
  caseId: string,
  patchId: string,
): Promise<{ claimId: string; before: string; after: string; loc: string }> {
  return post<{ claimId: string; before: string; after: string; loc: string }>(
    `/cases/${caseId}/patches/${patchId}/approve`,
  );
}

export async function rejectPatch(
  caseId: string,
  patchId: string,
): Promise<{ claimId: string; before: string; after: string; loc: string }> {
  return post<{ claimId: string; before: string; after: string; loc: string }>(
    `/cases/${caseId}/patches/${patchId}/reject`,
  );
}

// -- Multi-location patches -------------------------------------------------

export interface MultiLocationEdit {
  section: string;
  edit_class: string;
  before_text: string;
  after_text: string;
  reason: string;
}

export interface MultiLocationPatchResult {
  impact_id: string;
  global_rationale: string;
  edits: MultiLocationEdit[];
  validated_count: number;
  total_suggested: number;
  citation_source_ids: string[];
}

export async function generateMultiLocationPatch(
  caseId: string,
  impactId: string,
): Promise<MultiLocationPatchResult> {
  const fd = new FormData();
  fd.append('impact_id', impactId);
  return post<MultiLocationPatchResult>(
    `/cases/${caseId}/patches/multi-location`,
    fd,
  );
}

export async function exportPatchDiff(
  caseId: string,
  patchId: string,
): Promise<{ patch_id: string; diff: string }> {
  return post<{ patch_id: string; diff: string }>(
    `/cases/${caseId}/patches/${patchId}/export-diff`,
  );
}

export interface GitPatchExport {
  filename: string;
  patch_content: string;
  apply_instructions: string;
  patch_count: number;
}

export async function exportGitPatch(caseId: string): Promise<GitPatchExport> {
  return get<GitPatchExport>(`/cases/${caseId}/patches/git-export`);
}

export interface UnifiedDiffExport {
  filename: string;
  diff: string;
  bibtex: string;
  patch_count: number;
}

export async function exportUnifiedDiff(caseId: string): Promise<UnifiedDiffExport> {
  return get<UnifiedDiffExport>(`/cases/${caseId}/patches/unified-diff`);
}

export interface ApplyLocalResult {
  success: boolean;
  applied_count: number;
  file_name: string;
  written_to_disk: boolean;
  bib_updated: boolean;
  added_keys: string[];
  message?: string;
}

export async function applyLocalPatches(caseId: string): Promise<ApplyLocalResult> {
  return post<ApplyLocalResult>(`/cases/${caseId}/patches/apply-local`);
}

export interface GitSyncConfig {
  remote_url: string;
  branch: string;
  enabled: boolean;
}

export async function getGitSync(caseId: string): Promise<GitSyncConfig> {
  return get<GitSyncConfig>(`/cases/${caseId}/git-sync`);
}

export async function setGitSync(caseId: string, config: GitSyncConfig): Promise<GitSyncConfig> {
  return put<GitSyncConfig>(`/cases/${caseId}/git-sync`, config);
}

// -- Skills -----------------------------------------------------------------

export interface SkillExecuteResult {
  skill: string;
  artifact_type: string;
  content: Record<string, unknown>;
  warnings: string[];
  handoff_suggestions: string[];
}

export async function executeSkill(
  caseId: string,
  skillName: string,
  actionType: string,
  impactIds: string[] = [],
): Promise<SkillExecuteResult> {
  const fd = new FormData();
  fd.append('skill_name', skillName);
  fd.append('action_type', actionType);
  fd.append('impact_ids', JSON.stringify(impactIds));
  return post<SkillExecuteResult>(
    `/cases/${caseId}/skills/execute`,
    fd,
  );
}

export interface SkillPipelineResult {
  pipeline: string;
  skills: string[];
  results: SkillExecuteResult[];
}

export async function runSkillPipeline(
  caseId: string,
  body: { pipeline?: string; skill_names?: string[]; impact_ids?: string[] } = {},
): Promise<SkillPipelineResult> {
  return post<SkillPipelineResult>(`/cases/${caseId}/skill-pipeline`, body);
}

export interface ArenaReviewDimension {
  name: string;
  score: number;
  comment: string;
}

export interface ArenaReviewRisk {
  risk_type: string;
  severity: string;
  description: string;
  recommendation: string;
}

export interface ArenaPersonaResult {
  id: string;
  label: string;
  description: string;
  review: {
    overall_score: number | null;
    dimensions: ArenaReviewDimension[];
    risks: ArenaReviewRisk[];
    action_priorities: string[];
    warnings?: string[];
  };
  rebuttal: {
    rebuttal_points: { reviewer_comment_summary: string; response: string; manuscript_change: string; new_evidence?: string }[];
    revision_plan: { action_id: string; change_description: string; section_affected: string; status: string }[];
    strength_assessment: string;
    warnings?: string[];
  };
}

export interface ArenaSimulationResult {
  case_id: string;
  personas: ArenaPersonaResult[];
  overall: { persona_count: number; average_score: number | null; warnings: string[] };
}

export async function simulateArena(
  caseId: string,
  body: { personas?: string[]; focus?: string } = {},
): Promise<ArenaSimulationResult> {
  return post<ArenaSimulationResult>(`/cases/${caseId}/arena/simulate`, body);
}

export interface SkillPipelineRun {
  id: string;
  event_type: string;
  created_at: string | null;
  payload: {
    pipeline?: string;
    skills?: string[];
  };
}

export async function getSkillPipelineRuns(caseId: string): Promise<SkillPipelineRun[]> {
  return get<SkillPipelineRun[]>(`/cases/${caseId}/skill-pipeline-runs`);
}

// -- Claim atomic decompose & semantic verify -------------------------------

export interface AtomicClaim {
  statement: string;
  source_quote: string;
  source_locator: string;
  dependencies: string[];
  verifiable_independently: boolean;
}

export async function decomposeClaim(
  caseId: string,
  revId: string,
): Promise<{ claim_revision_id: string; atomic_claims: AtomicClaim[] }> {
  return post<{ claim_revision_id: string; atomic_claims: AtomicClaim[] }>(
    `/cases/${caseId}/claims/${revId}/decompose`,
  );
}

export async function verifyClaimSemantics(
  caseId: string,
  revId: string,
): Promise<Record<string, unknown>> {
  return post<Record<string, unknown>>(
    `/cases/${caseId}/claims/${revId}/verify-semantics`,
  );
}

// -- Audit -----------------------------------------------------------------

export async function getAudit(caseId: string): Promise<AuditRec[]> {
  return get<AuditRec[]>(`/cases/${caseId}/audit`);
}

export interface SkillRun {
  id: string;
  stage: string;
  provider: string;
  model: string;
  created_at: string | null;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  output: Record<string, unknown>;
}

export async function getSkillRuns(caseId: string): Promise<SkillRun[]> {
  return get<SkillRun[]>(`/cases/${caseId}/skill-runs`);
}

// -- Profile ---------------------------------------------------------------

export async function getProfile(caseId: string): Promise<Project['profile']> {
  return get<Project['profile']>(`/cases/${caseId}/profile`);
}

export async function analyzeProfile(caseId: string): Promise<Project['profile']> {
  return post<Project['profile']>(`/cases/${caseId}/profile/analyze`);
}

// -- Competitors -----------------------------------------------------------

export async function getCompetitors(caseId: string): Promise<CompetitorEntry[]> {
  return get<CompetitorEntry[]>(`/cases/${caseId}/competitors`);
}

export async function addCompetitor(
  caseId: string,
  team: string,
  aliases: string[],
): Promise<{ id: string; team: string; aliases: string[] }> {
  return post<{ id: string; team: string; aliases: string[] }>(`/cases/${caseId}/competitors`, { team, aliases });
}

export async function removeCompetitor(
  caseId: string,
  watchId: string,
): Promise<{ deleted: string }> {
  return del<{ deleted: string }>(`/cases/${caseId}/competitors/${watchId}`);
}

// -- Settings --------------------------------------------------------------

export async function getSettings(): Promise<SettingsData> {
  return get<SettingsData>('/settings');
}

export async function putSettings(updates: Record<string, string>): Promise<{ saved: string[] }> {
  return put<{ saved: string[] }>('/settings', { updates });
}

export async function testSettings(): Promise<{
  ok: boolean;
  mode: 'local' | 'remote';
  provider: string;
  model: string;
  available_models: string[];
}> {
  return post<{
    ok: boolean;
    mode: 'local' | 'remote';
    provider: string;
    model: string;
    available_models: string[];
  }>('/settings/test');
}

// ---------------------------------------------------------------------------
// useApi hook — generic data fetcher with loading / error state
// ---------------------------------------------------------------------------

interface UseApiResult<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
  refetch: () => void;
}

export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = []): UseApiResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;

  const execute = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchRef
      .current()
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    const cancel = execute();
    return cancel;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading, error, refetch: execute };
}

// ---------------------------------------------------------------------------
// Auth (multi-user)
// ---------------------------------------------------------------------------

export interface AuthResult {
  token: string | null;
  username: string;
  id: string | null;
  role: 'admin' | 'user';
}

export async function registerAccount(username: string, password: string): Promise<AuthResult> {
  return post<AuthResult>('/auth/register', { username, password });
}

export async function loginAccount(username: string, password: string): Promise<AuthResult> {
  const result = await post<AuthResult>('/auth/login', { username, password });
  if (result.token) setAuthToken(result.token);
  return result;
}

export async function logoutAccount(): Promise<void> {
  try {
    await post('/auth/logout', {});
  } finally {
    clearAuthToken();
  }
}

export async function getMe(): Promise<{ username: string; id: string; role: 'admin' | 'user' }> {
  return get('/auth/me');
}
