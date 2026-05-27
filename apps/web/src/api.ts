const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export function artifactFileUrl(artifactId: string): string {
  return `${API_BASE}/api/artifacts/${encodeURIComponent(artifactId)}`
}

export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelling' | 'cancelled'
export type RunKind = 'preselect' | 'review' | 'archive' | 'chart_export' | 'legacy_import' | 'backup' | 'restore' | 'diagnostic'

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

export type StrategyPreferenceId = 'b1' | 'b2' | 'brick'

export interface ProductPreferenceSettings {
  timezone: string
  theme: 'system' | 'light' | 'dark'
  table_density: 'comfortable' | 'compact'
  default_strategy_ids: StrategyPreferenceId[]
  analytics_default_limit: number
  candidate_page_size: number
  review_page_size: number
  archive_page_size: number
  chart_export_enabled: boolean
  auto_archive_after_review: boolean
}

export interface ProductPreferenceState {
  source: 'defaults' | 'sqlite'
  updated_at: string | null
  preferences: ProductPreferenceSettings
}

export interface ConfigFileMetadata {
  key: string
  path: string
  exists: boolean
  sections: string[]
  writable: boolean
  exposed: boolean
}

export interface ExternalIntegrationStatus {
  key: string
  label: string
  configured: boolean
  source: string
  secret_exposed: boolean
}

export interface LocalStateSettings {
  sqlite_path: string
  duckdb_path: string | null
  artifact_root: string
  backup_root: string
}

export interface ProductSettingsResponse {
  service: string
  version: string
  stack: ProductStack
  simulated_trading_in_scope: boolean
  product_preferences: ProductPreferenceState
  local_state: LocalStateSettings
  config_files: ConfigFileMetadata[]
  external_integrations: ExternalIntegrationStatus[]
}

export interface ProductSettingsUpdateRequest {
  preferences: ProductPreferenceSettings
}

export interface StrategyConfigProvenance {
  path: string
  exists: boolean
  section: string
}

export interface StrategyDefinition {
  id: StrategyPreferenceId
  label: string
  description: string
  enabled_by_default: boolean
  candidate_identity: string[]
  parity_status: 'product_owned_with_legacy_adapter' | 'legacy_only' | 'not_applicable'
  config_provenance: StrategyConfigProvenance
  parameters: Record<string, unknown>
}

export interface StrategyMetadataResponse {
  config_path: string
  config_exists: boolean
  candidate_identity: string[]
  strategies: StrategyDefinition[]
}

export interface StrategySummaryRow {
  pick_date: string
  run_id: string
  strategy: string
  total: number
  reviewed: number
  recommended: number
  unreviewed: number
  reviewed_rate: number
  recommended_rate: number
}

export interface StrategySummaryTotals {
  total: number
  reviewed: number
  recommended: number
  unreviewed: number
  reviewed_rate: number
  recommended_rate: number
  strategies: string[]
  pick_dates: string[]
}

export interface StrategySummaryResponse {
  rows: StrategySummaryRow[]
  totals: StrategySummaryTotals
  filters: Record<string, string | null>
}

export interface StrategySummaryFilters {
  pick_date?: string
  run_id?: string
  strategy?: string
  limit?: string
}

export interface BackupManifest {
  backup_id: string
  run_id: string
  created_at: string
  backup_path: string
  product_version: string
  sources: Record<string, string | null>
  files: Record<string, string>
  missing_optional: string[]
}

