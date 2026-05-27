"""
gemini_review.py
~~~~~~~~~~~~~~~~
使用 Google Gemini 对候选股票进行图表分析评分。
继承自 BaseReviewer 基础架构。

用法：
    python agent/gemini_review.py
    python agent/gemini_review.py --config config/gemini_review.yaml

配置：
    默认读取 config/gemini_review.yaml。

环境变量：
    GEMINI_API_KEY  —— Google Gemini API Key（必填）

输出：
    ./data/review/{pick_date}/{code}_{strategy}.json   每个候选的评分 JSON
    ./data/review/{pick_date}/suggestion.json  汇总推荐建议
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from base_reviewer import BaseReviewer

# ────────────────────────────────────────────────
# 配置加载
# ────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _ROOT / "config" / "gemini_review.yaml"
sys.path.insert(0, str(_ROOT))

from legacy_compat import (  # noqa: E402
    LEGACY_GEMINI_API_REVIEW_ENV,
    LEGACY_GEMINI_API_REVIEW_RETIRED_NOTICE,
    legacy_gemini_api_review_enabled,
    print_legacy_write_freeze_notice,
)

DEFAULT_CONFIG: dict[str, Any] = {
    # 路径参数（相对路径默认基于项目根目录）
    "candidates": "data/candidates/candidates_latest.json",
    "kline_dir": "data/kline",
    "output_dir": "data/review",
    "prompt_path": "agent/prompt.md",
    # Gemini 模型参数
    "model": "gemini-3.1-pro-preview",
    "request_delay": 5,
    "skip_existing": False,
    "suggest_min_score": 4.0,
    "classic_pattern_enabled": True,
}

genai: Any = None
types: Any = None


def _load_gemini_sdk() -> None:
    """Load the Gemini SDK only when the rollback-only legacy reviewer runs."""
    global genai, types
    if genai is not None and types is not None:
        return

    from google import genai as _genai
    from google.genai import types as _types

    genai = _genai
    types = _types


def _resolve_cfg_path(path_like: str | Path, base_dir: Path = _ROOT) -> Path:
    p = Path(path_like)
    return p if p.is_absolute() else (base_dir / p)


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    cfg_path = config_path or _DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"找不到配置文件：{cfg_path}")

    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = {**DEFAULT_CONFIG, **raw}

    # BaseReviewer 依赖这些路径字段为 Path 对象
    cfg["candidates"] = _resolve_cfg_path(cfg["candidates"])
    cfg["kline_dir"] = _resolve_cfg_path(cfg["kline_dir"])
    cfg["output_dir"] = _resolve_cfg_path(cfg["output_dir"])
    cfg["prompt_path"] = _resolve_cfg_path(cfg["prompt_path"])

    return cfg


class GeminiReviewer(BaseReviewer):
    def __init__(self, config):
        super().__init__(config)
        _load_gemini_sdk()
        
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            print("[ERROR] 未找到环境变量 GEMINI_API_KEY，请先设置后重试。", file=sys.stderr)
            sys.exit(1)
            
        self.client = genai.Client(api_key=api_key)

    @staticmethod
    def image_to_part(path: Path) -> types.Part:
        """将图片文件转为 Gemini Part 对象。"""
        suffix = path.suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
        mime_type = mime_map.get(suffix, "image/jpeg")
        data = path.read_bytes()
        return types.Part.from_bytes(data=data, mime_type=mime_type)

    def review_stock(self, code: str, day_chart: Path, prompt: str, strategy: str = "") -> dict:
        """
        调用 Gemini API，对单支股票进行图表分析，返回解析后的 JSON 结果。
        """
        strategy_line = f"来源策略：{strategy}\n" if strategy else ""
        user_text = (
            f"股票代码：{code}\n"
            f"{strategy_line}\n"
            "以下是该股票的 **日线图**，请按照系统提示中的框架进行分析，"
            "并严格按照要求输出 JSON。"
        )

        parts: list[types.Part] = [
            types.Part.from_text(text="【日线图】"),
            self.image_to_part(day_chart),
            types.Part.from_text(text=user_text),
        ]

        response = self.client.models.generate_content(
            model=self.config.get("model", "gemini-3.1-pro-preview"),
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                system_instruction=prompt,
                temperature=0.2,
            ),
        )

        response_text = response.text
        if response_text is None:
            raise RuntimeError(f"Gemini 返回空响应，无法解析 JSON（code={code}）")

        result = self.extract_json(response_text)
        result["code"] = code  # 附加股票代码便于追溯
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Gemini 图表复评")
    parser.add_argument(
        "--config",
        default=str(_DEFAULT_CONFIG_PATH),
        help="配置文件路径（默认 config/gemini_review.yaml）",
    )
    args = parser.parse_args()

    if not legacy_gemini_api_review_enabled():
        print(LEGACY_GEMINI_API_REVIEW_RETIRED_NOTICE, file=sys.stderr)
        print(
            "Use POST /api/runs/review/provider for product-owned review workflows. "
            f"Temporary rollback: set {LEGACY_GEMINI_API_REVIEW_ENV}=1.",
            file=sys.stderr,
        )
        return 2

    config = load_config(Path(args.config))
    print_legacy_write_freeze_notice(
        surface="agent.gemini_review",
        replacement="POST /api/runs/review/provider",
        writes="data/review",
    )
    reviewer = GeminiReviewer(config)
    reviewer.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
