import { useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  Archive,
  Ban,
  CheckCircle2,
  Clock3,
  Database,
  FileSearch,
  Gauge,
  GitBranch,
  Loader2,
  Play,
  RefreshCw,
  Search,
  Server,
  ShieldAlert,
  XCircle,
} from 'lucide-react'
import './App.css'
import {
  cancelRun,
  createDiagnosticRun,
  getHealth,
  getRun,
  getRunArtifacts,
  getRunEvents,
  listRuns,
  type Artifact,
  type Candidate,
  type CandidateFilters,
  type JobEvent,
  type RunStatus,
  type RunSummary,
  getCandidate,
  listCandidates,
} from './api'

const navItems = [
  { to: '/runs', label: 'Runs', icon: Activity, state: 'active' },
  { to: '/candidates', label: 'Candidates', icon: Search, state: 'active' },
  { to: '/reviews', label: 'Reviews', icon: FileSearch, state: 'pending' },
  { to: '/archive', label: 'Archive', icon: Archive, state: 'pending' },
  { to: '/migrations', label: 'Migrations', icon: Database, state: 'pending' },
]

const statusLabels: Record<RunStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  cancelling: 'Cancelling',
  cancelled: 'Cancelled',
}

function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="Product navigation">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <Gauge size={22} />
          </div>
          <div>
            <p className="brand-name">StockTradebyZ</p>
            <p className="brand-meta">Product refactor</p>
          </div>
        </div>

        <nav className="primary-nav">
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}>
              <item.icon size={18} aria-hidden="true" />
              <span>{item.label}</span>
              {item.state === 'pending' ? <span className="nav-pill">Soon</span> : null}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="main-surface">
        <Routes>
          <Route path="/" element={<Navigate to="/runs" replace />} />
          <Route path="/runs" element={<RunsView />} />
          <Route path="/candidates" element={<CandidatesView />} />
          <Route path="/reviews" element={<Placeholder title="Reviews" />} />
          <Route path="/archive" element={<Placeholder title="Archive" />} />
          <Route path="/migrations" element={<Placeholder title="Migrations" />} />
        </Routes>
      </main>
    </div>
  )
}

function CandidatesView() {
  const queryClient = useQueryClient()
  const [filters, setFilters] = useState<Required<CandidateFilters>>({
    pick_date: '',
    run_id: '',
    strategy: '',
    code: '',
  })
  const [selectedCandidateId, setSelectedCandidateId] = useState<number | null>(null)

  const normalizedFilters = {
    pick_date: filters.pick_date.trim(),
    run_id: filters.run_id.trim(),
    strategy: filters.strategy.trim(),
    code: filters.code.trim(),
  }

  const candidatesQuery = useQuery({
    queryKey: ['candidates', normalizedFilters],
    queryFn: () => listCandidates(normalizedFilters),
  })
  const candidates = candidatesQuery.data?.candidates ?? []
  const selectedStillVisible = selectedCandidateId !== null && candidates.some((candidate) => candidate.id === selectedCandidateId)
  const activeCandidateId = selectedStillVisible ? selectedCandidateId : candidates[0]?.id

  const detailQuery = useQuery({
    queryKey: ['candidate', activeCandidateId],
    queryFn: () => getCandidate(activeCandidateId as number),
    enabled: typeof activeCandidateId === 'number',
  })

  const hasFilters = Object.values(filters).some((value) => value.trim())

  function updateFilter(key: keyof CandidateFilters, value: string) {
    setFilters((current) => ({ ...current, [key]: value }))
  }

  function resetFilters() {
    setFilters({ pick_date: '', run_id: '', strategy: '', code: '' })
    setSelectedCandidateId(null)
  }

  return (
    <div className="run-center">
      <header className="topbar">
        <div>
          <p className="eyebrow">Selection evidence</p>
          <h1>Candidates</h1>
        </div>
        <button
          type="button"
          className="icon-button secondary"
          aria-label="Refresh candidates"
          onClick={() => queryClient.invalidateQueries({ queryKey: ['candidates'] })}
        >
          <RefreshCw size={17} aria-hidden="true" />
        </button>
      </header>

      <form className="filter-bar" onSubmit={(event) => event.preventDefault()} aria-label="Candidate filters">
        <FilterInput
          label="Pick date"
          placeholder="2026-05-27"
          value={filters.pick_date}
          onChange={(value) => updateFilter('pick_date', value)}
        />
        <FilterInput label="Run id" placeholder="run id" value={filters.run_id} onChange={(value) => updateFilter('run_id', value)} />
        <FilterInput
          label="Strategy"
          placeholder="b2 / brick"
          value={filters.strategy}
          onChange={(value) => updateFilter('strategy', value)}
        />
        <FilterInput label="Code" placeholder="000001" value={filters.code} onChange={(value) => updateFilter('code', value)} />
        <button type="button" className="action-button secondary filter-reset" onClick={resetFilters} disabled={!hasFilters}>
          Clear
        </button>
      </form>

      {candidatesQuery.isError ? (
        <div className="alert" role="alert">
          <ShieldAlert size={18} aria-hidden="true" />
          <span>{errorText(candidatesQuery.error)}</span>
        </div>
      ) : null}

      <div className="candidate-grid">
        <section className="candidate-list-panel" aria-label="Candidate list">
          <div className="panel-heading">
            <div>
              <h2>Candidate rows</h2>
              <p>{candidatesQuery.isLoading ? 'Loading candidate rows' : `${candidatesQuery.data?.total ?? 0} records`}</p>
            </div>
          </div>

          {candidatesQuery.isLoading ? <RunSkeleton /> : null}
          {!candidatesQuery.isLoading && candidates.length === 0 ? (
            <div className="empty-state">
              <Search size={24} aria-hidden="true" />
              <h3>{hasFilters ? 'No candidates match the filters' : 'No candidate rows yet'}</h3>
            </div>
          ) : null}

          <div className="candidate-list">
            {candidates.map((candidate) => (
              <button
                key={candidate.id}
                type="button"
                className={candidate.id === activeCandidateId ? 'candidate-row selected' : 'candidate-row'}
                onClick={() => setSelectedCandidateId(candidate.id)}
              >
                <span className="candidate-code">{candidate.code}</span>
                <span className="strategy-chip">{candidate.strategy}</span>
                <span className="candidate-meta">{candidate.pick_date}</span>
                <span className="candidate-price">{formatNumber(candidate.close)}</span>
                <span className="candidate-lineage">{candidate.batch_id}</span>
              </button>
            ))}
          </div>
        </section>

        <section className="detail-panel" aria-label="Candidate detail">
          {detailQuery.isLoading && activeCandidateId ? <RunDetailSkeleton /> : null}
          {!activeCandidateId && !candidatesQuery.isLoading ? (
            <div className="empty-state detail-empty">
              <Database size={24} aria-hidden="true" />
              <h3>Select a candidate</h3>
            </div>
          ) : null}
          {detailQuery.data ? <CandidateDetailPanel candidate={detailQuery.data.candidate} /> : null}
        </section>
      </div>
    </div>
  )
}

