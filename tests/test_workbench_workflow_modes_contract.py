from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_SOURCE = ROOT / "apps" / "web" / "src" / "features" / "app" / "workbenchWorkflow.ts"


def _mode_contract() -> list[tuple[str, str, list[str]]]:
    text = WORKFLOW_SOURCE.read_text(encoding="utf-8")
    mode_source = text.split("export const workbenchWorkflowModes", 1)[1]
    mode_source = mode_source.split("export function workbenchWorkflowModeById", 1)[0]
    pattern = re.compile(
        r"id: '([^']+)',\s+label: '([^']+)'.*?steps: \[([^\]]*)\]",
        re.S,
    )
    matches = pattern.findall(mode_source)
    return [
        (
            mode_id,
            label,
            re.findall(r"'([^']+)'", steps),
        )
        for mode_id, label, steps in matches
    ]


def test_workbench_run_modes_match_legacy_workbench_order() -> None:
    assert _mode_contract() == [
        ("full", "完整流程", ["market-data", "preselect", "chart-export", "review", "archive"]),
        ("skip-fetch", "跳过抓取", ["preselect", "chart-export", "review", "archive"]),
        ("preselect-and-charts", "初选+导出图表", ["preselect", "chart-export"]),
        ("fetch-only", "只抓取数据", ["market-data"]),
        ("preselect-only", "只跑初选", ["preselect"]),
        ("charts-only", "只导出图表", ["chart-export"]),
        ("review-only", "只跑复评", ["review", "archive"]),
    ]


def test_workbench_step_labels_and_legacy_commands_are_visible() -> None:
    text = WORKFLOW_SOURCE.read_text(encoding="utf-8")
    expected_fragments = [
        "拉取 K 线数据",
        "python -m pipeline.fetch_kline --config <run>/fetch_kline.yaml",
        "量化初选",
        "python -m pipeline.cli preselect --config <run>/rules_preselect.yaml --merge-same-date",
        "导出候选图表",
        "python dashboard/export_kline_charts.py",
        "Gemini CLI 复评",
        "python agent/gemini_cli_review.py --config <run>/gemini_cli_review.yaml",
        "归档当日结果",
        "python -m pipeline.archive_results --run-id <run>",
    ]
    for fragment in expected_fragments:
        assert fragment in text
