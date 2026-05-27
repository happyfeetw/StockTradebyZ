import { useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  Archive,
  Ban,
  CheckCircle2,
  Clock3,
  Database,
  ExternalLink,
  FileSearch,
  Gauge,
  GitBranch,
  Image as ImageIcon,
  Loader2,
  Play,
  RefreshCw,
  Search,
  Server,
  ShieldAlert,
  ShieldCheck,
  Upload,
  XCircle,
} from 'lucide-react'
import './App.css'
import {
  artifactFileUrl,
  cancelRun,
  createArchiveRun,
  createChartExportRun,
  createDiagnosticRun,
  createPreselectRun,
  createReviewProviderRun,
  dryRunLegacyImport,
  getArchiveRow,
  getCandidate,
  getHealth,
  getReview,
  getRun,
  getRunArtifacts,
  getRunEvents,
  importLegacyCandidateBatch,
  importLegacyHistorySnapshot,
  importLegacyReviewRun,
  listCandidateBatches,
  listArchiveRows,
  listArchiveSnapshots,
  listCandidates,
  listRuns,
  listReviews,
  verifyLegacyImport,
  type ArchiveRow,
  type ArchiveRowFilters,
  type ArchiveSnapshot,
  type ArchiveStatus,
  type Artifact,
  type Candidate,
  type CandidateBatchSummary,
  type CandidateFilters,
  type JobEvent,
  type LegacyImportIssue,
  type LegacyImportSectionReport,
  type LegacyImportSummary,
  type LegacyImportVerifyReport,
  type LegacyImportVerifyScope,
  type PreselectRunRequest,
  type RecommendationStatus,
  type Review,
  type ReviewFilters,
  type RunStatus,
  type RunSummary,
} from './api'

const navItems = [
  { to: '/runs', label: 'Runs', icon: Activity, state: 'active' },
  { to: '/candidates', label: 'Candidates', icon: Search, state: 'active' },
  { to: '/reviews', label: 'Reviews', icon: FileSearch, state: 'active' },
  { to: '/archive', label: 'Archive', icon: Archive, state: 'active' },
  { to: '/migrations', label: 'Migrations', icon: Database, state: 'active' },
]

const statusLabels: Record<RunStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  cancelling: 'Cancelling',
  cancelled: 'Cancelled',
}

const legacyMigrationScopes: { value: LegacyImportVerifyScope; label: string; description: string }[] = [
  { value: 'candidates', label: 'Candidates', description: 'Batch files and strategy counts' },
  { value: 'reviews', label: 'Reviews', description: 'Review runs and recommendations' },
  { value: 'history', label: 'History', description: 'Archive snapshots and rows' },
]

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
          <Route path="/reviews" element={<ReviewsView />} />
          <Route path="/archive" element={<ArchiveView />} />
          <Route path="/migrations" element={<MigrationsView />} />
        </Routes>
      </main>
    </div>
  )
}

