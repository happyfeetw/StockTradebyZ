const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelling' | 'cancelled'
export type RunKind = 'preselect' | 'review' | 'archive' | 'legacy_import' | 'backup' | 'restore' | 'diagnostic'

export interface ProductStack {
  frontend: string
  backend: string
  domain_language: string
  product_state_database: string
  analytical_database: string
}

export interface HealthResponse {
  status: string
  service: string
  version: string
  stack: ProductStack
  simulated_trading_in_scope: boolean
}

export interface RunSummary {
  id: string
  kind: RunKind
  status: RunStatus
  pick_date: string | null
  started_at: string | null
  finished_at: string | null
  summary: Record<string, unknown> | null
  created_at: string
}

export interface JobStep {
  id: number
  run_id: string
  name: string
  status: RunStatus
  started_at: string | null
  finished_at: string | null
  error: Record<string, unknown> | null
  created_at: string
}

export interface JobEvent {
  id: number
  run_id: string
  step_id: number | null
  level: string
  message: string
  created_at: string
}

export interface Artifact {
  id: string
  run_id: string
  kind: string
  path: string
  content_type: string | null
  metadata: Record<string, unknown> | null
  created_at: string
}

export interface RunDetail extends RunSummary {
  steps: JobStep[]
  events: JobEvent[]
  artifacts: Artifact[]
}

export interface RunListResponse {
  runs: RunSummary[]
}

export interface RunEventsResponse {
  events: JobEvent[]
}

export interface RunArtifactsResponse {
  artifacts: Artifact[]
}

export interface CandidateBatch {
  id: string
  run_id: string
  pick_date: string
  source: string
  strategy_counts: Record<string, unknown> | null
  created_at: string
}

export interface Candidate {
  id: number
  batch_id: string
  run_id: string
  pick_date: string
  code: string
  strategy: string
  close: number | null
  turnover_n: number | null
  brick_growth: number | null
  extra: Record<string, unknown> | null
  created_at: string
  batch: CandidateBatch
}

export interface CandidateListResponse {
  candidates: Candidate[]
  total: number
}

export interface CandidateDetailResponse {
  candidate: Candidate
}

export interface CandidateFilters {
  pick_date?: string
  run_id?: string
  strategy?: string
  code?: string
}

export type RecommendationStatus = 'all' | 'recommended' | 'reviewed'

export interface ReviewRun {
  id: string
  run_id: string
  candidate_batch_id: string | null
  pick_date: string
  provider: string
  status: string
  summary: Record<string, unknown> | null
  created_at: string
}

export interface Recommendation {
  id: number
  review_run_id: string
  review_id: number | null
  rank: number
  code: string
  strategy: string
  review_key: string
  verdict: string | null
  total_score: number | null
  payload: Record<string, unknown> | null
  created_at: string
}

export interface Review {
  id: number
  review_run_id: string
  run_id: string
  candidate_batch_id: string | null
  candidate_id: number | null
  pick_date: string
  code: string
  strategy: string
  review_key: string
  verdict: string | null
  total_score: number | null
  reviewer: string | null
  payload: Record<string, unknown> | null
  created_at: string
  review_run: ReviewRun
  recommendation: Recommendation | null
}

export interface ReviewListResponse {
  reviews: Review[]
  total: number
}

export interface ReviewDetailResponse {
  review: Review
}

export interface ReviewFilters {
  pick_date?: string
  run_id?: string
  review_run_id?: string
  candidate_batch_id?: string
  strategy?: string
  code?: string
  review_key?: string
  reviewer?: string
  recommendation_status?: RecommendationStatus
}

export type ArchiveStatus = 'all' | 'recommended' | 'reviewed' | 'unreviewed'

export interface ArchiveSnapshot {
  id: string
  pick_date: string
  run_id: string
  candidate_batch_id: string | null
  review_run_id: string | null
  candidate_run_date: string | null
  candidate_count: number
  reviewed_count: number
  recommended_count: number
  strategy_counts: Record<string, unknown> | null
  executed_strategies: string[] | null
  min_score_threshold: number | null
  source: Record<string, unknown> | null
  summary: Record<string, unknown> | null
  archived_at: string | null
  created_at: string
}

export interface ArchiveRow {
  id: number
  snapshot_id: string
  pick_date: string
  run_id: string
  candidate_batch_id: string | null
  review_run_id: string | null
  candidate_id: number | null
  review_id: number | null
  recommendation_id: number | null
  chart_artifact_id: string | null
  code: string
  strategy: string
  review_key: string
  status: Exclude<ArchiveStatus, 'all'>
  rank: number | null
  close: number | null
  turnover_n: number | null
  brick_growth: number | null
  extra: Record<string, unknown> | null
  review_payload: Record<string, unknown> | null
  chart: string | null
  created_at: string
  snapshot: ArchiveSnapshot
}

export interface ArchiveSnapshotListResponse {
  snapshots: ArchiveSnapshot[]
  total: number
}

export interface ArchiveDateResponse {
  snapshots: ArchiveSnapshot[]
  rows: ArchiveRow[]
  total: number
}

export interface ArchiveRowDetailResponse {
  row: ArchiveRow
}

export interface ArchiveSnapshotFilters {
  pick_date?: string
  run_id?: string
}

export interface ArchiveRowFilters extends ArchiveSnapshotFilters {
  strategy?: string
  code?: string
  review_key?: string
  status?: ArchiveStatus
  rank?: string
}