function RunsView() {
  const queryClient = useQueryClient()
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)

  const healthQuery = useQuery({ queryKey: ['health'], queryFn: getHealth, refetchInterval: 15_000 })
  const runsQuery = useQuery({ queryKey: ['runs'], queryFn: listRuns, refetchInterval: 10_000 })
  const runs = runsQuery.data?.runs ?? []
  const activeRunId = selectedRunId ?? runs[0]?.id ?? ''

  const detailQuery = useQuery({
    queryKey: ['run', activeRunId],
    queryFn: () => getRun(activeRunId),
    enabled: Boolean(activeRunId),
  })
  const eventsQuery = useQuery({
    queryKey: ['run-events', activeRunId],
    queryFn: () => getRunEvents(activeRunId),
    enabled: Boolean(activeRunId),
    refetchInterval: activeRunId ? 8_000 : false,
  })
  const artifactsQuery = useQuery({
    queryKey: ['run-artifacts', activeRunId],
    queryFn: () => getRunArtifacts(activeRunId),
    enabled: Boolean(activeRunId),
  })

  const createMutation = useMutation({
    mutationFn: () => createDiagnosticRun(false),
    onSuccess: (run) => {
      setSelectedRunId(run.id)
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      queryClient.setQueryData(['run', run.id], run)
    },
  })

  const cancelMutation = useMutation({
    mutationFn: (runId: string) => cancelRun(runId),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      queryClient.invalidateQueries({ queryKey: ['run', run.id] })
      queryClient.invalidateQueries({ queryKey: ['run-events', run.id] })
    },
  })

  const healthState = healthQuery.isLoading ? 'checking' : healthQuery.isError ? 'offline' : 'online'

  return (
    <div className="run-center">
      <header className="topbar">
        <div>
          <p className="eyebrow">Workflow control</p>
          <h1>Run Center</h1>
        </div>
        <div className={`health-chip ${healthState}`}>
          <Server size={17} aria-hidden="true" />
          <span>{healthState === 'online' ? 'API online' : healthState === 'offline' ? 'API offline' : 'Checking API'}</span>
        </div>
      </header>

      <section className="summary-strip" aria-label="Runtime summary">
        <Metric label="Runs" value={runs.length.toString()} />
        <Metric label="Active" value={runs.filter((run) => run.status === 'queued' || run.status === 'running').length.toString()} />
        <Metric label="Backend" value={healthQuery.data?.stack.backend ?? 'FastAPI'} />
        <Metric label="Storage" value={healthQuery.data?.stack.product_state_database ?? 'SQLite'} />
      </section>

      {healthQuery.isError || runsQuery.isError ? (
        <div className="alert" role="alert">
          <ShieldAlert size={18} aria-hidden="true" />
          <span>{errorText(healthQuery.error ?? runsQuery.error)}</span>
        </div>
      ) : null}

      <div className="workspace-grid">
        <section className="run-list-panel" aria-label="Runs">
          <div className="panel-heading">
            <div>
              <h2>Runs</h2>
              <p>{runsQuery.isLoading ? 'Loading runtime state' : `${runs.length} records`}</p>
            </div>
            <div className="button-row">
              <button
                type="button"
                className="icon-button secondary"
                aria-label="Refresh runs"
                onClick={() => queryClient.invalidateQueries({ queryKey: ['runs'] })}
              >
                <RefreshCw size={17} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="action-button"
                onClick={() => createMutation.mutate()}
                disabled={createMutation.isPending}
              >
                {createMutation.isPending ? <Loader2 className="spin" size={17} aria-hidden="true" /> : <Play size={17} aria-hidden="true" />}
                <span>Diagnostic</span>
              </button>
            </div>
          </div>

          {runsQuery.isLoading ? <RunSkeleton /> : null}
          {!runsQuery.isLoading && runs.length === 0 ? (
            <div className="empty-state">
              <Activity size={24} aria-hidden="true" />
              <h3>No product runs yet</h3>
              <button type="button" className="action-button" onClick={() => createMutation.mutate()} disabled={createMutation.isPending}>
                <Play size={17} aria-hidden="true" />
                <span>Diagnostic</span>
              </button>
            </div>
          ) : null}

          <div className="run-list">
            {runs.map((run) => (
              <button
                key={run.id}
                type="button"
                className={run.id === activeRunId ? 'run-row selected' : 'run-row'}
                onClick={() => setSelectedRunId(run.id)}
              >
                <span className={`status-dot ${run.status}`} aria-hidden="true" />
                <span className="run-row-main">
                  <span className="run-kind">{run.kind}</span>
                  <span className="run-id">{run.id}</span>
                </span>
                <StatusBadge status={run.status} />
              </button>
            ))}
          </div>
        </section>

        <section className="detail-panel" aria-label="Run detail">
          {detailQuery.isLoading && activeRunId ? <RunDetailSkeleton /> : null}
          {!activeRunId && !runsQuery.isLoading ? (
            <div className="empty-state detail-empty">
              <Clock3 size={24} aria-hidden="true" />
              <h3>Select a run</h3>
            </div>
          ) : null}
          {detailQuery.data ? (
            <RunDetailPanel
              run={detailQuery.data}
              events={eventsQuery.data?.events ?? detailQuery.data.events}
              artifacts={artifactsQuery.data?.artifacts ?? detailQuery.data.artifacts}
              onCancel={() => cancelMutation.mutate(detailQuery.data.id)}
              cancelling={cancelMutation.isPending}
            />
          ) : null}
        </section>
      </div>
    </div>
  )
}