function CandidatesView() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = candidateFiltersFromParams(searchParams)
  const [selectedCandidateId, setSelectedCandidateId] = useState<number | null>(null)

  const normalizedFilters = {
    batch_id: filters.batch_id.trim(),
    pick_date: filters.pick_date.trim(),
    run_id: filters.run_id.trim(),
    strategy: filters.strategy.trim(),
    code: filters.code.trim(),
  }

  const candidatesQuery = useQuery({
    queryKey: ['candidates', normalizedFilters],
    queryFn: () => listCandidates(normalizedFilters),
  })
  const candidateBatchesQuery = useQuery({
    queryKey: ['candidate-batches', normalizedFilters.pick_date, normalizedFilters.run_id],
    queryFn: () =>
      listCandidateBatches({
        pick_date: normalizedFilters.pick_date,
        run_id: normalizedFilters.run_id,
      }),
  })
  const candidates = candidatesQuery.data?.candidates ?? []
  const candidateBatches = candidateBatchesQuery.data?.batches ?? []
  const activeBatchId = normalizedFilters.batch_id
  const activeBatch = candidateBatches.find((batch) => batch.id === activeBatchId) ?? null
  const selectedStillVisible = selectedCandidateId !== null && candidates.some((candidate) => candidate.id === selectedCandidateId)
  const activeCandidateId = selectedStillVisible ? selectedCandidateId : candidates[0]?.id

  const detailQuery = useQuery({
    queryKey: ['candidate', activeCandidateId],
    queryFn: () => getCandidate(activeCandidateId as number),
    enabled: typeof activeCandidateId === 'number',
  })
  const archiveBatchMutation = useMutation({
    mutationFn: (batch: CandidateBatchSummary) => {
      if (!batch.latest_review_run_id) {
        throw new Error('Selected batch has no review run to archive')
      }
      return createArchiveRun({
        candidate_batch_id: batch.id,
        review_run_id: batch.latest_review_run_id,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      queryClient.invalidateQueries({ queryKey: ['candidate-batches'] })
      queryClient.invalidateQueries({ queryKey: ['archive-snapshots'] })
      queryClient.invalidateQueries({ queryKey: ['archive-rows'] })
    },
  })
  const chartExportMutation = useMutation({
    mutationFn: (batch: CandidateBatchSummary) =>
      createChartExportRun({
        candidate_batch_id: batch.id,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      queryClient.invalidateQueries({ queryKey: ['candidate-batches'] })
    },
  })
  const reviewBatchMutation = useMutation({
    mutationFn: (batch: CandidateBatchSummary) =>
      createReviewProviderRun({
        candidate_batch_id: batch.id,
        provider: 'gemini-cli',
        require_charts: true,
        provider_config: {
          skip_existing: true,
        },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      queryClient.invalidateQueries({ queryKey: ['candidate-batches'] })
      queryClient.invalidateQueries({ queryKey: ['reviews'] })
      queryClient.invalidateQueries({ queryKey: ['review'] })
    },
  })

  const hasFilters = Object.values(filters).some((value) => value.trim())

  function updateFilter(key: keyof CandidateFilters, value: string) {
    const nextFilters = { ...filters, [key]: value }
    archiveBatchMutation.reset()
    chartExportMutation.reset()
    reviewBatchMutation.reset()
    setSearchParams(candidateFiltersToParams(nextFilters), { replace: true })
    setSelectedCandidateId(null)
  }

  function resetFilters() {
    const nextFilters = emptyCandidateFilters()
    archiveBatchMutation.reset()
    chartExportMutation.reset()
    reviewBatchMutation.reset()
    setSearchParams(candidateFiltersToParams(nextFilters), { replace: true })
    setSelectedCandidateId(null)
  }

  function selectBatch(batch: CandidateBatchSummary) {
    archiveBatchMutation.reset()
    chartExportMutation.reset()
    reviewBatchMutation.reset()
    const nextFilters = {
      ...filters,
      batch_id: batch.id,
      pick_date: '',
      run_id: '',
    }
    setSearchParams(candidateFiltersToParams(nextFilters), { replace: true })
    setSelectedCandidateId(null)
  }

  function runGeminiReview(batch: CandidateBatchSummary) {
    const confirmed = window.confirm(
      `Run Gemini CLI review for ${batch.pick_date} (${batch.candidate_count} candidates)? This may consume Gemini quota.`
    )
    if (!confirmed) return
    reviewBatchMutation.mutate(batch)
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
          onClick={() => {
            queryClient.invalidateQueries({ queryKey: ['candidates'] })
            queryClient.invalidateQueries({ queryKey: ['candidate-batches'] })
            queryClient.invalidateQueries({ queryKey: ['candidate'] })
          }}
        >
          <RefreshCw size={17} aria-hidden="true" />
        </button>
      </header>

      <section className="candidate-batch-panel" aria-label="Historical candidate batches">
        <div className="panel-heading">
          <div>
            <h2>Candidate batches</h2>
            <p>
              {candidateBatchesQuery.isLoading
                ? 'Loading historical batches'
                : `${candidateBatchesQuery.data?.total ?? 0} batches${activeBatch ? `, selected ${activeBatch.pick_date}` : ''}`}
            </p>
          </div>
          <div className="button-row">
            {activeBatch ? (
              <>
                <button
                  type="button"
                  className="action-button"
                  onClick={() => chartExportMutation.mutate(activeBatch)}
                  disabled={activeBatch.candidate_count === 0 || chartExportMutation.isPending}
                >
                  {chartExportMutation.isPending ? <Loader2 className="spin" size={17} aria-hidden="true" /> : <ImageIcon size={17} aria-hidden="true" />}
                  <span>Export charts</span>
                </button>
                <button
                  type="button"
                  className="action-button secondary"
                  onClick={() => runGeminiReview(activeBatch)}
                  disabled={activeBatch.candidate_count === 0 || reviewBatchMutation.isPending}
                >
                  {reviewBatchMutation.isPending ? <Loader2 className="spin" size={17} aria-hidden="true" /> : <Play size={17} aria-hidden="true" />}
                  <span>Gemini review</span>
                </button>
                <button
                  type="button"
                  className="action-button secondary"
                  onClick={() => archiveBatchMutation.mutate(activeBatch)}
                  disabled={!activeBatch.latest_review_run_id || archiveBatchMutation.isPending}
                >
                  {archiveBatchMutation.isPending ? <Loader2 className="spin" size={17} aria-hidden="true" /> : <Archive size={17} aria-hidden="true" />}
                  <span>Archive selected</span>
                </button>
              </>
            ) : null}
            {activeBatchId ? (
              <button type="button" className="action-button secondary" onClick={() => updateFilter('batch_id', '')}>
                <XCircle size={17} aria-hidden="true" />
                <span>Clear batch</span>
              </button>
            ) : null}
          </div>
        </div>

        {candidateBatchesQuery.isError ? (
          <div className="alert compact-alert" role="alert">
            <ShieldAlert size={18} aria-hidden="true" />
            <span>{errorText(candidateBatchesQuery.error)}</span>
          </div>
        ) : null}
        {archiveBatchMutation.isError ? (
          <div className="alert compact-alert" role="alert">
            <ShieldAlert size={18} aria-hidden="true" />
            <span>{errorText(archiveBatchMutation.error)}</span>
          </div>
        ) : null}
        {chartExportMutation.isError ? (
          <div className="alert compact-alert" role="alert">
            <ShieldAlert size={18} aria-hidden="true" />
            <span>{errorText(chartExportMutation.error)}</span>
          </div>
        ) : null}
        {reviewBatchMutation.isError ? (
          <div className="alert compact-alert" role="alert">
            <ShieldAlert size={18} aria-hidden="true" />
            <span>{errorText(reviewBatchMutation.error)}</span>
          </div>
        ) : null}
        {archiveBatchMutation.isSuccess ? (
          <div className="alert success-alert compact-alert" role="status">
            <CheckCircle2 size={18} aria-hidden="true" />
            <span>Archive run {archiveBatchMutation.data.run.id} created from {archiveBatchMutation.data.snapshot.review_run_id}.</span>
          </div>
        ) : null}
        {chartExportMutation.isSuccess ? (
          <div className="alert success-alert compact-alert chart-export-alert" role="status">
            <CheckCircle2 size={18} aria-hidden="true" />
            <span>
              Chart run {chartExportMutation.data.run.id} exported {chartExportMutation.data.artifacts.length} artifacts.
            </span>
            {chartExportMutation.data.artifacts[0] ? (
              <a
                className="artifact-open-link"
                href={artifactFileUrl(chartExportMutation.data.artifacts[0].id)}
                target="_blank"
                rel="noreferrer"
                aria-label="Open first chart artifact"
              >
                <ExternalLink size={15} aria-hidden="true" />
                <span>Open first chart</span>
              </a>
            ) : null}
          </div>
        ) : null}
        {reviewBatchMutation.isSuccess ? (
          <div className="alert success-alert compact-alert chart-export-alert" role="status">
            <CheckCircle2 size={18} aria-hidden="true" />
            <span>
              Review run {reviewBatchMutation.data.review_run.id} recorded {reviewBatchMutation.data.reviews.length} reviews.
            </span>
            <Link
              className="artifact-open-link"
              to={`/reviews?candidate_batch_id=${encodeURIComponent(reviewBatchMutation.data.review_run.candidate_batch_id ?? activeBatchId)}`}
            >
              <FileSearch size={15} aria-hidden="true" />
              <span>Open reviews</span>
            </Link>
          </div>
        ) : null}

        {candidateBatchesQuery.isLoading ? <RunSkeleton /> : null}
        {!candidateBatchesQuery.isLoading && candidateBatches.length === 0 ? (
          <div className="empty-state batch-empty">
            <Database size={24} aria-hidden="true" />
            <h3>{normalizedFilters.pick_date || normalizedFilters.run_id ? 'No batches match the filters' : 'No candidate batches yet'}</h3>
          </div>
        ) : null}

        <div className="candidate-batch-grid">
          {candidateBatches.map((batch) => (
            <article
              key={batch.id}
              className={batch.id === activeBatchId ? 'candidate-batch-row selected' : 'candidate-batch-row'}
            >
              <button
                type="button"
                className="candidate-batch-main"
                onClick={() => selectBatch(batch)}
                aria-label={`Use candidate batch ${batch.id} from ${batch.pick_date}`}
              >
                <span className="batch-row-head">
                  <strong>{batch.pick_date}</strong>
                  <span className="strategy-chip">{batch.source}</span>
                </span>
                <span className="batch-id-line">{batch.id}</span>
                <span className="batch-metrics">
                  <span>{batch.candidate_count} candidates</span>
                  <span>{batch.latest_reviewed_count} reviewed</span>
                  <span>{batch.latest_recommended_count} rec</span>
                  <span>{batch.archive_snapshot_count} archives</span>
                </span>
                <span className="batch-lineage">
                  <code>{batch.run_id}</code>
                  <code>{batch.latest_review_run_id ?? 'no review run'}</code>
                </span>
              </button>
              <span className="batch-actions">
                <Link className="artifact-open-link" to={`/reviews?candidate_batch_id=${encodeURIComponent(batch.id)}`}>
                  <FileSearch size={15} aria-hidden="true" />
                  <span>Reviews</span>
                </Link>
                <Link className="artifact-open-link" to={`/archive?pick_date=${encodeURIComponent(batch.pick_date)}`}>
                  <Archive size={15} aria-hidden="true" />
                  <span>Archive</span>
                </Link>
              </span>
            </article>
          ))}
        </div>
      </section>

      <form className="filter-bar candidate-filter-bar" onSubmit={(event) => event.preventDefault()} aria-label="Candidate filters">
        <FilterInput
          label="Batch"
          placeholder="candidate batch"
          value={filters.batch_id}
          onChange={(value) => updateFilter('batch_id', value)}
        />
        <FilterInput
          label="Pick date"
          placeholder="2026-05-27"
          type="date"
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
          <XCircle size={17} aria-hidden="true" />
          <span>Clear</span>
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
                aria-label={`${candidate.code} ${candidate.strategy} candidate from run ${candidate.run_id}`}
              >
                <span className="candidate-row-head">
                  <span className="candidate-code">{candidate.code}</span>
                  <span className="strategy-chip">{candidate.strategy}</span>
                </span>
                <CandidateCell label="Pick date" value={candidate.pick_date} />
                <CandidateCell label="Close" value={formatNumber(candidate.close)} strong />
                <CandidateCell label="Turnover" value={formatNumber(candidate.turnover_n)} />
                <CandidateCell label="Brick growth" value={formatNumber(candidate.brick_growth)} />
                <CandidateCell label="Run" value={candidate.run_id} mono wide />
                <CandidateCell label="Batch" value={candidate.batch_id} mono wide />
                <CandidateCell label="Extra" value={jsonInline(candidate.extra)} extra />
              </button>
            ))}
          </div>
        </section>

        <section className="detail-panel" aria-label="Candidate detail">
          {detailQuery.isError ? (
            <div className="alert detail-alert" role="alert">
              <ShieldAlert size={18} aria-hidden="true" />
              <span>{errorText(detailQuery.error)}</span>
            </div>
          ) : null}
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

function ReviewsView() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = reviewFiltersFromParams(searchParams)
  const [selectedReviewId, setSelectedReviewId] = useState<number | null>(null)

  const normalizedFilters = {
    pick_date: filters.pick_date.trim(),
    run_id: filters.run_id.trim(),
    review_run_id: filters.review_run_id.trim(),
    candidate_batch_id: filters.candidate_batch_id.trim(),
    strategy: filters.strategy.trim(),
    code: filters.code.trim(),
    review_key: filters.review_key.trim(),
    reviewer: filters.reviewer.trim(),
    recommendation_status: filters.recommendation_status,
  }

  const reviewsQuery = useQuery({
    queryKey: ['reviews', normalizedFilters],
    queryFn: () => listReviews(normalizedFilters),
  })
  const reviews = reviewsQuery.data?.reviews ?? []
  const selectedStillVisible = selectedReviewId !== null && reviews.some((review) => review.id === selectedReviewId)
  const activeReviewId = selectedStillVisible ? selectedReviewId : reviews[0]?.id

  const detailQuery = useQuery({
    queryKey: ['review', activeReviewId],
    queryFn: () => getReview(activeReviewId as number),
    enabled: typeof activeReviewId === 'number',
  })

  const hasFilters = Object.entries(filters).some(([key, value]) => {
    if (key === 'recommendation_status') return value !== 'all'
    return value.trim()
  })

  function updateFilter(key: Exclude<keyof ReviewFilters, 'recommendation_status'>, value: string) {
    const nextFilters = { ...filters, [key]: value }
    setSearchParams(reviewFiltersToParams(nextFilters), { replace: true })
    setSelectedReviewId(null)
  }

  function updateRecommendationStatus(value: RecommendationStatus) {
    const nextFilters = { ...filters, recommendation_status: value }
    setSearchParams(reviewFiltersToParams(nextFilters), { replace: true })
    setSelectedReviewId(null)
  }

  function resetFilters() {
    const nextFilters = emptyReviewFilters()
    setSearchParams(reviewFiltersToParams(nextFilters), { replace: true })
    setSelectedReviewId(null)
  }

  return (
    <div className="run-center">
      <header className="topbar">
        <div>
          <p className="eyebrow">Review evidence</p>
          <h1>Reviews</h1>
        </div>
        <button
          type="button"
          className="icon-button secondary"
          aria-label="Refresh reviews"
          onClick={() => {
            queryClient.invalidateQueries({ queryKey: ['reviews'] })
            queryClient.invalidateQueries({ queryKey: ['review'] })
          }}
        >
          <RefreshCw size={17} aria-hidden="true" />
        </button>
      </header>

      <form className="filter-bar review-filter-bar" onSubmit={(event) => event.preventDefault()} aria-label="Review filters">
        <FilterInput
          label="Pick date"
          placeholder="2026-05-27"
          type="date"
          value={filters.pick_date}
          onChange={(value) => updateFilter('pick_date', value)}
        />
        <FilterInput label="Run id" placeholder="run id" value={filters.run_id} onChange={(value) => updateFilter('run_id', value)} />
        <FilterInput
          label="Review run"
          placeholder="review batch"
          value={filters.review_run_id}
          onChange={(value) => updateFilter('review_run_id', value)}
        />
        <FilterInput
          label="Batch"
          placeholder="candidate batch"
          value={filters.candidate_batch_id}
          onChange={(value) => updateFilter('candidate_batch_id', value)}
        />
        <FilterInput
          label="Strategy"
          placeholder="b2 / brick"
          value={filters.strategy}
          onChange={(value) => updateFilter('strategy', value)}
        />
        <FilterInput label="Code" placeholder="000001" value={filters.code} onChange={(value) => updateFilter('code', value)} />
        <FilterInput
          label="Review key"
          placeholder="000001_b2"
          value={filters.review_key}
          onChange={(value) => updateFilter('review_key', value)}
        />
        <FilterInput
          label="Reviewer"
          placeholder="gemini-cli"
          value={filters.reviewer}
          onChange={(value) => updateFilter('reviewer', value)}
        />
        <FilterSelect label="Status" value={filters.recommendation_status} onChange={updateRecommendationStatus} />
        <button type="button" className="action-button secondary filter-reset" onClick={resetFilters} disabled={!hasFilters}>
          <XCircle size={17} aria-hidden="true" />
          <span>Clear</span>
        </button>
      </form>

      {reviewsQuery.isError ? (
        <div className="alert" role="alert">
          <ShieldAlert size={18} aria-hidden="true" />
          <span>{errorText(reviewsQuery.error)}</span>
        </div>
      ) : null}

      <div className="review-grid">
        <section className="review-list-panel" aria-label="Review list">
          <div className="panel-heading">
            <div>
              <h2>Review rows</h2>
              <p>{reviewsQuery.isLoading ? 'Loading review rows' : `${reviewsQuery.data?.total ?? 0} records`}</p>
            </div>
          </div>

          {reviewsQuery.isLoading ? <RunSkeleton /> : null}
          {!reviewsQuery.isLoading && reviews.length === 0 ? (
            <div className="empty-state">
              <FileSearch size={24} aria-hidden="true" />
              <h3>{hasFilters ? 'No reviews match the filters' : 'No review rows yet'}</h3>
            </div>
          ) : null}

          <div className="review-list">
            {reviews.map((review) => (
              <button
                key={review.id}
                type="button"
                className={review.id === activeReviewId ? 'review-row selected' : 'review-row'}
                onClick={() => setSelectedReviewId(review.id)}
                aria-label={`${review.code} ${review.strategy} review ${review.review_key}`}
              >
                <span className="review-row-head">
                  <span className="candidate-code">{review.code}</span>
                  <span className="strategy-chip">{review.strategy}</span>
                </span>
                <span className={`verdict-chip ${verdictClass(review.verdict)}`}>{review.verdict ?? 'No verdict'}</span>
                <CandidateCell label="Score" value={formatNumber(review.total_score)} strong />
                <CandidateCell label="Rank" value={formatRank(review)} />
                <CandidateCell label="Pick date" value={review.pick_date} />
                <CandidateCell label="Review key" value={review.review_key} mono wide extra />
                <CandidateCell label="Run" value={review.run_id} mono wide />
                <CandidateCell label="Reviewer" value={reviewerName(review)} />
              </button>
            ))}
          </div>
        </section>

        <section className="detail-panel" aria-label="Review detail">
          {detailQuery.isError ? (
            <div className="alert detail-alert" role="alert">
              <ShieldAlert size={18} aria-hidden="true" />
              <span>{errorText(detailQuery.error)}</span>
            </div>
          ) : null}
          {detailQuery.isLoading && activeReviewId ? <RunDetailSkeleton /> : null}
          {!activeReviewId && !reviewsQuery.isLoading ? (
            <div className="empty-state detail-empty">
              <FileSearch size={24} aria-hidden="true" />
              <h3>Select a review</h3>
            </div>
          ) : null}
          {detailQuery.data ? <ReviewDetailPanel review={detailQuery.data.review} /> : null}
        </section>
      </div>
    </div>
  )
}

function ArchiveView() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = archiveFiltersFromParams(searchParams)
  const [selectedRowId, setSelectedRowId] = useState<number | null>(null)

  const snapshotsQuery = useQuery({
    queryKey: ['archive-snapshots'],
    queryFn: () => listArchiveSnapshots(),
  })
  const snapshots = snapshotsQuery.data?.snapshots ?? []
  const filteredPickDate = filters.pick_date.trim()
  const activePickDate = filteredPickDate || (snapshots[0]?.pick_date ?? '')
  const activeSnapshot = findActiveSnapshot(snapshots, activePickDate, filters.run_id.trim())

  const normalizedFilters = {
    pick_date: filteredPickDate,
    run_id: filters.run_id.trim(),
    strategy: filters.strategy.trim(),
    code: filters.code.trim(),
    review_key: filters.review_key.trim(),
    status: filters.status,
    rank: filters.rank.trim(),
  }

  const rowsQuery = useQuery({
    queryKey: ['archive-rows', activePickDate, normalizedFilters],
    queryFn: () => listArchiveRows(activePickDate, normalizedFilters),
    enabled: Boolean(activePickDate),
  })
  const rows = rowsQuery.data?.rows ?? []
  const selectedStillVisible = selectedRowId !== null && rows.some((row) => row.id === selectedRowId)
  const activeRowId = selectedStillVisible ? selectedRowId : rows[0]?.id

  const detailQuery = useQuery({
    queryKey: ['archive-row', activeRowId],
    queryFn: () => getArchiveRow(activeRowId as number),
    enabled: typeof activeRowId === 'number',
  })

  const hasFilters = Object.entries(filters).some(([key, value]) => {
    if (key === 'status') return value !== 'all'
    return value.trim()
  })

  function updateFilter(key: Exclude<keyof ArchiveRowFilters, 'status'>, value: string) {
    const nextFilters = { ...filters, [key]: value }
    setSearchParams(archiveFiltersToParams(nextFilters), { replace: true })
    setSelectedRowId(null)
  }

  function updateArchiveStatus(value: ArchiveStatus) {
    const nextFilters = { ...filters, status: value }
    setSearchParams(archiveFiltersToParams(nextFilters), { replace: true })
    setSelectedRowId(null)
  }

  function selectSnapshot(snapshot: ArchiveSnapshot) {
    setSelectedRowId(null)
    const nextFilters = {
      ...filters,
      pick_date: snapshot.pick_date,
      run_id: snapshot.run_id,
    }
    setSearchParams(archiveFiltersToParams(nextFilters), { replace: true })
  }

  function resetFilters() {
    const nextFilters = emptyArchiveFilters()
    setSearchParams(archiveFiltersToParams(nextFilters), { replace: true })
    setSelectedRowId(null)
  }

  return (
    <div className="run-center">
      <header className="topbar">
        <div>
          <p className="eyebrow">History evidence</p>
          <h1>Archive</h1>
        </div>
        <button
          type="button"
          className="icon-button secondary"
          aria-label="Refresh archive"
          onClick={() => {
            queryClient.invalidateQueries({ queryKey: ['archive-snapshots'] })
            queryClient.invalidateQueries({ queryKey: ['archive-rows'] })
            queryClient.invalidateQueries({ queryKey: ['archive-row'] })
          }}
        >
          <RefreshCw size={17} aria-hidden="true" />
        </button>
      </header>

      <section className="summary-strip archive-summary-strip" aria-label="Archive summary">
        <Metric label="Archive dates" value={(snapshotsQuery.data?.total ?? 0).toString()} />
        <Metric label="Candidates" value={formatNumber(activeSnapshot?.candidate_count ?? null)} />
        <Metric label="Recommended" value={formatNumber(activeSnapshot?.recommended_count ?? null)} />
        <Metric label="Reviewed" value={formatNumber(activeSnapshot?.reviewed_count ?? null)} />
      </section>

      <form className="filter-bar archive-filter-bar" onSubmit={(event) => event.preventDefault()} aria-label="Archive filters">
        <FilterInput
          label="Pick date"
          placeholder="2026-05-27"
          type="date"
          value={filters.pick_date}
          onChange={(value) => updateFilter('pick_date', value)}
        />
        <FilterInput label="Run id" placeholder="archive run" value={filters.run_id} onChange={(value) => updateFilter('run_id', value)} />
        <FilterInput
          label="Strategy"
          placeholder="b2 / brick"
          value={filters.strategy}
          onChange={(value) => updateFilter('strategy', value)}
        />
        <FilterInput label="Code" placeholder="000001" value={filters.code} onChange={(value) => updateFilter('code', value)} />
        <FilterInput
          label="Review key"
          placeholder="000001_b2"
          value={filters.review_key}
          onChange={(value) => updateFilter('review_key', value)}
        />
        <ArchiveStatusSelect label="Status" value={filters.status} onChange={updateArchiveStatus} />
        <FilterInput label="Rank" placeholder="1" value={filters.rank} onChange={(value) => updateFilter('rank', value)} />
        <button type="button" className="action-button secondary filter-reset" onClick={resetFilters} disabled={!hasFilters}>
          <XCircle size={17} aria-hidden="true" />
          <span>Clear</span>
        </button>
      </form>

      {snapshotsQuery.isError || rowsQuery.isError ? (
        <div className="alert" role="alert">
          <ShieldAlert size={18} aria-hidden="true" />
          <span>{errorText(snapshotsQuery.error ?? rowsQuery.error)}</span>
        </div>
      ) : null}

      <div className="archive-grid">
        <section className="archive-snapshot-panel" aria-label="Archive dates">
          <div className="panel-heading">
            <div>
              <h2>Archive dates</h2>
              <p>{snapshotsQuery.isLoading ? 'Loading snapshots' : `${snapshotsQuery.data?.total ?? 0} snapshots`}</p>
            </div>
          </div>

          {snapshotsQuery.isLoading ? <RunSkeleton /> : null}
          {!snapshotsQuery.isLoading && snapshots.length === 0 ? (
            <div className="empty-state">
              <Archive size={24} aria-hidden="true" />
              <h3>No archive snapshots yet</h3>
            </div>
          ) : null}

          <div className="archive-snapshot-list">
            {snapshots.map((snapshot) => {
              const selected = snapshot.pick_date === activePickDate && (!filters.run_id.trim() || snapshot.run_id === filters.run_id.trim())
              return (
                <button
                  key={snapshot.id}
                  type="button"
                  className={selected ? 'archive-snapshot-row selected' : 'archive-snapshot-row'}
                  onClick={() => selectSnapshot(snapshot)}
                  aria-label={`Archive ${snapshot.pick_date} run ${snapshot.run_id}`}
                >
                  <span>
                    <strong>{snapshot.pick_date}</strong>
                    <small>{formatDateTime(snapshot.archived_at ?? snapshot.created_at)}</small>
                  </span>
                  <span className="snapshot-counts">
                    <span>{snapshot.candidate_count} rows</span>
                    <span>{snapshot.recommended_count} rec</span>
                  </span>
                  <code>{snapshot.run_id}</code>
                </button>
              )
            })}
          </div>
        </section>

        <section className="archive-list-panel" aria-label="Archive rows">
          <div className="panel-heading">
            <div>
              <h2>Archive rows</h2>
              <p>
                {rowsQuery.isLoading
                  ? 'Loading archive rows'
                  : activePickDate
                    ? `${rowsQuery.data?.total ?? 0} records for ${activePickDate}`
                    : 'Select an archive date'}
              </p>
            </div>
          </div>

          {rowsQuery.isLoading ? <RunSkeleton /> : null}
          {!rowsQuery.isLoading && activePickDate && rows.length === 0 ? (
            <div className="empty-state">
              <Search size={24} aria-hidden="true" />
              <h3>{hasFilters ? 'No archive rows match the filters' : 'No rows for this archive date'}</h3>
            </div>
          ) : null}
          {!activePickDate && !snapshotsQuery.isLoading ? (
            <div className="empty-state">
              <Archive size={24} aria-hidden="true" />
              <h3>Select an archive date</h3>
            </div>
          ) : null}

          <div className="archive-list">
            {rows.map((row) => (
              <button
                key={row.id}
                type="button"
                className={row.id === activeRowId ? 'archive-row selected' : 'archive-row'}
                onClick={() => setSelectedRowId(row.id)}
                aria-label={`${row.code} ${row.strategy} archive row ${row.review_key}`}
              >
                <span className="review-row-head">
                  <span className="candidate-code">{row.code}</span>
                  <span className="strategy-chip">{row.strategy}</span>
                </span>
                <span className={`archive-status-chip ${archiveStatusClass(row.status)}`}>{archiveStatusLabel(row.status)}</span>
                <CandidateCell label="Rank" value={formatArchiveRank(row)} />
                <CandidateCell label="Close" value={formatNumber(row.close)} strong />
                <CandidateCell label="Review key" value={row.review_key} mono wide extra />
                <CandidateCell label="Run" value={row.run_id} mono wide />
                <CandidateCell label="Chart" value={row.chart || 'Not linked'} wide extra />
              </button>
            ))}
          </div>
        </section>

        <section className="detail-panel" aria-label="Archive row detail">
          {detailQuery.isError ? (
            <div className="alert detail-alert" role="alert">
              <ShieldAlert size={18} aria-hidden="true" />
              <span>{errorText(detailQuery.error)}</span>
            </div>
          ) : null}
          {detailQuery.isLoading && activeRowId ? <RunDetailSkeleton /> : null}
          {!activeRowId && !rowsQuery.isLoading ? (
            <div className="empty-state detail-empty">
              <Archive size={24} aria-hidden="true" />
              <h3>Select an archive row</h3>
            </div>
          ) : null}
          {detailQuery.data ? <ArchiveDetailPanel row={detailQuery.data.row} /> : null}
        </section>
      </div>
    </div>
  )
}

