"""
agy_cli_review.py
~~~~~~~~~~~~~~~~~
使用本机 Antigravity CLI（agy）对候选股票进行实验性图表复评。

该路径只用于迁移探索：
- 默认不替换 gemini-cli
- 不覆盖正式 data/review/{pick_date}/{code}.json
- 结果写入 data/review/agy_cli_experimental/{pick_date}
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from base_reviewer import BaseReviewer

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _ROOT / "config" / "agy_cli_review.yaml"

DEFAULT_CONFIG: dict[str, Any] = {
    "candidates": "data/candidates/candidates_latest.json",
    "kline_dir": "data/kline",
    "output_dir": "data/review/agy_cli_experimental",
    "prompt_path": "agent/prompt.md",
    "agy_bin": "agy",
    "model": "Gemini 3.5 Flash (Medium)",
    "print_timeout": "10m",
    "timeout_seconds": 900,
    "request_delay": 10,
    "max_items": 1,
    "skip_existing": True,
    "suggest_min_score": 4.0,
    "save_raw_cli_io": True,
    "raw_log_dir": "",
    "json_repair_enabled": True,
    "json_repair_prompt_max_chars": 12000,
    "settings_path": "~/.gemini/antigravity-cli/settings.json",
    "expected_model_label": "",
    "fail_on_model_mismatch": False,
    "classic_pattern_enabled": True,
}

REQUIRED_TEXT_FIELDS: tuple[str, ...] = (
    "trend_reasoning",
    "position_reasoning",
    "volume_reasoning",
    "abnormal_move_reasoning",
    "signal_reasoning",
    "classic_pattern_type",
    "classic_pattern_reasoning",
    "signal_type",
    "verdict",
    "comment",
)
REQUIRED_SCORE_FIELDS: tuple[str, ...] = (
    "trend_structure",
    "price_position",
    "volume_behavior",
    "previous_abnormal_move",
    "classic_pattern_match",
)
VALID_VERDICTS = {"PASS", "WATCH", "FAIL"}

JSON_OUTPUT_CONTRACT = """
输出契约：
1. 只能输出一个 JSON 对象，必须能被 Python json.loads 直接解析。
2. 不要输出 Markdown 代码块、解释文字、前后缀、注释或多余字段说明。
3. 必须包含 trend_reasoning、position_reasoning、volume_reasoning、abnormal_move_reasoning、signal_reasoning、classic_pattern_type、classic_pattern_reasoning、scores、total_score、signal_type、verdict、comment。
4. scores 必须包含 trend_structure、price_position、volume_behavior、previous_abnormal_move、classic_pattern_match，且分数必须是 0 到 5 的数字。
5. verdict 只能是 PASS、WATCH 或 FAIL。
""".strip()

JSON_SCHEMA_EXAMPLE = """
{
  "trend_reasoning": "string",
  "position_reasoning": "string",
  "volume_reasoning": "string",
  "abnormal_move_reasoning": "string",
  "signal_reasoning": "string",
  "classic_pattern_type": "string",
  "classic_pattern_reasoning": "string",
  "scores": {
    "trend_structure": 1,
    "price_position": 1,
    "volume_behavior": 1,
    "previous_abnormal_move": 1,
    "classic_pattern_match": 1
  },
  "total_score": 1.0,
  "signal_type": "string",
  "verdict": "WATCH",
  "comment": "一句中文交易员点评"
}
""".strip()


class AgyCliError(RuntimeError):
    pass


class AgyCliJsonContractError(AgyCliError):
    pass


def _resolve_cfg_path(path_like: str | Path, base_dir: Path = _ROOT) -> Path:
    p = Path(path_like).expanduser()
    return p if p.is_absolute() else (base_dir / p)


def _load_yaml_config(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    config = {**DEFAULT_CONFIG, **loaded}
    for key in ("candidates", "kline_dir", "output_dir", "prompt_path"):
        config[key] = _resolve_cfg_path(config[key])
    config["settings_path"] = _resolve_cfg_path(config["settings_path"], base_dir=Path.home())
    config["json_repair_enabled"] = bool(config.get("json_repair_enabled", True))
    config["json_repair_prompt_max_chars"] = int(config.get("json_repair_prompt_max_chars", 12000))
    return config


def _read_model_evidence(settings_path: Path, configured_model: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "source": str(settings_path),
        "model": configured_model,
        "available": bool(configured_model),
        "control": "per-call --model",
        "note": "AGY 1.0.5 supports per-call --model; settings model is recorded only as secondary evidence.",
    }
    if not settings_path.exists():
        return evidence
    try:
        parsed = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - model evidence should not block unless configured
        evidence["error"] = str(exc)
        return evidence
    settings_model = parsed.get("model")
    if isinstance(settings_model, str) and settings_model.strip():
        evidence["settings_model"] = settings_model.strip()
    return evidence


class AgyCliReviewer(BaseReviewer):
    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.agy_bin = str(config.get("agy_bin", "agy"))
        self.model = str(config.get("model") or config.get("expected_model_label") or "").strip()
        self.print_timeout = str(config.get("print_timeout", "10m"))
        self.timeout_seconds = int(config.get("timeout_seconds", 900))
        self.cli_call_index = 0
        self.raw_log_root: Path | None = None
        self.model_evidence = _read_model_evidence(Path(config["settings_path"]), self.model)
        self._validate_agy_bin()
        self._validate_model_expectation()

    def _validate_agy_bin(self) -> None:
        if os.sep in self.agy_bin:
            if not Path(self.agy_bin).exists():
                raise FileNotFoundError(f"找不到 agy CLI：{self.agy_bin}")
            return
        if shutil.which(self.agy_bin) is None:
            raise FileNotFoundError(f"找不到 agy CLI：{self.agy_bin}")

    def _validate_model_expectation(self) -> None:
        expected = str(self.config.get("expected_model_label") or "").strip()
        actual = self.model
        if expected and expected != actual:
            message = f"AGY 本次调用模型为 {actual or '默认模型'}，期望为 {expected}"
            if self.config.get("fail_on_model_mismatch", False):
                raise AgyCliError(message)
            print(f"[WARN] {message}；本次仍按实验模式继续。")

    @staticmethod
    def _safe_log_name(code: str) -> str:
        return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in code)[:80]

    def _next_raw_call_dir(self, code: str) -> Path | None:
        if not self.config.get("save_raw_cli_io", True) or self.raw_log_root is None:
            return None
        self.cli_call_index += 1
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        call_dir = self.raw_log_root / f"{timestamp}_{self.cli_call_index:04d}_{self._safe_log_name(code)}"
        call_dir.mkdir(parents=True, exist_ok=True)
        return call_dir

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        half = max(1, max_chars // 2)
        return (
            text[:half]
            + "\n\n...[TRUNCATED_FOR_JSON_REPAIR]...\n\n"
            + text[-half:]
        )

    @staticmethod
    def _chart_ref_for_agy(source: Path) -> str:
        return f"@{source.resolve().as_posix()}"

    def _build_prompt(self, *, code: str, day_chart: Path, prompt: str, strategy: str = "") -> str:
        chart_ref = self._chart_ref_for_agy(day_chart)
        strategy_line = f"候选策略：{strategy}\n" if strategy else ""
        return (
            f"{prompt}\n\n"
            "---\n\n"
            f"股票代码：{code}\n"
            f"{strategy_line}"
            f"日线图：{chart_ref}\n\n"
            "请读取上面的日线图，严格按照评分规则完成复评。"
            f"\n\n{JSON_OUTPUT_CONTRACT}"
        )

    def _build_command(self, day_chart: Path, prompt_text: str) -> list[str]:
        cmd = [
            self.agy_bin,
            "--add-dir",
            str(day_chart.parent.resolve()),
            "--print-timeout",
            self.print_timeout,
            "--print",
            prompt_text,
        ]
        if self.model:
            cmd[1:1] = ["--model", self.model]
        return cmd

    def _run_agy(
        self,
        *,
        code: str,
        day_chart: Path,
        prompt_text: str,
        purpose: str = "review",
    ) -> subprocess.CompletedProcess[str]:
        raw_dir = self._next_raw_call_dir(f"{code}_{purpose}")
        cmd = self._build_command(day_chart, prompt_text)
        started_at = datetime.now()
        started_monotonic = time.monotonic()

        if raw_dir is not None:
            self._write_text(raw_dir / "prompt.txt", prompt_text)
            self._write_json(
                raw_dir / "meta.json",
                {
                    "status": "running",
                    "started_at": started_at.isoformat(timespec="seconds"),
                    "command": cmd,
                    "code": code,
                    "purpose": purpose,
                    "image_path": str(day_chart),
                    "print_timeout": self.print_timeout,
                    "timeout_seconds": self.timeout_seconds,
                    "model": self.model,
                    "model_evidence": self.model_evidence,
                },
            )

        model_part = f" --model {self.model!r}" if self.model else ""
        print(f"[Command] AGY CLI 实际命令: {self.agy_bin}{model_part} --add-dir {day_chart.parent} --print-timeout {self.print_timeout} --print <prompt>")
        print(f"[INFO] AGY model: {self.model or '(agy default)'}")
        if raw_dir is not None:
            print(f"[INFO] AGY CLI raw log: {raw_dir}")

        with tempfile.TemporaryDirectory(prefix="stocktradebyz-agy-review-") as tmp:
            result = subprocess.run(
                cmd,
                cwd=tmp,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                env={**os.environ, "NO_COLOR": "1"},
            )

        if raw_dir is not None:
            self._write_text(raw_dir / "stdout.txt", result.stdout or "")
            self._write_text(raw_dir / "stderr.txt", result.stderr or "")
            self._write_json(
                raw_dir / "meta.json",
                {
                    "status": "finished",
                    "started_at": started_at.isoformat(timespec="seconds"),
                    "ended_at": datetime.now().isoformat(timespec="seconds"),
                    "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
                    "command": cmd,
                    "code": code,
                    "purpose": purpose,
                    "image_path": str(day_chart),
                    "exit_code": result.returncode,
                    "model": self.model,
                    "print_timeout": self.print_timeout,
                    "timeout_seconds": self.timeout_seconds,
                    "model_evidence": self.model_evidence,
                },
            )
        return result

    @staticmethod
    def _numeric_score(value: Any) -> float | None:
        return BaseReviewer._numeric_score(value)

    def _validate_review_payload(self, payload: dict[str, Any], *, code: str, strategy: str) -> None:
        errors: list[str] = []
        for field in REQUIRED_TEXT_FIELDS:
            value = payload.get(field)
            if not isinstance(value, str):
                errors.append(f"{field} 缺失或不是字符串")

        scores = payload.get("scores")
        if not isinstance(scores, dict):
            errors.append("scores 缺失或不是对象")
        else:
            for field in REQUIRED_SCORE_FIELDS:
                if field not in scores:
                    errors.append(f"scores.{field} 缺失")
                    continue
                if self._numeric_score(scores.get(field)) is None:
                    errors.append(f"scores.{field} 不是 0 到 5 的数字")

        if self._numeric_score(payload.get("total_score")) is None:
            errors.append("total_score 缺失或不是 0 到 5 的数字")

        verdict = str(payload.get("verdict") or "").strip().upper()
        if verdict not in VALID_VERDICTS:
            errors.append("verdict 必须是 PASS、WATCH 或 FAIL")

        payload_code = str(payload.get("code") or "").strip()
        if payload_code and payload_code != code:
            errors.append(f"code 不匹配：输出 {payload_code}，期望 {code}")

        payload_strategy = str(payload.get("strategy") or "").strip()
        if payload_strategy and strategy and payload_strategy != strategy:
            errors.append(f"strategy 不匹配：输出 {payload_strategy}，期望 {strategy}")

        if errors:
            raise AgyCliJsonContractError("; ".join(errors))

    def _parse_review_payload(self, text: str, *, code: str, strategy: str) -> dict[str, Any]:
        try:
            parsed = self.extract_json(text)
        except Exception as exc:  # noqa: BLE001 - turn parser detail into reviewer contract error
            raise AgyCliJsonContractError(f"无法从 AGY 输出提取合法 JSON：{exc}") from exc
        self._validate_review_payload(parsed, code=code, strategy=strategy)
        return parsed

    def _build_json_repair_prompt(
        self,
        *,
        code: str,
        strategy: str,
        original_output: str,
        error: Exception,
    ) -> str:
        max_chars = int(self.config.get("json_repair_prompt_max_chars", 12000))
        clipped_output = self._truncate_text(original_output, max_chars)
        strategy_line = f"候选策略：{strategy}\n" if strategy else ""
        return (
            "你上一次输出没有通过本地 JSON 契约校验。"
            "不要重新分析图表，不要改变已有交易判断；只把上一段输出修复为一个合法 JSON 对象。\n\n"
            f"股票代码：{code}\n"
            f"{strategy_line}"
            f"校验错误：{error}\n\n"
            f"{JSON_OUTPUT_CONTRACT}\n\n"
            "必须使用以下 JSON 形状，保留原输出中已有的判断和分数；"
            "如果某个必需字段确实缺失，用保守中文说明补齐，不要添加解释文字。\n\n"
            f"{JSON_SCHEMA_EXAMPLE}\n\n"
            "上一段原始输出如下：\n"
            "```text\n"
            f"{clipped_output}\n"
            "```"
        )

    def review_stock(self, code: str, day_chart: Path, prompt: str, strategy: str = "") -> dict:
        prompt_text = self._build_prompt(
            code=code,
            day_chart=day_chart,
            prompt=prompt,
            strategy=strategy,
        )
        result = self._run_agy(code=code, day_chart=day_chart, prompt_text=prompt_text, purpose="review")
        combined_output = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode != 0:
            raise AgyCliError(f"AGY CLI 退出码 {result.returncode}: {combined_output[:1200]}")

        repair_attempted = False
        repair_used = False
        repair_reason = ""
        try:
            parsed = self._parse_review_payload(result.stdout, code=code, strategy=strategy)
        except AgyCliJsonContractError as exc:
            repair_reason = str(exc)
            if not self.config.get("json_repair_enabled", True):
                raise
            repair_attempted = True
            repair_prompt = self._build_json_repair_prompt(
                code=code,
                strategy=strategy,
                original_output=result.stdout,
                error=exc,
            )
            repair_result = self._run_agy(
                code=code,
                day_chart=day_chart,
                prompt_text=repair_prompt,
                purpose="json_repair",
            )
            repair_output = f"{repair_result.stdout}\n{repair_result.stderr}".strip()
            if repair_result.returncode != 0:
                raise AgyCliError(f"AGY JSON 修复调用退出码 {repair_result.returncode}: {repair_output[:1200]}")
            try:
                parsed = self._parse_review_payload(repair_result.stdout, code=code, strategy=strategy)
            except AgyCliJsonContractError as repair_exc:
                raise AgyCliError(
                    "AGY 输出 JSON 修复失败："
                    f"初次错误：{repair_reason}；修复后错误：{repair_exc}"
                ) from repair_exc
            repair_used = True

        parsed["code"] = code
        parsed["strategy"] = strategy or parsed.get("strategy", "")
        parsed["reviewer"] = "agy-cli-experimental"
        parsed["model"] = self.model
        parsed["model_evidence"] = self.model_evidence
        parsed["json_output_mode"] = "prompt-json"
        parsed["json_schema_valid"] = True
        parsed["json_repair_attempted"] = repair_attempted
        parsed["json_repair_used"] = repair_used
        if repair_reason:
            parsed["json_repair_reason"] = repair_reason[:500]
        return parsed

    def run(self) -> None:
        candidates_data = self.load_candidates(Path(self.config["candidates"]))
        pick_date: str = candidates_data["pick_date"]
        candidates: list[dict[str, Any]] = self.order_candidates_for_review(candidates_data)
        max_items = self.config.get("max_items")
        if max_items is not None:
            candidates = candidates[: int(max_items)]
        print(f"[INFO] pick_date={pick_date}，AGY 实验复评股票数={len(candidates)}")

        out_dir = self.output_dir / pick_date
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_log_dir = str(self.config.get("raw_log_dir") or "").strip()
        self.raw_log_root = Path(raw_log_dir) if raw_log_dir else out_dir / "agy_cli_runs"
        if self.config.get("save_raw_cli_io", True):
            self.raw_log_root.mkdir(parents=True, exist_ok=True)
            print(f"[INFO] AGY CLI raw logs: {self.raw_log_root}")

        all_results: list[dict[str, Any]] = []
        failed_codes: list[str] = []
        for i, candidate in enumerate(candidates, 1):
            code = str(candidate["code"])
            strategy = str(candidate.get("strategy") or "")
            review_key = self.review_key(code, strategy)
            out_file = self.review_file(out_dir, code, strategy)
            if self.config.get("skip_existing", False) and out_file.exists():
                print(f"[{i}/{len(candidates)}] {review_key} — 已存在，跳过。")
                with open(out_file, encoding="utf-8") as f:
                    result = json.load(f)
                result = self.normalize_scores(result, self.config)
                all_results.append(result)
                continue

            day_chart = self.find_chart_images(pick_date, code)
            if day_chart is None:
                print(f"[{i}/{len(candidates)}] {review_key} — 缺少日线图，跳过。")
                failed_codes.append(review_key)
                continue

            print(f"[{i}/{len(candidates)}] {review_key} — AGY 正在分析 ...", end=" ", flush=True)
            try:
                result = self.review_stock(
                    code=code,
                    day_chart=day_chart,
                    prompt=self.prompt,
                    strategy=strategy,
                )
                result["strategy"] = strategy or result.get("strategy", "")
                result["review_key"] = review_key
                result = self.normalize_scores(result, self.config)
                self._write_json(out_file, result)
                all_results.append(result)
                print(f"完成 — verdict={result.get('verdict', '?')}, score={result.get('total_score', '?')}")
            except Exception as exc:  # noqa: BLE001 - collect failed code and continue
                print(f"失败 — {exc}")
                failed_codes.append(review_key)

            if i < len(candidates):
                time.sleep(float(self.config.get("request_delay", 10)))

        print(f"\n[INFO] AGY 实验复评完成：成功 {len(all_results)} 支，失败/跳过 {len(failed_codes)} 支")
        if failed_codes:
            print(f"[WARN] 未处理股票：{failed_codes}")
        if not all_results:
            print("[ERROR] 没有可用的 AGY 实验复评结果，跳过汇总。")
            return

        suggestion = self.generate_suggestion(
            pick_date=pick_date,
            all_results=all_results,
            min_score=float(self.config.get("suggest_min_score", 4.0)),
            candidates=candidates,
        )
        suggestion["reviewer"] = "agy-cli-experimental"
        suggestion["model"] = self.model
        suggestion["model_evidence"] = self.model_evidence
        suggestion["review_complete"] = not failed_codes
        suggestion["pending"] = failed_codes
        suggestion_file = out_dir / "suggestion.json"
        self._write_json(suggestion_file, suggestion)
        print(f"[INFO] AGY 实验汇总已写入: {suggestion_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="AGY CLI 实验图表复评")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG_PATH), help="配置文件路径")
    parser.add_argument("--limit", type=int, default=None, help="覆盖配置中的 max_items")
    parser.add_argument("--candidates", default="", help="覆盖候选列表 JSON")
    parser.add_argument("--kline-dir", default="", help="覆盖 K 线图目录")
    parser.add_argument("--output-dir", default="", help="覆盖实验输出目录")
    parser.add_argument("--model", default="", help="覆盖配置中的 agy --model")
    args = parser.parse_args()

    config = _load_yaml_config(Path(args.config))
    if args.limit is not None:
        config["max_items"] = args.limit
    if args.candidates:
        config["candidates"] = _resolve_cfg_path(args.candidates)
    if args.kline_dir:
        config["kline_dir"] = _resolve_cfg_path(args.kline_dir)
    if args.output_dir:
        config["output_dir"] = _resolve_cfg_path(args.output_dir)
    if args.model:
        config["model"] = args.model

    reviewer = AgyCliReviewer(config)
    reviewer.run()


if __name__ == "__main__":
    main()