function RunDetailPanel({
  run,
  events,
  artifacts,
  onCancel,
  cancelling,
}: {
  run: RunSummary & { steps?: { id: number; name: string; status: RunStatus; error: Record<string, unknown> | null }[] }
  events: JobEvent[]
  artifacts: Artifact[]
  onCancel: () => void
  cancelling: boolean
}) {
  const canCancel = run.status === 'queued' || run.status === 'running'

  return (
    <div className="detail-content">
      <div className="detail-header">
        <div>
          <p className="eyebrow">Selected run</p>
          <h2>{run.kind}</h2>
          <p className="muted run-id-wrap">{run.id}</p>
        </div>
        <div className="button-row">
          <StatusBadge status={run.status} />
          <button type="button" className="action-button danger" onClick={onCancel} disabled={!canCancel || cancelling}>
            {cancelling ? <Loader2 className="spin" size={17} aria-hidden="true" /> : <Ban size={17} aria-hidden="true" />}
            <span>Cancel</span>
          </button>
        </div>
      </div>

      <div className="detail-meta">
        <Metric label="Created" value={formatDateTime(run.created_at)} />
        <Metric label="Started" value={formatDateTime(run.started_at)} />
        <Metric label="Finished" value={formatDateTime(run.finished_at)} />
      </div>

      <section className="subsection">
        <h3>Steps</h3>
        <div className="step-list">
          {(run.steps ?? []).length === 0 ? <p className="muted">No steps recorded.</p> : null}
          {(run.steps ?? []).map((step) => (
            <div className="step-row" key={step.id}>
              <GitBranch size={16} aria-hidden="true" />
              <span>{step.name}</span>
              <StatusBadge status={step.status} />
            </div>
          ))}
        </div>
      </section>

      <section className="subsection">
        <h3>Events</h3>
        <div className="event-list">
          {events.length === 0 ? <p className="muted">No events recorded.</p> : null}
          {events.map((event) => (
            <div className="event-row" key={event.id}>
              <span className={`event-level ${event.level}`}>{event.level}</span>
              <span>{event.message}</span>
              <time>{formatDateTime(event.created_at)}</time>
            </div>
          ))}
        </div>
      </section>

      <section className="subsection">
        <h3>Artifacts</h3>
        {artifacts.length === 0 ? <p className="muted">No artifacts linked.</p> : null}
        {artifacts.map((artifact) => (
          <div className="artifact-row" key={artifact.id}>
            <Database size={16} aria-hidden="true" />
            <span>{artifact.kind}</span>
            <code>{artifact.path}</code>
          </div>
        ))}
      </section>
    </div>
  )
}

