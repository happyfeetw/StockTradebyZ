export type WorkbenchWorkflowModeId =
  | 'full'
  | 'skip-fetch'
  | 'preselect-and-charts'
  | 'fetch-only'
  | 'preselect-only'
  | 'charts-only'
  | 'review-only'

export type WorkbenchWorkflowStepId = 'market-data' | 'preselect' | 'chart-export' | 'review' | 'archive'

export interface WorkbenchWorkflowMode {
  id: WorkbenchWorkflowModeId
  label: string
  description: string
  steps: readonly WorkbenchWorkflowStepId[]
}

export interface WorkbenchWorkflowStepDefinition {
  id: WorkbenchWorkflowStepId
  label: string
  command: string
}

export const workbenchWorkflowSteps: Record<WorkbenchWorkflowStepId, WorkbenchWorkflowStepDefinition> = {
  'market-data': {
    id: 'market-data',
    label: '拉取 K 线数据',
    command: 'python -m pipeline.fetch_kline --config <run>/fetch_kline.yaml',
  },
  preselect: {
    id: 'preselect',
    label: '量化初选',
    command: 'python -m pipeline.cli preselect --config <run>/rules_preselect.yaml --merge-same-date',
  },
  'chart-export': {
    id: 'chart-export',
    label: '导出候选图表',
    command: 'python dashboard/export_kline_charts.py',
  },
  review: {
    id: 'review',
    label: 'Gemini CLI 复评',
    command: 'python agent/gemini_cli_review.py --config <run>/gemini_cli_review.yaml',
  },
  archive: {
    id: 'archive',
    label: '归档当日结果',
    command: 'python -m pipeline.archive_results --run-id <run>',
  },
}

export const workbenchWorkflowModes: readonly WorkbenchWorkflowMode[] = [
  {
    id: 'full',
    label: '完整流程',
    description: '拉取数据、初选、导出图表、复评并归档。',
    steps: ['market-data', 'preselect', 'chart-export', 'review', 'archive'],
  },
  {
    id: 'skip-fetch',
    label: '跳过抓取',
    description: '使用本地已有日线数据，执行初选、图表、复评和归档。',
    steps: ['preselect', 'chart-export', 'review', 'archive'],
  },
  {
    id: 'preselect-and-charts',
    label: '初选+导出图表',
    description: '只生成候选批次并导出候选图表。',
    steps: ['preselect', 'chart-export'],
  },
  {
    id: 'fetch-only',
    label: '只抓取数据',
    description: '只拉取 K 线数据。',
    steps: ['market-data'],
  },
  {
    id: 'preselect-only',
    label: '只跑初选',
    description: '只生成候选批次。',
    steps: ['preselect'],
  },
  {
    id: 'charts-only',
    label: '只导出图表',
    description: '对当前候选批次导出图表。',
    steps: ['chart-export'],
  },
  {
    id: 'review-only',
    label: '只跑复评',
    description: '对当前候选批次复评，并按旧 Workbench 默认行为归档。',
    steps: ['review', 'archive'],
  },
]

export function workbenchWorkflowModeById(modeId: WorkbenchWorkflowModeId): WorkbenchWorkflowMode {
  return workbenchWorkflowModes.find((mode) => mode.id === modeId) ?? workbenchWorkflowModes[1]
}

export function stepsForWorkbenchWorkflowMode(modeId: WorkbenchWorkflowModeId): readonly WorkbenchWorkflowStepId[] {
  return workbenchWorkflowModeById(modeId).steps
}