export interface LegacyImportIssue {
  section: string
  source_path: string
  reason: string
  message: string
  record_key: string | null
}

export interface LegacyImportSectionReport {
  files_seen: number
  files_valid: number
  records_seen: number
  records_valid: number
  by_kind: Record<string, number>
}

export interface LegacyImportTotals {
  files_seen: number
  files_valid: number
  records_seen: number
  records_valid: number
  warning_count: number
  quarantine_count: number
}

export interface LegacyImportSummary {
  run_id: string
  pick_date: string
  source_file: string
  strategy_counts: Record<string, number>
  batch_id: string | null
  review_run_id: string | null
  candidates_imported: number
  reviews_imported: number
  recommendations_imported: number
}

export interface LegacyImportDryRunReport {
  migration_id: string | null
  dry_run: boolean
  data_root: string
  sections: Record<string, LegacyImportSectionReport>
  totals: LegacyImportTotals
  warnings: LegacyImportIssue[]
  quarantine: LegacyImportIssue[]
  import_summary: LegacyImportSummary | null
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
    ...init,
  })

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const payload = (await response.json()) as { detail?: string }
      message = payload.detail || message
    } catch {
      // Keep the HTTP status text when the backend returns a non-JSON error.
    }
    throw new ApiError(response.status, message)
  }

  return response.json() as Promise<T>
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/api/health')
}

export function listRuns(): Promise<RunListResponse> {
  return request<RunListResponse>('/api/runs')
}

export function getRun(runId: string): Promise<RunDetail> {
  return request<RunDetail>(`/api/runs/${runId}`)
}

export function createDiagnosticRun(fail = false): Promise<RunDetail> {
  return request<RunDetail>('/api/runs/diagnostic', {
    method: 'POST',
    body: JSON.stringify({ fail }),
  })
}

export function cancelRun(runId: string): Promise<RunSummary> {
  return request<RunSummary>(`/api/runs/${runId}/cancel`, { method: 'POST' })
}

export function getRunEvents(runId: string): Promise<RunEventsResponse> {
  return request<RunEventsResponse>(`/api/jobs/${runId}/events`)
}

export function getRunArtifacts(runId: string): Promise<RunArtifactsResponse> {
  return request<RunArtifactsResponse>(`/api/runs/${runId}/artifacts`)
}

export function listCandidates(filters: CandidateFilters = {}): Promise<CandidateListResponse> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value?.trim()) {
      params.set(key, value.trim())
    }
  }
  const query = params.toString()
  return request<CandidateListResponse>(`/api/candidates${query ? `?${query}` : ''}`)
}

export function getCandidate(candidateId: number): Promise<CandidateDetailResponse> {
  return request<CandidateDetailResponse>(`/api/candidates/${candidateId}`)
}

export function listReviews(filters: ReviewFilters = {}): Promise<ReviewListResponse> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (!value?.trim()) continue
    if (key === 'recommendation_status' && value === 'all') continue
    params.set(key, value.trim())
  }
  const query = params.toString()
  return request<ReviewListResponse>(`/api/reviews${query ? `?${query}` : ''}`)
}

export function getReview(reviewId: number): Promise<ReviewDetailResponse> {
  return request<ReviewDetailResponse>(`/api/reviews/${reviewId}`)
}

export function listArchiveSnapshots(filters: ArchiveSnapshotFilters = {}): Promise<ArchiveSnapshotListResponse> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value?.trim()) {
      params.set(key, value.trim())
    }
  }
  const query = params.toString()
  return request<ArchiveSnapshotListResponse>(`/api/archive${query ? `?${query}` : ''}`)
}

export function listArchiveRows(pickDate: string, filters: ArchiveRowFilters = {}): Promise<ArchiveDateResponse> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (!value?.trim()) continue
    if (key === 'pick_date') continue
    if (key === 'status' && value === 'all') continue
    params.set(key, value.trim())
  }
  const query = params.toString()
  return request<ArchiveDateResponse>(`/api/archive/${pickDate}${query ? `?${query}` : ''}`)
}

export function getArchiveRow(rowId: number): Promise<ArchiveRowDetailResponse> {
  return request<ArchiveRowDetailResponse>(`/api/archive/rows/${rowId}`)
}

export function dryRunLegacyImport(dataRoot: string): Promise<LegacyImportDryRunReport> {
  return request<LegacyImportDryRunReport>('/api/migrations/import-legacy', {
    method: 'POST',
    body: JSON.stringify({ dry_run: true, data_root: dataRoot }),
  })
}

export function importLegacyCandidateBatch(dataRoot: string, pickDate: string): Promise<LegacyImportDryRunReport> {
  return request<LegacyImportDryRunReport>('/api/migrations/import-legacy', {
    method: 'POST',
    body: JSON.stringify({
      dry_run: false,
      data_root: dataRoot,
      scope: 'candidates',
      pick_date: pickDate,
    }),
  })
}

export function importLegacyReviewRun(dataRoot: string, pickDate: string): Promise<LegacyImportDryRunReport> {
  return request<LegacyImportDryRunReport>('/api/migrations/import-legacy', {
    method: 'POST',
    body: JSON.stringify({
      dry_run: false,
      data_root: dataRoot,
      scope: 'reviews',
      pick_date: pickDate,
    }),
  })
}
