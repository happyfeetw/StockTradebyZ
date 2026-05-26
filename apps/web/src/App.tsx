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
  type JobEvent,
  type RunStatus,
  type RunSummary,
} from './api'

const navItems = [
  { to: '/runs', label: 'Runs', icon: Activity, state: 'active' },
  { to: '/candidates', label: 'Candidates', icon: Search, state: 'pending' },
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
          <Route path="/candidates" element={<Placeholder title="Candidates" />} />
          <Route path="/reviews" element={<Placeholder title="Reviews" />} />
          <Route path="/archive" element={<Placeholder title="Archive" />} />
          <Route path="/migrations" element={<Placeholder title="Migrations" />} />
        </Routes>
      </main>
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

function errorText(error: unknown) {
  if (error instanceof Error) return error.message
  return 'Unable to reach the API'
}

export default App
