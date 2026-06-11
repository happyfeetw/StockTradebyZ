"""
codex_cli_review.py
~~~~~~~~~~~~~~~~~~~
使用 Codex CLI 非交互模式对候选股票进行图表复评。

Codex reviewer 固定为 GPT-5.5、高思考强度、标准速度路径。它只改变复评工具与模型，
提示词、评分字段和归一化规则沿用现有 reviewer 契约。
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
_DEFAULT_CONFIG_PATH = _ROOT / "config" / "codex_cli_review.yaml"

REVIEWER_KEY = "codex-cli"
FIXED_MODEL = "gpt-5.5"
FIXED_MODEL_PROFILE = "gpt-5.5-high-standard"
FIXED_REASONING_EFFORT = "high"
FIXED_SPEED_TIER = "standard"
DEFAULT_BATCH_SIZE = 5
AUTH_MODE_LOCAL_OAUTH = "local_oauth"
AUTH_MODE_ENV_PROVIDER = "env_provider"
AUTH_MODE_ALIASES = {
    "oauth": AUTH_MODE_LOCAL_OAUTH,
    "local": AUTH_MODE_LOCAL_OAUTH,
    "local_oauth": AUTH_MODE_LOCAL_OAUTH,
    "codex_oauth": AUTH_MODE_LOCAL_OAUTH,
    "native": AUTH_MODE_LOCAL_OAUTH,
    "env": AUTH_MODE_ENV_PROVIDER,
    "env_provider": AUTH_MODE_ENV_PROVIDER,
    "local_proxy": AUTH_MODE_ENV_PROVIDER,
    "proxy": AUTH_MODE_ENV_PROVIDER,
    "apikey": AUTH_MODE_ENV_PROVIDER,
    "api_key": AUTH_MODE_ENV_PROVIDER,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "candidates": "data/candidates/candidates_latest.json",
    "kline_dir": "data/kline",
    "output_dir": "data/review/codex_cli",
    "prompt_path": "agent/prompt.md",
    "codex_bin": "codex",
    "model": FIXED_MODEL,
    "model_profile": FIXED_MODEL_PROFILE,
    "reasoning_effort": FIXED_REASONING_EFFORT,
    "speed_tier": FIXED_SPEED_TIER,
    "force_fixed_model": True,
    "auth_mode": AUTH_MODE_LOCAL_OAUTH,
    "ignore_user_config": False,
    "env_provider_enabled": False,
    "codex_provider_name": "env_custom",
    "codex_base_url": "",
    "base_url_env_vars": ["CODEX_OPENAI_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE"],
    "api_key_env_vars": ["CODEX_OPENAI_API_KEY", "OPENAI_API_KEY"],
    "timeout_seconds": 900,
    "request_delay": 1,
    "batch_size": DEFAULT_BATCH_SIZE,
    "max_items": 1,
    "skip_existing": True,
    "suggest_min_score": 4.0,
    "save_raw_cli_io": True,
    "raw_log_dir": "",
    "fallback_to_single_on_batch_error": True,
    "retry_backoff_seconds": [30, 90],
    "classic_pattern_enabled": True,
    "group_review_by_strategy": True,
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


class CodexCliError(RuntimeError):
    pass


class CodexCliAuthError(CodexCliError):
    pass


class CodexCliJsonContractError(CodexCliError):
    pass


def _resolve_cfg_path(path_like: str | Path, base_dir: Path = _ROOT) -> Path:
    p = Path(path_like).expanduser()
    return p if p.is_absolute() else (base_dir / p)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _float_list(value: Any) -> list[float]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [float(item.strip()) for item in value.split(",") if item.strip()]
    return [float(item) for item in value]


def _string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _bool_value(value: Any, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _toml_string(value: str) -> str:
    return json.dumps(str(value))


def _safe_provider_name(value: Any) -> str:
    raw = str(value or "env_custom").strip() or "env_custom"
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw)
    if not safe or not (safe[0].isalpha() or safe[0] == "_"):
        safe = f"provider_{safe}"
    return safe


def _normalize_auth_mode(cfg: dict[str, Any]) -> str:
    raw = str(cfg.get("auth_mode") or cfg.get("codex_auth_mode") or "").strip().lower().replace("-", "_")
    if raw:
        return AUTH_MODE_ALIASES.get(raw, AUTH_MODE_LOCAL_OAUTH)
    if _bool_value(cfg.get("env_provider_enabled"), default=False):
        return AUTH_MODE_ENV_PROVIDER
    return AUTH_MODE_LOCAL_OAUTH


def _is_auth_error(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "401 unauthorized",
            "incorrect api key",
            "invalid api key",
            "no api key provided",
            "missing api key",
            "authentication failed",
            "unauthorized",
        )
    )


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    cfg_path = config_path or _DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"找不到配置文件：{cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    cfg = {**DEFAULT_CONFIG, **raw}
    ignore_user_config_configured = "ignore_user_config" in raw
    if "auth_mode" not in raw and "codex_auth_mode" not in raw:
        cfg.pop("auth_mode", None)
    for key in ("candidates", "kline_dir", "output_dir", "prompt_path"):
        cfg[key] = _resolve_cfg_path(cfg[key])
    cfg["raw_log_dir"] = _resolve_cfg_path(cfg["raw_log_dir"]) if str(cfg.get("raw_log_dir") or "").strip() else None
    cfg["timeout_seconds"] = int(cfg.get("timeout_seconds", 900))
    cfg["request_delay"] = float(cfg.get("request_delay", 1))
    cfg["batch_size"] = max(1, int(cfg.get("batch_size", DEFAULT_BATCH_SIZE)))
    cfg["max_items"] = _optional_int(cfg.get("max_items"))
    cfg["retry_backoff_seconds"] = _float_list(cfg.get("retry_backoff_seconds", [30, 90]))
    cfg["auth_mode"] = _normalize_auth_mode(cfg)
    if cfg["auth_mode"] == AUTH_MODE_ENV_PROVIDER:
        cfg["env_provider_enabled"] = True
        cfg["ignore_user_config"] = _bool_value(
            cfg.get("ignore_user_config") if ignore_user_config_configured else None,
            default=True,
        )
    else:
        cfg["env_provider_enabled"] = False
        cfg["ignore_user_config"] = False
    cfg["codex_provider_name"] = _safe_provider_name(cfg.get("codex_provider_name"))
    cfg["codex_base_url"] = str(cfg.get("codex_base_url") or "").strip()
    cfg["base_url_env_vars"] = _string_list(cfg.get("base_url_env_vars"))
    cfg["api_key_env_vars"] = _string_list(cfg.get("api_key_env_vars"))

    if bool(cfg.get("force_fixed_model", True)):
        cfg["model"] = FIXED_MODEL
        cfg["model_profile"] = FIXED_MODEL_PROFILE
        cfg["reasoning_effort"] = FIXED_REASONING_EFFORT
        cfg["speed_tier"] = FIXED_SPEED_TIER
    else:
        cfg["model"] = str(cfg.get("model") or FIXED_MODEL)
        cfg["model_profile"] = str(cfg.get("model_profile") or cfg["model"])
        cfg["reasoning_effort"] = str(cfg.get("reasoning_effort") or FIXED_REASONING_EFFORT)
        cfg["speed_tier"] = str(cfg.get("speed_tier") or FIXED_SPEED_TIER)
    return cfg


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _find_reviews(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    if isinstance(value, dict):
        for key in ("reviews", "results", "items", "stocks"):
            found = _find_reviews(value.get(key))
            if found is not None:
                return found
        for key in ("text", "content", "output", "message", "response", "last_message"):
            item = value.get(key)
            if isinstance(item, str):
                parsed = _parse_reviews_text(item, raise_on_error=False)
                if parsed is not None:
                    return parsed
            else:
                found = _find_reviews(item)
                if found is not None:
                    return found
    return None


def _parse_reviews_text(text: str, *, raise_on_error: bool = True) -> list[dict[str, Any]] | None:
    payload = _strip_json_fence(text)
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        start = payload.find("{")
        end = payload.rfind("}") + 1
        if start == -1 or end == 0:
            start = payload.find("[")
            end = payload.rfind("]") + 1
        if start == -1 or end == 0:
            if raise_on_error:
                raise CodexCliJsonContractError(f"未能在 Codex 输出中找到 JSON:\n{payload[:1200]}")
            return None
        try:
            parsed = json.loads(payload[start:end])
        except json.JSONDecodeError:
            if raise_on_error:
                raise
            return None
    found = _find_reviews(parsed)
    if found is None and raise_on_error:
        raise CodexCliJsonContractError(f"Codex 输出缺少 reviews 数组:\n{payload[:1200]}")
    return found


def output_schema(item_count: int) -> dict[str, Any]:
    review_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "code",
            "strategy",
            "trend_reasoning",
            "position_reasoning",
            "volume_reasoning",
            "abnormal_move_reasoning",
            "signal_reasoning",
            "classic_pattern_type",
            "classic_pattern_reasoning",
            "common_gate",
            "scores",
            "total_score",
            "signal_type",
            "verdict",
            "comment",
        ],
        "properties": {
            "code": {"type": "string"},
            "strategy": {"type": "string"},
            "trend_reasoning": {"type": "string"},
            "position_reasoning": {"type": "string"},
            "volume_reasoning": {"type": "string"},
            "abnormal_move_reasoning": {"type": "string"},
            "signal_reasoning": {"type": "string"},
            "classic_pattern_type": {"type": "string"},
            "classic_pattern_reasoning": {"type": "string"},
            "common_gate": {
                "type": "object",
                "additionalProperties": False,
                "required": ["scores", "hard_veto", "hard_veto_reasons", "comment"],
                "properties": {
                    "scores": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "trend_qualification",
                            "support_stop_loss_control",
                            "overhead_room",
                            "volume_health",
                            "post_entry_discipline",
                        ],
                        "properties": {
                            "trend_qualification": {"type": "number", "minimum": 0, "maximum": 5},
                            "support_stop_loss_control": {"type": "number", "minimum": 0, "maximum": 5},
                            "overhead_room": {"type": "number", "minimum": 0, "maximum": 5},
                            "volume_health": {"type": "number", "minimum": 0, "maximum": 5},
                            "post_entry_discipline": {"type": "number", "minimum": 0, "maximum": 5},
                        },
                    },
                    "hard_veto": {"type": "boolean"},
                    "hard_veto_reasons": {"type": "array", "items": {"type": "string"}},
                    "comment": {"type": "string"},
                },
            },
            "scores": {
                "type": "object",
                "required": list(REQUIRED_SCORE_FIELDS),
                "additionalProperties": False,
                "properties": {
                    field: {"type": "number", "minimum": 0, "maximum": 5}
                    for field in REQUIRED_SCORE_FIELDS
                },
            },
            "total_score": {"type": "number", "minimum": 0, "maximum": 5},
            "signal_type": {"type": "string"},
            "verdict": {"type": "string", "enum": sorted(VALID_VERDICTS)},
            "comment": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "required": ["reviews"],
        "additionalProperties": False,
        "properties": {
            "reviews": {
                "type": "array",
                "minItems": item_count,
                "maxItems": item_count,
                "items": review_schema,
            }
        },
    }


class CodexCliReviewer(BaseReviewer):
    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.codex_bin = str(config.get("codex_bin", "codex"))
        self.model = str(config.get("model") or FIXED_MODEL)
        self.model_profile = str(config.get("model_profile") or self.model)
        self.reasoning_effort = str(config.get("reasoning_effort") or FIXED_REASONING_EFFORT)
        self.speed_tier = str(config.get("speed_tier") or FIXED_SPEED_TIER)
        self.timeout_seconds = int(config.get("timeout_seconds", 900))
        self.raw_log_root: Path | None = None
        self.cli_call_index = 0
        self._validate_codex_bin()

    def _validate_codex_bin(self) -> None:
        if os.sep in self.codex_bin:
            if not Path(self.codex_bin).exists():
                raise FileNotFoundError(f"找不到 codex CLI：{self.codex_bin}")
            return
        if shutil.which(self.codex_bin) is None:
            raise FileNotFoundError(f"找不到 codex CLI：{self.codex_bin}")

    @staticmethod
    def _safe_log_name(codes: list[str]) -> str:
        if not codes:
            raw = "unknown"
        elif len(codes) == 1:
            raw = codes[0]
        else:
            raw = f"{codes[0]}-{codes[-1]}_{len(codes)}"
        return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)[:80]

    def _next_raw_call_dir(self, codes: list[str]) -> Path | None:
        if not self.config.get("save_raw_cli_io", True) or self.raw_log_root is None:
            return None
        self.cli_call_index += 1
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        call_dir = self.raw_log_root / f"{timestamp}_{self.cli_call_index:04d}_{self._safe_log_name(codes)}"
        call_dir.mkdir(parents=True, exist_ok=True)
        return call_dir

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _base_url_source(self) -> tuple[str, str]:
        configured = str(self.config.get("codex_base_url") or "").strip()
        if configured:
            return "config.codex_base_url", configured
        for env_name in _string_list(self.config.get("base_url_env_vars")):
            value = os.environ.get(env_name, "").strip()
            if value:
                return env_name, value
        return "", ""

    def _api_key_env_source(self) -> tuple[str, str]:
        for env_name in _string_list(self.config.get("api_key_env_vars")):
            value = os.environ.get(env_name, "").strip()
            if value:
                return env_name, value
        return "", ""

    def _provider_override_meta(self) -> dict[str, Any]:
        enabled = _bool_value(self.config.get("env_provider_enabled"), default=False)
        source, base_url = self._base_url_source()
        provider_name = _safe_provider_name(self.config.get("codex_provider_name"))
        return {
            "enabled": enabled and bool(base_url),
            "provider_name": provider_name,
            "base_url": base_url,
            "base_url_source": source,
        }

    def _provider_override_args(self) -> list[str]:
        meta = self._provider_override_meta()
        if not meta["enabled"]:
            return []
        provider_name = str(meta["provider_name"])
        base_url = str(meta["base_url"])
        return [
            "-c",
            f"model_provider={_toml_string(provider_name)}",
            "-c",
            f"model_providers.{provider_name}.name={_toml_string(provider_name)}",
            "-c",
            f"model_providers.{provider_name}.wire_api={_toml_string('responses')}",
            "-c",
            f"model_providers.{provider_name}.requires_openai_auth=true",
            "-c",
            f"model_providers.{provider_name}.base_url={_toml_string(base_url)}",
            "-c",
            f"preferred_auth_method={_toml_string('apikey')}",
        ]

    def _codex_env(self) -> tuple[dict[str, str], dict[str, Any]]:
        env = {**os.environ, "NO_COLOR": "1"}
        provider_meta = self._provider_override_meta()
        api_key_env_var = ""
        api_key = ""
        stripped_env_vars: list[str] = []
        if provider_meta["enabled"]:
            api_key_env_var, api_key = self._api_key_env_source()
            if api_key:
                env["OPENAI_API_KEY"] = api_key
        else:
            strip_names = {
                *_string_list(self.config.get("base_url_env_vars")),
                *_string_list(self.config.get("api_key_env_vars")),
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "OPENAI_API_BASE",
            }
            for env_name in sorted(name for name in strip_names if name):
                if env_name in env:
                    env.pop(env_name, None)
                    stripped_env_vars.append(env_name)
        return env, {
            "api_key_env_var": api_key_env_var,
            "api_key_env_present": bool(api_key),
            "api_key_forwarded": bool(api_key and provider_meta["enabled"]),
            "stripped_env_vars": stripped_env_vars,
        }

    def _build_batch_prompt(self, *, items: list[dict[str, Any]], prompt: str) -> str:
        prompt = self.prompt_for_strategy(prompt, self.batch_strategy(items))
        lines = [
            prompt,
            "",
            "---",
            "",
            f"本批需要复评 {len(items)} 支股票。图片按命令行 --image 的顺序提供，必须逐张对应：",
        ]
        for index, item in enumerate(items, 1):
            lines.append(f"{index}. 股票代码：{item['code']}")
            if item.get("strategy"):
                lines.append(f"   来源策略：{item['strategy']}")
            lines.append(f"   图片：第 {index} 张日线图")
        lines.extend(
            [
                "",
                "请分别读取每张日线图，严格按照评分规则逐只完成复评。",
                "只输出符合 output schema 的 JSON 对象，不要输出 Markdown、解释文字或额外字段。",
                "reviews 数组长度必须等于本批股票数量，顺序必须与上方股票列表一致。",
            ]
        )
        return "\n".join(lines)

    def _build_command(self, *, image_paths: list[Path], schema_path: Path, output_path: Path, work_dir: Path, prompt: str) -> list[str]:
        cmd = [
            self.codex_bin,
            "--ask-for-approval",
            "never",
            "exec",
            "--ignore-rules",
            "-c",
            f'model_reasoning_effort="{self.reasoning_effort}"',
            "-c",
            "fast_default_opt_out=true",
            "--model",
            self.model,
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
        ]
        if self.config.get("ignore_user_config", False):
            cmd.insert(4, "--ignore-user-config")
        cmd.extend(self._provider_override_args())
        for image_path in image_paths:
            cmd.extend(["--image", str(image_path.resolve())])
        cmd.extend(
            [
                "--output-schema",
                str(schema_path.resolve()),
                "-C",
                str(work_dir.resolve()),
                "-o",
                str(output_path.resolve()),
                prompt,
            ]
        )
        return cmd

    def _run_codex_batch(self, *, items: list[dict[str, Any]], prompt_text: str) -> subprocess.CompletedProcess[str]:
        codes = [str(item["code"]) for item in items]
        image_paths = [Path(item["day_chart"]) for item in items]
        raw_dir = self._next_raw_call_dir(codes)
        started_at = datetime.now()
        started_monotonic = time.monotonic()

        with tempfile.TemporaryDirectory(prefix="stocktradebyz-codex-review-") as tmp:
            work_dir = Path(tmp)
            schema_path = (raw_dir or work_dir) / "schema.json"
            output_path = (raw_dir or work_dir) / "last_message.json"
            self._write_json(schema_path, output_schema(len(items)))
            if raw_dir is not None:
                self._write_text(raw_dir / "prompt.txt", prompt_text)

            cmd = self._build_command(
                image_paths=image_paths,
                schema_path=schema_path,
                output_path=output_path,
                work_dir=work_dir,
                prompt=prompt_text,
            )
            provider_meta = self._provider_override_meta()
            codex_env, env_meta = self._codex_env()
            provider_text = ""
            if provider_meta["enabled"]:
                provider_text = f" provider={provider_meta['provider_name']} base_url={provider_meta['base_url']}"
            print(
                "[Command] Codex CLI 实际命令: "
                f"{self.codex_bin} exec --model {self.model} "
                f"-c model_reasoning_effort={self.reasoning_effort} "
                f"-c fast_default_opt_out=true{provider_text} "
                f"--image <{len(image_paths)} files> --output-schema <schema> <prompt>"
            )
            print(f"[INFO] Codex model: {self.model}, reasoning={self.reasoning_effort}, speed={self.speed_tier}")
            if raw_dir is not None:
                print(f"[INFO] Codex CLI raw log: {raw_dir}")
                self._write_json(
                    raw_dir / "meta.json",
                    {
                        "status": "running",
                        "started_at": started_at.isoformat(timespec="seconds"),
                        "command": cmd,
                        "codes": codes,
                        "image_paths": [str(path) for path in image_paths],
                        "model": self.model,
                        "model_profile": self.model_profile,
                        "reasoning_effort": self.reasoning_effort,
                        "speed_tier": self.speed_tier,
                        "ignore_user_config": bool(self.config.get("ignore_user_config", False)),
                        "provider_override": provider_meta,
                        "env": env_meta,
                        "timeout_seconds": self.timeout_seconds,
                    },
                )

            result = subprocess.run(
                cmd,
                cwd=work_dir,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                env=codex_env,
            )
            output_text = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
            # `codex exec -o` writes the final structured message to output_path,
            # while stdout may also echo it. Parsing the concatenation yields
            # "Extra data", so prefer the explicit final-message file.
            result_stdout = output_text.strip() or result.stdout.strip()

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
                        "codes": codes,
                        "image_paths": [str(path) for path in image_paths],
                        "exit_code": result.returncode,
                        "model": self.model,
                        "model_profile": self.model_profile,
                        "reasoning_effort": self.reasoning_effort,
                        "speed_tier": self.speed_tier,
                        "ignore_user_config": bool(self.config.get("ignore_user_config", False)),
                        "provider_override": provider_meta,
                        "env": env_meta,
                        "timeout_seconds": self.timeout_seconds,
                    },
                )
            result = subprocess.CompletedProcess(
                args=result.args,
                returncode=result.returncode,
                stdout=result_stdout,
                stderr=result.stderr,
            )

        return result

    @staticmethod
    def _numeric_score(value: Any) -> float | None:
        return BaseReviewer._numeric_score(value)

    def _validate_review_payload(self, payload: dict[str, Any], *, code: str, strategy: str) -> None:
        errors: list[str] = []
        for field in REQUIRED_TEXT_FIELDS:
            if not isinstance(payload.get(field), str):
                errors.append(f"{field} 缺失或不是字符串")

        scores = payload.get("scores")
        if not isinstance(scores, dict):
            errors.append("scores 缺失或不是对象")
        else:
            for field in REQUIRED_SCORE_FIELDS:
                if field not in scores:
                    errors.append(f"scores.{field} 缺失")
                elif self._numeric_score(scores.get(field)) is None:
                    errors.append(f"scores.{field} 不是 0 到 5 的数字")

        if self._numeric_score(payload.get("total_score")) is None:
            errors.append("total_score 缺失或不是 0 到 5 的数字")
        verdict = str(payload.get("verdict") or "").strip().upper()
        if verdict not in VALID_VERDICTS:
            errors.append("verdict 必须是 PASS、WATCH 或 FAIL")
        actual_code = str(payload.get("code") or "").strip()
        if actual_code and actual_code != code:
            errors.append(f"code 不匹配：输出 {actual_code}，期望 {code}")
        actual_strategy = str(payload.get("strategy") or "").strip()
        if actual_strategy and strategy and actual_strategy != strategy:
            errors.append(f"strategy 不匹配：输出 {actual_strategy}，期望 {strategy}")
        if errors:
            raise CodexCliJsonContractError("; ".join(errors))

    def _parse_result(self, result: subprocess.CompletedProcess[str], *, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        combined_output = f"{result.stdout}\n{result.stderr}".strip()
        if result.returncode != 0:
            if _is_auth_error(combined_output):
                raise CodexCliAuthError(f"Codex CLI 认证失败: {combined_output[:1200]}")
            raise CodexCliError(f"Codex CLI 退出码 {result.returncode}: {combined_output[:1200]}")

        reviews = _parse_reviews_text(result.stdout, raise_on_error=False)
        if reviews is None:
            reviews = _parse_reviews_text(combined_output, raise_on_error=True)
        if reviews is None:
            raise CodexCliJsonContractError("Codex 输出缺少 reviews")
        if len(reviews) != len(items):
            raise CodexCliJsonContractError(f"Codex 批量返回数量不匹配：期望 {len(items)}，实际 {len(reviews)}")

        normalized: list[dict[str, Any]] = []
        for item, payload in zip(items, reviews):
            code = str(item["code"])
            strategy = str(item.get("strategy") or "")
            self._validate_review_payload(payload, code=code, strategy=strategy)
            payload["code"] = code
            payload["strategy"] = strategy or payload.get("strategy", "")
            payload["reviewer"] = REVIEWER_KEY
            payload["model"] = self.model
            payload["model_profile"] = self.model_profile
            payload["reasoning_effort"] = self.reasoning_effort
            payload["speed_tier"] = self.speed_tier
            payload["json_output_mode"] = "output-schema"
            payload["json_schema_valid"] = True
            normalized.append(payload)
        return normalized

    def review_batch(self, items: list[dict[str, Any]], prompt: str) -> list[dict[str, Any]]:
        prompt_text = self._build_batch_prompt(items=items, prompt=prompt)
        result = self._run_codex_batch(items=items, prompt_text=prompt_text)
        return self._parse_result(result, items=items)

    def review_stock(self, code: str, day_chart: Path, prompt: str, strategy: str = "") -> dict:
        item = {"code": code, "strategy": strategy, "review_key": self.review_key(code, strategy), "day_chart": day_chart}
        return self.review_batch([item], prompt)[0]

    @staticmethod
    def _write_stock_result(item: dict[str, Any], result: dict[str, Any]) -> None:
        with open(item["out_file"], "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _codes(items: list[dict[str, Any]]) -> list[str]:
        return [str(item.get("review_key") or item["code"]) for item in items]

    def _review_batch_items(self, items: list[dict[str, Any]], total_candidates: int) -> tuple[list[dict[str, Any]], list[str]]:
        if not items:
            return [], []
        start_index = items[0]["index"]
        end_index = items[-1]["index"]
        codes = self._codes(items)
        retry_delays = list(self.config.get("retry_backoff_seconds") or [])

        for attempt in range(len(retry_delays) + 1):
            action = "批量分析" if attempt == 0 else f"批量重试 {attempt}/{len(retry_delays)}"
            print(
                f"[{start_index}-{end_index}/{total_candidates}] "
                f"{','.join(codes)} — Codex CLI {action} {len(items)} 张图 ...",
                end=" ",
                flush=True,
            )
            try:
                results = self.review_batch(items, self.prompt)
                for item, result in zip(items, results):
                    result["review_key"] = item.get("review_key") or self.review_key(str(result.get("code") or ""), str(result.get("strategy") or ""))
                    result = self.normalize_scores(result, self.config)
                    self._write_stock_result(item, result)
                print("完成")
                for result in results:
                    print(f"    {result['code']} — verdict={result.get('verdict', '?')}, score={result.get('total_score', '?')}")
                return results, []
            except CodexCliAuthError as exc:
                print(f"认证失败 — {exc}")
                raise
            except Exception as exc:  # noqa: BLE001 - fallback below decides whether to split.
                print(f"失败 — {exc}")
                if attempt < len(retry_delays):
                    delay = float(retry_delays[attempt])
                    if delay > 0:
                        print(f"[INFO] Codex 本批 {delay} 秒后重试。")
                        time.sleep(delay)
                    continue
                break

        if self.config.get("fallback_to_single_on_batch_error", True) and len(items) > 1:
            delay = float(self.config.get("request_delay", 1))
            if len(items) > 2:
                mid = len(items) // 2
                print(f"[INFO] Codex 批量失败，拆分为 {mid}+{len(items) - mid} 继续。")
                if delay:
                    time.sleep(delay)
                left_results, left_failed = self._review_batch_items(items[:mid], total_candidates)
                if delay:
                    time.sleep(delay)
                right_results, right_failed = self._review_batch_items(items[mid:], total_candidates)
                return left_results + right_results, left_failed + right_failed
            print("[INFO] Codex 小批量失败，使用同一模型逐只复评。")
            results: list[dict[str, Any]] = []
            failed: list[str] = []
            for item in items:
                if delay:
                    time.sleep(delay)
                item_results, item_failed = self._review_batch_items([item], total_candidates)
                results.extend(item_results)
                failed.extend(item_failed)
            return results, failed

        return [], codes

    def run(self) -> None:
        candidates_data = self.load_candidates(Path(self.config["candidates"]))
        pick_date: str = candidates_data["pick_date"]
        candidates: list[dict[str, Any]] = self.order_candidates_for_review(candidates_data)
        max_items = self.config.get("max_items")
        if max_items is not None:
            candidates = candidates[: int(max_items)]
        batch_size = int(self.config.get("batch_size", DEFAULT_BATCH_SIZE))
        print(f"[INFO] pick_date={pick_date}，Codex 复评股票数={len(candidates)}，batch_size={batch_size}")
        print(f"[INFO] Codex reviewer 模型：{self.model} / reasoning={self.reasoning_effort} / speed={self.speed_tier}")

        out_dir = self.output_dir / pick_date
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_log_dir = self.config.get("raw_log_dir")
        self.raw_log_root = Path(raw_log_dir) if raw_log_dir else out_dir / "codex_cli_runs"
        if self.config.get("save_raw_cli_io", True):
            self.raw_log_root.mkdir(parents=True, exist_ok=True)
            print(f"[INFO] Codex CLI raw logs: {self.raw_log_root}")
        else:
            self.raw_log_root = None

        all_results: list[dict[str, Any]] = []
        failed_codes: list[str] = []
        review_batch: list[dict[str, Any]] = []

        for i, candidate in enumerate(candidates, 1):
            code = str(candidate["code"])
            strategy = str(candidate.get("strategy") or "")
            item_key = self.review_key(code, strategy)
            out_file = self.review_file(out_dir, code, strategy)
            if self.config.get("skip_existing", False) and out_file.exists():
                with open(out_file, encoding="utf-8") as f:
                    result = json.load(f)
                if result.get("reviewer") == REVIEWER_KEY:
                    print(f"[{i}/{len(candidates)}] {item_key} — 已存在，跳过。")
                    result = self.normalize_scores(result, self.config)
                    all_results.append(result)
                    continue

            day_chart = self.find_chart_images(pick_date, code)
            if day_chart is None:
                print(f"[{i}/{len(candidates)}] {item_key} — 缺少日线图，跳过。")
                failed_codes.append(item_key)
                continue

            if review_batch and self.batch_strategy(review_batch) != strategy:
                results, failed = self._review_batch_items(review_batch, len(candidates))
                all_results.extend(results)
                failed_codes.extend(failed)
                review_batch = []
                if i < len(candidates) and float(self.config.get("request_delay", 1)) > 0:
                    time.sleep(float(self.config.get("request_delay", 1)))

            review_batch.append(
                {
                    "index": i,
                    "code": code,
                    "strategy": strategy,
                    "review_key": item_key,
                    "day_chart": day_chart,
                    "out_file": out_file,
                }
            )
            if len(review_batch) < batch_size:
                continue
            results, failed = self._review_batch_items(review_batch, len(candidates))
            all_results.extend(results)
            failed_codes.extend(failed)
            review_batch = []
            if i < len(candidates) and float(self.config.get("request_delay", 1)) > 0:
                time.sleep(float(self.config.get("request_delay", 1)))

        if review_batch:
            results, failed = self._review_batch_items(review_batch, len(candidates))
            all_results.extend(results)
            failed_codes.extend(failed)

        print(f"\n[INFO] Codex 复评完成：成功 {len(all_results)} 支，失败/跳过 {len(failed_codes)} 支")
        if failed_codes:
            print(f"[WARN] 未处理股票：{failed_codes}")
        if not all_results:
            print("[ERROR] 没有可用的 Codex 复评结果，跳过汇总。")
            raise SystemExit(1)

        suggestion = self.generate_suggestion(
            pick_date=pick_date,
            all_results=all_results,
            min_score=float(self.config.get("suggest_min_score", 4.0)),
            candidates=candidates,
        )
        reviewed_codes = {self.result_review_key(item) for item in all_results}
        pending = [self.candidate_review_key(candidate) for candidate in candidates if self.candidate_review_key(candidate) not in reviewed_codes]
        suggestion.update(
            {
                "reviewer": REVIEWER_KEY,
                "model": self.model,
                "model_profile": self.model_profile,
                "reasoning_effort": self.reasoning_effort,
                "speed_tier": self.speed_tier,
                "review_complete": not failed_codes and not pending,
                "total_candidates": len(candidates),
                "failed_or_skipped": failed_codes,
                "pending": pending,
            }
        )
        suggestion_file = out_dir / "suggestion.json"
        self._write_json(suggestion_file, suggestion)
        print(f"[INFO] Codex 汇总已写入: {suggestion_file}")
        if not suggestion["review_complete"]:
            raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Codex CLI 图表复评")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG_PATH), help="配置文件路径")
    parser.add_argument("--limit", type=int, default=None, help="覆盖配置中的 max_items")
    parser.add_argument("--candidates", default="", help="覆盖候选列表 JSON")
    parser.add_argument("--kline-dir", default="", help="覆盖 K 线图目录")
    parser.add_argument("--output-dir", default="", help="覆盖输出目录")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    if args.limit is not None:
        config["max_items"] = args.limit
    if args.candidates:
        config["candidates"] = _resolve_cfg_path(args.candidates)
    if args.kline_dir:
        config["kline_dir"] = _resolve_cfg_path(args.kline_dir)
    if args.output_dir:
        config["output_dir"] = _resolve_cfg_path(args.output_dir)

    reviewer = CodexCliReviewer(config)
    reviewer.run()


if __name__ == "__main__":
    main()
