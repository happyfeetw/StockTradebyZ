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

export interface LegacyImportDryRunReport {
  dry_run: boolean
  data_root: string
  totals: {
    files_seen: number
    files_valid: number
    records_seen: number
    records_valid: number
    warning_count: number
    quarantine_count: number
  }
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

export function dryRunLegacyImport(dataRoot: string): Promise<LegacyImportDryRunReport> {
  return request<LegacyImportDryRunReport>('/api/migrations/import-legacy', {
    method: 'POST',
    body: JSON.stringify({ dry_run: true, data_root: dataRoot }),
  })
}