function MigrationsView() {
  const queryClient = useQueryClient()
  const [dataRoot, setDataRoot] = useState('data')
  const [importScope, setImportScope] = useState<LegacyImportVerifyScope>('candidates')
  const [importPickDate, setImportPickDate] = useState('')
  const [verifyScope, setVerifyScope] = useState<LegacyImportVerifyScope>('candidates')
  const [verifyPickDate, setVerifyPickDate] = useState('')
  const [verifyRunId, setVerifyRunId] = useState('')
  const dryRunMutation = useMutation({
    mutationFn: (root: string) => dryRunLegacyImport(root),
  })
  const importMutation = useMutation({
    mutationFn: ({ root, scope, pickDate }: { root: string; scope: LegacyImportVerifyScope; pickDate: string }) => {
      if (scope === 'candidates') return importLegacyCandidateBatch(root, pickDate)
      if (scope === 'reviews') return importLegacyReviewRun(root, pickDate)
      return importLegacyHistorySnapshot(root, pickDate)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      queryClient.invalidateQueries({ queryKey: ['candidates'] })
      queryClient.invalidateQueries({ queryKey: ['reviews'] })
      queryClient.invalidateQueries({ queryKey: ['archive-snapshots'] })
      queryClient.invalidateQueries({ queryKey: ['archive-rows'] })
    },
  })
  const verifyMutation = useMutation({
    mutationFn: ({ root, scope, pickDate, runId }: { root: string; scope: LegacyImportVerifyScope; pickDate: string; runId: string }) =>
      verifyLegacyImport(root, scope, pickDate, runId.trim() || undefined),
  })
  const report = dryRunMutation.data
  const importSummary = importMutation.isSuccess ? (importMutation.data.import_summary ?? null) : null
  const verifyReport = verifyMutation.isSuccess ? verifyMutation.data : null
  const totals = report?.totals
  const sectionNames = ['candidates', 'reviews', 'history']
  const normalizedRoot = dataRoot.trim() || 'data'

  function runDryRun() {
    dryRunMutation.mutate(normalizedRoot)
  }

  function runImport() {
    if (!importPickDate.trim()) return
    importMutation.mutate({ root: normalizedRoot, scope: importScope, pickDate: importPickDate.trim() })
  }

  function runVerify() {
    if (!verifyPickDate.trim()) return
    verifyMutation.mutate({
      root: normalizedRoot,
      scope: verifyScope,
      pickDate: verifyPickDate.trim(),
      runId: verifyRunId,
    })
  }

  return (
    <div className="run-center">
      <header className="topbar">
        <div>
          <p className="eyebrow">Migration workbench</p>
          <h1>Migrations</h1>
        </div>
        <span className="pending-chip">Candidates / Reviews / History</span>
      </header>

      <section className="summary-strip migration-summary-strip" aria-label="Migration dry-run summary">
        <Metric label="Files" value={totals ? `${totals.files_valid}/${totals.files_seen}` : 'Not run'} />
        <Metric label="Records" value={totals ? `${totals.records_valid}/${totals.records_seen}` : 'Not run'} />
        <Metric label="Warnings" value={totals ? totals.warning_count.toString() : 'Not run'} />
        <Metric label="Quarantine" value={totals ? totals.quarantine_count.toString() : 'Not run'} />
      </section>

      <section className="migration-control-panel" aria-label="Legacy import dry-run control">
        <form
          className="migration-form"
          onSubmit={(event) => {
            event.preventDefault()
            runDryRun()
          }}
        >
          <FilterInput label="Data root" placeholder="data" value={dataRoot} onChange={setDataRoot} />
          <button type="submit" className="action-button" disabled={dryRunMutation.isPending}>
            {dryRunMutation.isPending ? <Loader2 className="spin" size={17} aria-hidden="true" /> : <Play size={17} aria-hidden="true" />}
            <span>Dry run</span>
          </button>
        </form>
        <p className="muted migration-note">
          Dry-run scans candidates, reviews, and history before a write operation. Trading account and simulated trading data stay out of this
          migration flow.
        </p>
      </section>

      {dryRunMutation.isError ? (
        <div className="alert" role="alert">
          <ShieldAlert size={18} aria-hidden="true" />
          <span>{errorText(dryRunMutation.error)}</span>
        </div>
      ) : null}

      <div className="migration-action-grid" aria-label="Legacy migration actions">
        <section className="migration-action-panel" aria-label="Legacy import execution">
          <div className="panel-heading">
            <div>
              <h2>Import</h2>
              <p>Writes one legacy scope into product storage</p>
            </div>
            <Upload size={19} aria-hidden="true" />
          </div>
          <form
            className="migration-action-form"
            onSubmit={(event) => {
              event.preventDefault()
              runImport()
            }}
          >
            <ScopeSelector label="Import scope" value={importScope} onChange={setImportScope} />
            <FilterInput label="Pick date" type="date" placeholder="YYYY-MM-DD" value={importPickDate} onChange={setImportPickDate} />
            <button type="submit" className="action-button danger" disabled={importMutation.isPending || !importPickDate.trim()}>
              {importMutation.isPending ? (
                <Loader2 className="spin" size={17} aria-hidden="true" />
              ) : (
                <Upload size={17} aria-hidden="true" />
              )}
              <span>Import scope</span>
            </button>
          </form>
          {importMutation.isError ? (
            <div className="alert compact-alert" role="alert">
              <ShieldAlert size={18} aria-hidden="true" />
              <span>{errorText(importMutation.error)}</span>
            </div>
          ) : null}
          {importSummary ? <ImportSummaryPanel summary={importSummary} /> : null}
        </section>

        <section className="migration-action-panel" aria-label="Legacy import verification">
          <div className="panel-heading">
            <div>
              <h2>Verify</h2>
              <p>Compares legacy files with SQLite and DuckDB records</p>
            </div>
            <ShieldCheck size={19} aria-hidden="true" />
          </div>
          <form
            className="migration-action-form"
            onSubmit={(event) => {
              event.preventDefault()
              runVerify()
            }}
          >
            <ScopeSelector label="Verify scope" value={verifyScope} onChange={setVerifyScope} />
            <FilterInput label="Pick date" type="date" placeholder="YYYY-MM-DD" value={verifyPickDate} onChange={setVerifyPickDate} />
            <FilterInput label="Run id" placeholder="Optional" value={verifyRunId} onChange={setVerifyRunId} />
            <button type="submit" className="action-button" disabled={verifyMutation.isPending || !verifyPickDate.trim()}>
              {verifyMutation.isPending ? (
                <Loader2 className="spin" size={17} aria-hidden="true" />
              ) : (
                <ShieldCheck size={17} aria-hidden="true" />
              )}
              <span>Verify</span>
            </button>
          </form>
          {verifyMutation.isError ? (
            <div className="alert compact-alert" role="alert">
              <ShieldAlert size={18} aria-hidden="true" />
              <span>{errorText(verifyMutation.error)}</span>
            </div>
          ) : null}
          {verifyReport ? <VerifyResultPanel report={verifyReport} /> : null}
        </section>
      </div>

      {!report && !dryRunMutation.isPending ? (
        <div className="empty-state migration-empty">
          <Database size={24} aria-hidden="true" />
          <h3>Run a dry-run scan before importing legacy files</h3>
        </div>
      ) : null}

      {dryRunMutation.isPending ? <RunSkeleton /> : null}

      {report ? (
        <div className="migration-grid">
          <section className="migration-section-panel" aria-label="Migration section reports">
            <div className="panel-heading">
              <div>
                <h2>Sections</h2>
                <p>{report.data_root}</p>
              </div>
              <span className="status-badge succeeded">
                <CheckCircle2 size={15} aria-hidden="true" />
                Dry run
              </span>
            </div>
            <div className="migration-section-grid">
              {sectionNames.map((section) => (
                <MigrationSectionCard key={section} name={section} report={report.sections[section]} />
              ))}
            </div>
          </section>

          <section className="migration-issues-panel" aria-label="Migration warnings and quarantine">
            <div className="panel-heading">
              <div>
                <h2>Issues</h2>
                <p>{report.warnings.length + report.quarantine.length} findings</p>
              </div>
            </div>
            <MigrationIssueList title="Warnings" kind="warning" issues={report.warnings} />
            <MigrationIssueList title="Quarantine" kind="quarantine" issues={report.quarantine} />
          </section>
        </div>
      ) : null}
    </div>
  )
}