export interface BackupCreateResponse {
  backup: BackupManifest
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

export interface PreselectRunRequest {
  config_path?: string
  data_dir?: string
  pick_date?: string
  end_date?: string
}

export interface PreselectCandidate {
  id: number | null
  batch_id: string | null
  code: string
  date: string
  strategy: string
  close: number | null
  turnover_n: number | null
  brick_growth: number | null
  extra: Record<string, unknown>
}

export interface PreselectCandidateBatch {
  id: string
  run_id: string
  pick_date: string
  source: string
  strategy_counts: Record<string, number>
  total: number
  created_at: string
  candidates: PreselectCandidate[]
}

export interface PreselectRunResponse {
  run: RunSummary
  batch: PreselectCandidateBatch
}

export interface CandidateBatch {
  id: string
  run_id: string
  pick_date: string
  source: string
  strategy_counts: Record<string, unknown> | null
  created_at: string
}

export interface CandidateBatchSummary extends CandidateBatch {
  candidate_count: number
  review_run_count: number
  latest_review_run_id: string | null
  latest_reviewed_count: number
  latest_recommended_count: number
  archive_snapshot_count: number
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

export interface CandidateBatchListResponse {
  batches: CandidateBatchSummary[]
  total: number
}

export interface CandidateBatchDetailResponse {
  batch: CandidateBatchSummary
  candidates: Candidate[]
  total: number
}

export interface CandidateFilters {
  batch_id?: string
  pick_date?: string
  run_id?: string
  strategy?: string
  code?: string
}

export interface CandidateBatchFilters {
  pick_date?: string
  run_id?: string
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

export interface ReviewProviderRunCreateRequest {
  candidate_batch_id: string
  provider: string
  reviewer?: string
  min_score?: number
  classic_pattern_config?: Record<string, unknown>
  codes?: string[]
  strategies?: string[]
  require_charts?: boolean
  provider_config?: Record<string, unknown>
}

export interface ReviewRunCreateResponse {
  run: RunSummary
  review_run: ReviewRun
  reviews: Review[]
  recommendations: Recommendation[]
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

export interface ArchiveRunCreateRequest {
  candidate_batch_id: string
  review_run_id: string
}

export interface ArchiveRunCreateResponse {
  run: RunSummary
  snapshot: ArchiveSnapshot
  rows: ArchiveRow[]
}

export interface ChartExportRunCreateRequest {
  candidate_batch_id: string
  raw_dir?: string
  bars?: number
  limit?: number
}

export interface ChartExportRunCreateResponse {
  run: RunSummary
  artifacts: Artifact[]
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
  strategy_counts: Record<string, unknown>
  batch_id: string | null
  review_run_id: string | null
  archive_snapshot_id: string | null
  pre_import_backup_id: string | null
  pre_import_backup_path: string | null
  candidates_imported: number
  reviews_imported: number
  recommendations_imported: number
  archive_rows_imported: number
  archive_reviewed_count: number
  archive_recommended_count: number
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

export type LegacyImportVerifyScope = 'candidates' | 'reviews' | 'history'

export interface LegacyImportVerifyCounts {
  legacy: number
  sqlite: number
  duckdb: number | null
}

export interface LegacyImportVerifyMismatches {
  missing_in_sqlite: string[]
  extra_in_sqlite: string[]
  missing_in_duckdb: string[]
  extra_in_duckdb: string[]
}

export interface LegacyImportVerifyReport {
  passed: boolean
  data_root: string
  scope: LegacyImportVerifyScope
  pick_date: string
  run_id: string | null
  source_path: string
  duckdb_checked: boolean
  counts: LegacyImportVerifyCounts
  mismatches: LegacyImportVerifyMismatches
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

export function getSettings(): Promise<ProductSettingsResponse> {
  return request<ProductSettingsResponse>('/api/settings')
}

export function putSettings(payload: ProductSettingsUpdateRequest): Promise<ProductSettingsResponse> {
  return request<ProductSettingsResponse>('/api/settings', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function getStrategies(): Promise<StrategyMetadataResponse> {
  return request<StrategyMetadataResponse>('/api/strategies')
}

export function getStrategySummary(filters: StrategySummaryFilters = {}): Promise<StrategySummaryResponse> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value?.trim()) {
      params.set(key, value.trim())
    }
  }
  const query = params.toString()
  return request<StrategySummaryResponse>(`/api/analytics/strategy-summary${query ? `?${query}` : ''}`)
}

export function createBackup(): Promise<BackupCreateResponse> {
  return request<BackupCreateResponse>('/api/backups', { method: 'POST' })
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

export function createPreselectRun(payload: PreselectRunRequest): Promise<PreselectRunResponse> {
  return request<PreselectRunResponse>('/api/runs/preselect', {
    method: 'POST',
    body: JSON.stringify(payload),
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

export function listCandidateBatches(filters: CandidateBatchFilters = {}): Promise<CandidateBatchListResponse> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value?.trim()) {
      params.set(key, value.trim())
    }
  }
  const query = params.toString()
  return request<CandidateBatchListResponse>(`/api/candidate-batches${query ? `?${query}` : ''}`)
}

export function getCandidateBatch(batchId: string): Promise<CandidateBatchDetailResponse> {
  return request<CandidateBatchDetailResponse>(`/api/candidate-batches/${encodeURIComponent(batchId)}`)
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

export function createReviewProviderRun(payload: ReviewProviderRunCreateRequest): Promise<ReviewRunCreateResponse> {
  return request<ReviewRunCreateResponse>('/api/runs/review/provider', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
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

export function createArchiveRun(payload: ArchiveRunCreateRequest): Promise<ArchiveRunCreateResponse> {
  return request<ArchiveRunCreateResponse>('/api/runs/archive', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function createChartExportRun(payload: ChartExportRunCreateRequest): Promise<ChartExportRunCreateResponse> {
  return request<ChartExportRunCreateResponse>('/api/runs/chart-export', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
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

export function importLegacyHistorySnapshot(dataRoot: string, pickDate: string): Promise<LegacyImportDryRunReport> {
  return request<LegacyImportDryRunReport>('/api/migrations/import-legacy', {
    method: 'POST',
    body: JSON.stringify({
      dry_run: false,
      data_root: dataRoot,
      scope: 'history',
      pick_date: pickDate,
    }),
  })
}

export function verifyLegacyImport(
  dataRoot: string,
  scope: LegacyImportVerifyScope,
  pickDate: string,
  runId?: string,
): Promise<LegacyImportVerifyReport> {
  return request<LegacyImportVerifyReport>('/api/migrations/verify-legacy', {
    method: 'POST',
    body: JSON.stringify({
      data_root: dataRoot,
      scope,
      pick_date: pickDate,
      run_id: runId ?? null,
    }),
  })
}