function CandidateDetailPanel({ candidate }: { candidate: Candidate }) {
  return (
    <div className="detail-content">
      <div className="detail-header">
        <div>
          <p className="eyebrow">Selected candidate</p>
          <h2>{candidate.code}</h2>
          <p className="muted run-id-wrap">{candidate.batch_id}</p>
        </div>
        <span className="strategy-chip large">{candidate.strategy}</span>
      </div>

      <div className="detail-meta candidate-metrics">
        <Metric label="Pick date" value={candidate.pick_date} />
        <Metric label="Close" value={formatNumber(candidate.close)} />
        <Metric label="Turnover" value={formatNumber(candidate.turnover_n)} />
        <Metric label="Brick growth" value={formatNumber(candidate.brick_growth)} />
      </div>

      <section className="subsection">
        <h3>Lineage</h3>
        <div className="lineage-grid">
          <DataPair label="Candidate id" value={candidate.id.toString()} />
          <DataPair label="Batch id" value={candidate.batch_id} />
          <DataPair label="Run id" value={candidate.run_id} />
          <DataPair label="Source" value={candidate.batch.source} />
          <DataPair label="Batch date" value={candidate.batch.pick_date} />
          <DataPair label="Created" value={formatDateTime(candidate.created_at)} />
        </div>
      </section>

      <section className="subsection">
        <h3>Strategy counts</h3>
        <pre className="json-block">{jsonPreview(candidate.batch.strategy_counts)}</pre>
      </section>

      <section className="subsection">
        <h3>Extra</h3>
        <pre className="json-block">{jsonPreview(candidate.extra)}</pre>
      </section>
    </div>
  )
}

function FilterInput({
  label,
  value,
  placeholder,
  onChange,
}: {
  label: string
  value: string
  placeholder: string
  onChange: (value: string) => void
}) {
  return (
    <label className="filter-field">
      <span>{label}</span>
      <input value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </label>
  )
}

function DataPair({ label, value }: { label: string; value: string }) {
  return (
    <div className="data-pair">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function Placeholder({ title }: { title: string }) {
  return (
    <div className="placeholder-view">
      <header className="topbar">
        <div>
          <p className="eyebrow">Product area</p>
          <h1>{title}</h1>
        </div>
        <span className="pending-chip">Not wired</span>
      </header>
      <section className="placeholder-panel">
        <Database size={28} aria-hidden="true" />
        <h2>{title} is queued for a later slice</h2>
      </section>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function StatusBadge({ status }: { status: RunStatus }) {
  const Icon = status === 'succeeded' ? CheckCircle2 : status === 'failed' || status === 'cancelled' ? XCircle : Clock3
  return (
    <span className={`status-badge ${status}`}>
      <Icon size={15} aria-hidden="true" />
      {statusLabels[status]}
    </span>
  )
}

function RunSkeleton() {
  return (
    <div className="skeleton-stack" aria-label="Loading runs">
      <span />
      <span />
      <span />
    </div>
  )
}

function RunDetailSkeleton() {
  return (
    <div className="detail-content" aria-label="Loading run detail">
      <div className="skeleton-title" />
      <div className="skeleton-grid">
        <span />
        <span />
        <span />
      </div>
      <div className="skeleton-block" />
    </div>
  )
}

function formatDateTime(value: string | null) {
  if (!value) return 'Not set'
  return new Intl.DateTimeFormat(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function formatNumber(value: number | null) {
  if (value === null || Number.isNaN(value)) return 'Not set'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value)
}

function jsonPreview(value: Record<string, unknown> | null) {
  if (!value || Object.keys(value).length === 0) return '{}'
  return JSON.stringify(value, null, 2)
}

function errorText(error: unknown) {
  if (error instanceof Error) return error.message
  return 'Unable to reach the API'
}

export default App