function RunsView() {
  const queryClient = useQueryClient()
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [preselectForm, setPreselectForm] = useState<Record<keyof PreselectRunRequest, string>>({
    config_path: '',
    data_dir: '',
    pick_date: '',
    end_date: '',
  })

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

  const preselectMutation = useMutation({
    mutationFn: () => createPreselectRun(compactPreselectRequest(preselectForm)),
    onSuccess: (response) => {
      setSelectedRunId(response.run.id)
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      queryClient.invalidateQueries({ queryKey: ['candidates'] })
      queryClient.invalidateQueries({ queryKey: ['run', response.run.id] })
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
  const preselectResult = preselectMutation.isSuccess ? preselectMutation.data : null

  function updatePreselectField(key: keyof PreselectRunRequest, value: string) {
    setPreselectForm((current) => ({ ...current, [key]: value }))
  }

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

          <form
            className="run-setup-form"
            aria-label="Preselect run setup"
            onSubmit={(event) => {
              event.preventDefault()
              preselectMutation.mutate()
            }}
          >
            <FilterInput
              label="Config path"
              placeholder="config/rules_preselect.yaml"
              value={preselectForm.config_path}
              onChange={(value) => updatePreselectField('config_path', value)}
            />
            <FilterInput
              label="Data dir"
              placeholder="data/daily"
              value={preselectForm.data_dir}
              onChange={(value) => updatePreselectField('data_dir', value)}
            />
            <FilterInput
              label="Pick date"
              type="date"
              placeholder="YYYY-MM-DD"
              value={preselectForm.pick_date}
              onChange={(value) => updatePreselectField('pick_date', value)}
            />
            <FilterInput
              label="End date"
              type="date"
              placeholder="YYYY-MM-DD"
              value={preselectForm.end_date}
              onChange={(value) => updatePreselectField('end_date', value)}
            />
            <button type="submit" className="action-button run-setup-submit" disabled={preselectMutation.isPending}>
              {preselectMutation.isPending ? <Loader2 className="spin" size={17} aria-hidden="true" /> : <Play size={17} aria-hidden="true" />}
              <span>Run preselect</span>
            </button>
          </form>

          {preselectMutation.isError ? (
            <div className="alert compact-alert" role="alert">
              <ShieldAlert size={18} aria-hidden="true" />
              <span>{errorText(preselectMutation.error)}</span>
            </div>
          ) : null}

          {preselectResult ? (
            <div className="run-setup-result" aria-label="Preselect result">
              <StatusBadge status={preselectResult.run.status} />
              <DataPair label="Batch" value={preselectResult.batch.id} />
              <DataPair label="Pick date" value={preselectResult.batch.pick_date} />
              <DataPair label="Candidates" value={preselectResult.batch.total.toString()} />
            </div>
          ) : null}

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
            {isProductOwnedArtifactPath(artifact.path) ? (
              <a
                className="artifact-open-link"
                href={artifactFileUrl(artifact.id)}
                target="_blank"
                rel="noreferrer"
                aria-label={`Open ${artifact.kind} artifact`}
              >
                <ExternalLink size={15} aria-hidden="true" />
                <span>Open</span>
              </a>
            ) : (
              <span className="artifact-source-chip">Legacy path</span>
            )}
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

function ReviewDetailPanel({ review }: { review: Review }) {
  return (
    <div className="detail-content">
      <div className="detail-header">
        <div>
          <p className="eyebrow">Selected review</p>
          <h2>{review.code}</h2>
          <p className="muted run-id-wrap">{review.review_key}</p>
        </div>
        <div className="review-detail-actions">
          <span className="strategy-chip large">{review.strategy}</span>
          <span className={`verdict-chip large ${verdictClass(review.verdict)}`}>{review.verdict ?? 'No verdict'}</span>
        </div>
      </div>

      <div className="detail-meta review-metrics">
        <Metric label="Pick date" value={review.pick_date} />
        <Metric label="Score" value={formatNumber(review.total_score)} />
        <Metric label="Rank" value={formatRank(review)} />
        <Metric label="Provider" value={reviewerName(review)} />
      </div>

      <section className="subsection">
        <h3>Lineage</h3>
        <div className="lineage-grid">
          <DataPair label="Review id" value={review.id.toString()} />
          <DataPair label="Review run" value={review.review_run_id} />
          <DataPair label="Run id" value={review.run_id} />
          <DataPair label="Candidate batch" value={review.candidate_batch_id ?? 'Not linked'} />
          <DataPair label="Candidate id" value={review.candidate_id?.toString() ?? 'Not linked'} />
          <DataPair label="Created" value={formatDateTime(review.created_at)} />
        </div>
      </section>

      <section className="subsection">
        <h3>Review payload</h3>
        <pre className="json-block">{jsonPreview(review.payload)}</pre>
      </section>

      <section className="subsection">
        <h3>Review run summary</h3>
        <pre className="json-block">{jsonPreview(review.review_run.summary)}</pre>
      </section>

      <section className="subsection">
        <h3>Recommendation payload</h3>
        {review.recommendation ? (
          <pre className="json-block">{jsonPreview(review.recommendation.payload)}</pre>
        ) : (
          <p className="muted">This review is not in the recommendation list.</p>
        )}
      </section>
    </div>
  )
}

function ArchiveDetailPanel({ row }: { row: ArchiveRow }) {
  return (
    <div className="detail-content">
      <div className="detail-header">
        <div>
          <p className="eyebrow">Selected archive row</p>
          <h2>{row.code}</h2>
          <p className="muted run-id-wrap">{row.review_key}</p>
        </div>
        <div className="review-detail-actions">
          <span className="strategy-chip large">{row.strategy}</span>
          <span className={`archive-status-chip large ${archiveStatusClass(row.status)}`}>{archiveStatusLabel(row.status)}</span>
        </div>
      </div>

      <div className="detail-meta archive-metrics">
        <Metric label="Pick date" value={row.pick_date} />
        <Metric label="Rank" value={formatArchiveRank(row)} />
        <Metric label="Close" value={formatNumber(row.close)} />
        <Metric label="Turnover" value={formatNumber(row.turnover_n)} />
      </div>

      <section className="subsection">
        <h3>Lineage</h3>
        <div className="lineage-grid">
          <DataPair label="Archive row" value={row.id.toString()} />
          <DataPair label="Snapshot" value={row.snapshot_id} />
          <DataPair label="Run id" value={row.run_id} />
          <DataPair label="Candidate batch" value={row.candidate_batch_id ?? 'Not linked'} />
          <DataPair label="Review run" value={row.review_run_id ?? 'Not linked'} />
          <DataPair label="Candidate id" value={row.candidate_id?.toString() ?? 'Not linked'} />
          <DataPair label="Review id" value={row.review_id?.toString() ?? 'Not linked'} />
          <DataPair label="Recommendation id" value={row.recommendation_id?.toString() ?? 'Not linked'} />
          <DataPair label="Chart artifact" value={row.chart_artifact_id ?? 'Not linked'} />
          <DataPair label="Archived" value={formatDateTime(row.snapshot.archived_at ?? row.snapshot.created_at)} />
          <DataPair label="Chart" value={row.chart ?? 'Not linked'} />
        </div>
      </section>

      <ArchiveChartArtifact row={row} />

      <section className="subsection">
        <h3>Snapshot summary</h3>
        <div className="lineage-grid">
          <DataPair label="Candidates" value={row.snapshot.candidate_count.toString()} />
          <DataPair label="Recommended" value={row.snapshot.recommended_count.toString()} />
          <DataPair label="Reviewed" value={row.snapshot.reviewed_count.toString()} />
          <DataPair label="Threshold" value={formatNumber(row.snapshot.min_score_threshold)} />
        </div>
        <pre className="json-block">{jsonPreview(row.snapshot.summary)}</pre>
      </section>

      <section className="subsection">
        <h3>Extra</h3>
        <pre className="json-block">{jsonPreview(row.extra)}</pre>
      </section>

      <section className="subsection">
        <h3>Review payload</h3>
        <pre className="json-block">{jsonPreview(row.review_payload)}</pre>
      </section>

      <section className="subsection">
        <h3>Strategy counts</h3>
        <pre className="json-block">{jsonPreview(row.snapshot.strategy_counts)}</pre>
      </section>
    </div>
  )
}

function ArchiveChartArtifact({ row }: { row: ArchiveRow }) {
  const artifactId = row.chart_artifact_id
  const isProductOwned = artifactId !== null && (!row.chart || isProductOwnedArtifactPath(row.chart))

  if (!isProductOwned) {
    if (!row.chart) return null
    return (
      <section className="subsection">
        <h3>Chart artifact</h3>
        <div className="artifact-preview-panel">
          <div className="artifact-preview-head">
            <ImageIcon size={17} aria-hidden="true" />
            <div>
              <strong>Legacy chart reference</strong>
              <code>{row.chart}</code>
            </div>
          </div>
          <p className="muted artifact-preview-note">This chart path is legacy source material and is not served by the product artifact API.</p>
        </div>
      </section>
    )
  }

  const artifactUrl = artifactFileUrl(artifactId)

  return (
    <section className="subsection">
      <h3>Chart artifact</h3>
      <div className="artifact-preview-panel">
        <div className="artifact-preview-head">
          <ImageIcon size={17} aria-hidden="true" />
          <div>
            <strong>Product chart artifact</strong>
            <code>{row.chart_artifact_id}</code>
          </div>
          <a className="artifact-open-link" href={artifactUrl} target="_blank" rel="noreferrer" aria-label="Open chart artifact">
            <ExternalLink size={15} aria-hidden="true" />
            <span>Open</span>
          </a>
        </div>
        <img className="artifact-preview-image" src={artifactUrl} alt={`${row.code} ${row.strategy} chart`} loading="lazy" />
        {row.chart ? <p className="muted artifact-preview-note">{row.chart}</p> : null}
      </div>
    </section>
  )
}

function MigrationSectionCard({ name, report }: { name: string; report?: LegacyImportSectionReport }) {
  return (
    <div className="migration-section-card">
      <div className="migration-section-head">
        <Database size={17} aria-hidden="true" />
        <h3>{sectionLabel(name)}</h3>
      </div>
      <div className="section-count-grid">
        <DataPair label="Files" value={report ? `${report.files_valid}/${report.files_seen}` : '0/0'} />
        <DataPair label="Records" value={report ? `${report.records_valid}/${report.records_seen}` : '0/0'} />
      </div>
      <pre className="json-block compact">{jsonPreview(report?.by_kind ?? null)}</pre>
    </div>
  )
}

function MigrationIssueList({
  title,
  kind,
  issues,
}: {
  title: string
  kind: 'warning' | 'quarantine'
  issues: LegacyImportIssue[]
}) {
  return (
    <section className="migration-issue-section">
      <div className="migration-issue-heading">
        <h3>{title}</h3>
        <span className={`issue-kind ${kind}`}>{issues.length}</span>
      </div>
      {issues.length === 0 ? (
        <p className="muted migration-issue-empty">No {title.toLowerCase()} found.</p>
      ) : (
        <div className="migration-issue-list">
          {issues.map((issue, index) => (
            <div className="migration-issue-row" key={`${issue.section}-${issue.source_path}-${issue.reason}-${issue.record_key ?? index}`}>
              <span className={`issue-kind ${kind}`}>{issue.section}</span>
              <div>
                <strong>{issue.reason}</strong>
                <p>{issue.message}</p>
                <code>{issue.source_path}</code>
                {issue.record_key ? <small>{issue.record_key}</small> : null}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function ScopeSelector({
  label,
  value,
  onChange,
}: {
  label: string
  value: LegacyImportVerifyScope
  onChange: (value: LegacyImportVerifyScope) => void
}) {
  return (
    <fieldset className="scope-selector">
      <legend>{label}</legend>
      <div className="scope-button-grid">
        {legacyMigrationScopes.map((scope) => (
          <button
            key={scope.value}
            type="button"
            className={scope.value === value ? 'scope-button selected' : 'scope-button'}
            aria-pressed={scope.value === value}
            onClick={() => onChange(scope.value)}
          >
            <strong>{scope.label}</strong>
            <span>{scope.description}</span>
          </button>
        ))}
      </div>
    </fieldset>
  )
}

function ImportSummaryPanel({ summary }: { summary: LegacyImportSummary }) {
  return (
    <div className="migration-result-panel">
      <div className="migration-result-head">
        <span className="status-badge succeeded">
          <CheckCircle2 size={15} aria-hidden="true" />
          Imported
        </span>
        <span className="muted">{summary.pick_date}</span>
      </div>
      <div className="migration-result-grid">
        <DataPair label="Run id" value={summary.run_id} />
        <DataPair label="Source file" value={summary.source_file} />
        <DataPair label="Backup id" value={summary.pre_import_backup_id ?? 'Not created'} />
        <DataPair label="Backup path" value={summary.pre_import_backup_path ?? 'Not created'} />
        <DataPair label="Batch id" value={summary.batch_id ?? 'Not linked'} />
        <DataPair label="Review run" value={summary.review_run_id ?? 'Not linked'} />
        <DataPair label="Archive snapshot" value={summary.archive_snapshot_id ?? 'Not linked'} />
      </div>
      <div className="migration-count-grid" aria-label="Imported record counts">
        <Metric label="Candidates" value={summary.candidates_imported.toString()} />
        <Metric label="Reviews" value={summary.reviews_imported.toString()} />
        <Metric label="Recommendations" value={summary.recommendations_imported.toString()} />
        <Metric label="Archive rows" value={summary.archive_rows_imported.toString()} />
      </div>
      <pre className="json-block compact">{jsonPreview(summary.strategy_counts)}</pre>
    </div>
  )
}

function VerifyResultPanel({ report }: { report: LegacyImportVerifyReport }) {
  const mismatchEntries = [
    { label: 'Missing in SQLite', values: report.mismatches.missing_in_sqlite },
    { label: 'Extra in SQLite', values: report.mismatches.extra_in_sqlite },
    { label: 'Missing in DuckDB', values: report.mismatches.missing_in_duckdb },
    { label: 'Extra in DuckDB', values: report.mismatches.extra_in_duckdb },
  ]
  const mismatchCount = mismatchEntries.reduce((total, entry) => total + entry.values.length, 0)

  return (
    <div className="migration-result-panel">
      <div className="migration-result-head">
        <span className={report.passed ? 'status-badge succeeded' : 'status-badge failed'}>
          {report.passed ? <CheckCircle2 size={15} aria-hidden="true" /> : <XCircle size={15} aria-hidden="true" />}
          {report.passed ? 'Verified' : 'Mismatch'}
        </span>
        <span className="muted">{sectionLabel(report.scope)} / {report.pick_date}</span>
      </div>
      <div className="migration-result-grid">
        <DataPair label="Source path" value={report.source_path} />
        <DataPair label="Run id" value={report.run_id ?? 'Not filtered'} />
        <DataPair label="DuckDB check" value={report.duckdb_checked ? 'Checked' : 'Skipped'} />
      </div>
      <div className="migration-count-grid" aria-label="Verification record counts">
        <Metric label="Legacy" value={report.counts.legacy.toString()} />
        <Metric label="SQLite" value={report.counts.sqlite.toString()} />
        <Metric label="DuckDB" value={report.counts.duckdb === null ? 'Skipped' : report.counts.duckdb.toString()} />
        <Metric label="Mismatches" value={mismatchCount.toString()} />
      </div>
      {mismatchCount === 0 ? (
        <p className="muted migration-issue-empty">No mismatches found.</p>
      ) : (
        <div className="migration-mismatch-list">
          {mismatchEntries
            .filter((entry) => entry.values.length > 0)
            .map((entry) => (
              <div className="migration-mismatch-row" key={entry.label}>
                <strong>{entry.label}</strong>
                <code>{entry.values.join(', ')}</code>
              </div>
            ))}
        </div>
      )}
    </div>
  )
}

function FilterInput({
  label,
  value,
  placeholder,
  type = 'text',
  onChange,
}: {
  label: string
  value: string
  placeholder: string
  type?: 'text' | 'date'
  onChange: (value: string) => void
}) {
  return (
    <label className="filter-field">
      <span>{label}</span>
      <input type={type} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </label>
  )
}

function FilterSelect({
  label,
  value,
  onChange,
}: {
  label: string
  value: RecommendationStatus
  onChange: (value: RecommendationStatus) => void
}) {
  return (
    <label className="filter-field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value as RecommendationStatus)}>
        <option value="all">All reviews</option>
        <option value="recommended">Recommended</option>
        <option value="reviewed">Reviewed only</option>
      </select>
    </label>
  )
}

function ArchiveStatusSelect({
  label,
  value,
  onChange,
}: {
  label: string
  value: ArchiveStatus
  onChange: (value: ArchiveStatus) => void
}) {
  return (
    <label className="filter-field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value as ArchiveStatus)}>
        <option value="all">All rows</option>
        <option value="recommended">Recommended</option>
        <option value="reviewed">Reviewed</option>
        <option value="unreviewed">Unreviewed</option>
      </select>
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

function CandidateCell({
  label,
  value,
  mono = false,
  strong = false,
  wide = false,
  extra = false,
}: {
  label: string
  value: string
  mono?: boolean
  strong?: boolean
  wide?: boolean
  extra?: boolean
}) {
  const className = ['candidate-cell', mono ? 'mono' : '', wide ? 'wide' : '', extra ? 'extra' : ''].filter(Boolean).join(' ')
  return (
    <span className={className} title={value}>
      <span>{label}</span>
      <strong className={strong ? 'numeric' : undefined}>{value}</strong>
    </span>
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

function formatRank(review: Review) {
  return review.recommendation ? `#${review.recommendation.rank}` : 'No rank'
}

function reviewerName(review: Review) {
  return review.reviewer || review.review_run.provider || 'Not set'
}

function findActiveSnapshot(snapshots: ArchiveSnapshot[], pickDate: string, runId: string) {
  if (!pickDate) return undefined
  return snapshots.find((snapshot) => snapshot.pick_date === pickDate && (!runId || snapshot.run_id === runId))
    ?? snapshots.find((snapshot) => snapshot.pick_date === pickDate)
}

function verdictClass(verdict: string | null) {
  const normalized = verdict?.toLowerCase()
  if (normalized === 'pass') return 'pass'
  if (normalized === 'fail') return 'fail'
  if (normalized === 'watch') return 'watch'
  return 'neutral'
}

function archiveStatusLabel(status: ArchiveRow['status']) {
  if (status === 'recommended') return 'Recommended'
  if (status === 'reviewed') return 'Reviewed'
  return 'Unreviewed'
}

function archiveStatusClass(status: ArchiveRow['status']) {
  if (status === 'recommended') return 'recommended'
  if (status === 'reviewed') return 'reviewed'
  return 'unreviewed'
}

function formatArchiveRank(row: ArchiveRow) {
  return row.rank ? `#${row.rank}` : 'No rank'
}

function sectionLabel(section: string) {
  if (section === 'candidates') return 'Candidates'
  if (section === 'reviews') return 'Reviews'
  if (section === 'history') return 'History'
  return section
}

function emptyCandidateFilters(): Required<CandidateFilters> {
  return {
    batch_id: '',
    pick_date: '',
    run_id: '',
    strategy: '',
    code: '',
  }
}

function candidateFiltersFromParams(params: URLSearchParams): Required<CandidateFilters> {
  return {
    batch_id: paramValue(params, 'batch_id'),
    pick_date: paramValue(params, 'pick_date'),
    run_id: paramValue(params, 'run_id'),
    strategy: paramValue(params, 'strategy'),
    code: paramValue(params, 'code'),
  }
}

function candidateFiltersToParams(filters: Required<CandidateFilters>) {
  return nonEmptyParams({
    batch_id: filters.batch_id,
    pick_date: filters.pick_date,
    run_id: filters.run_id,
    strategy: filters.strategy,
    code: filters.code,
  })
}

function emptyReviewFilters(): Required<ReviewFilters> {
  return {
    pick_date: '',
    run_id: '',
    review_run_id: '',
    candidate_batch_id: '',
    strategy: '',
    code: '',
    review_key: '',
    reviewer: '',
    recommendation_status: 'all',
  }
}

function reviewFiltersFromParams(params: URLSearchParams): Required<ReviewFilters> {
  return {
    pick_date: paramValue(params, 'pick_date'),
    run_id: paramValue(params, 'run_id'),
    review_run_id: paramValue(params, 'review_run_id'),
    candidate_batch_id: paramValue(params, 'candidate_batch_id'),
    strategy: paramValue(params, 'strategy'),
    code: paramValue(params, 'code'),
    review_key: paramValue(params, 'review_key'),
    reviewer: paramValue(params, 'reviewer'),
    recommendation_status: recommendationStatusValue(params.get('recommendation_status')),
  }
}

function reviewFiltersToParams(filters: Required<ReviewFilters>) {
  const params = nonEmptyParams({
    pick_date: filters.pick_date,
    run_id: filters.run_id,
    review_run_id: filters.review_run_id,
    candidate_batch_id: filters.candidate_batch_id,
    strategy: filters.strategy,
    code: filters.code,
    review_key: filters.review_key,
    reviewer: filters.reviewer,
  })
  if (filters.recommendation_status !== 'all') {
    params.set('recommendation_status', filters.recommendation_status)
  }
  return params
}

function emptyArchiveFilters(): Required<ArchiveRowFilters> {
  return {
    pick_date: '',
    run_id: '',
    strategy: '',
    code: '',
    review_key: '',
    status: 'all',
    rank: '',
  }
}

function archiveFiltersFromParams(params: URLSearchParams): Required<ArchiveRowFilters> {
  return {
    pick_date: paramValue(params, 'pick_date'),
    run_id: paramValue(params, 'run_id'),
    strategy: paramValue(params, 'strategy'),
    code: paramValue(params, 'code'),
    review_key: paramValue(params, 'review_key'),
    status: archiveStatusValue(params.get('status')),
    rank: paramValue(params, 'rank'),
  }
}

function archiveFiltersToParams(filters: Required<ArchiveRowFilters>) {
  const params = nonEmptyParams({
    pick_date: filters.pick_date,
    run_id: filters.run_id,
    strategy: filters.strategy,
    code: filters.code,
    review_key: filters.review_key,
    rank: filters.rank,
  })
  if (filters.status !== 'all') {
    params.set('status', filters.status)
  }
  return params
}

function paramValue(params: URLSearchParams, key: string) {
  return params.get(key)?.trim() ?? ''
}

function nonEmptyParams(values: Record<string, string>) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    const trimmed = value.trim()
    if (trimmed) params.set(key, trimmed)
  }
  return params
}

function recommendationStatusValue(value: string | null): RecommendationStatus {
  if (value === 'recommended' || value === 'reviewed') return value
  return 'all'
}

function archiveStatusValue(value: string | null): ArchiveStatus {
  if (value === 'recommended' || value === 'reviewed' || value === 'unreviewed') return value
  return 'all'
}

function compactPreselectRequest(form: Record<keyof PreselectRunRequest, string>): PreselectRunRequest {
  return Object.fromEntries(Object.entries(form).filter(([, value]) => value.trim()).map(([key, value]) => [key, value.trim()]))
}

function isProductOwnedArtifactPath(path: string | null) {
  if (!path?.trim()) return false
  const normalized = path.replaceAll('\\', '/')
  return normalized !== 'data' && !normalized.startsWith('data/')
}

function jsonPreview(value: Record<string, unknown> | null) {
  if (!value || Object.keys(value).length === 0) return '{}'
  return JSON.stringify(value, null, 2)
}

function jsonInline(value: Record<string, unknown> | null) {
  if (!value || Object.keys(value).length === 0) return '{}'
  return `keys: ${Object.keys(value).join(', ')}`
}

function errorText(error: unknown) {
  if (error instanceof Error) return error.message
  return 'Unable to reach the API'
}

export default App
