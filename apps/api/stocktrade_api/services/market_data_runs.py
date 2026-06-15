from __future__ import annotations

import os
import multiprocessing as mp
import queue
import re
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import yaml

from ..schemas.market_data import MarketDataRunRequest
from ..storage.run_repository import RunRepository
from ..storage.sqlite import ROOT
from ..storage.sqlite_models import Artifact
from .cancellation import CancellationCheck, WorkflowCancellationRequested, raise_if_cancelled


class MarketDataDownloadValidationError(ValueError):
    pass


class MarketDataDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class CreatedMarketDataDownload:
    summary: dict[str, Any]
    artifacts: list[Artifact]


def _fetch_kline_child_main(
    config_path: str,
    log_path: str,
    cancel_event: Any,
    progress_queue: Any,
) -> None:
    try:
        from pipeline import fetch_kline

        def should_cancel() -> bool:
            return bool(cancel_event.is_set())

        def report_progress(payload: dict[str, Any]) -> None:
            progress_queue.put({"type": "progress", "payload": payload})

        fetch_kline.main(
            config_path=Path(config_path),
            log_path=Path(log_path),
            should_cancel=should_cancel,
            progress_callback=report_progress,
        )
        progress_queue.put({"type": "done"})
        progress_queue.close()
        progress_queue.join_thread()
    except BaseException as exc:  # pragma: no cover - exercised through parent process tests.
        progress_queue.put(
            {
                "type": "error",
                "class": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        progress_queue.close()
        progress_queue.join_thread()
        raise


class MarketDataDownloadService:
    def __init__(
        self,
        run_repository: RunRepository,
        *,
        artifact_root: str | Path,
        execution_mode: str = "subprocess",
        cancel_grace_seconds: float = 2.0,
    ) -> None:
        if execution_mode not in {"subprocess", "in_process"}:
            raise ValueError("execution_mode must be subprocess or in_process")
        self.run_repository = run_repository
        self.artifact_root = _resolve_root(artifact_root)
        self.execution_mode = execution_mode
        self.cancel_grace_seconds = max(0.0, float(cancel_grace_seconds))

    def run(
        self,
        *,
        run_id: str,
        request: MarketDataRunRequest,
        should_cancel: CancellationCheck | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> CreatedMarketDataDownload:
        raise_if_cancelled(should_cancel)
        if not (os.environ.get("TUSHARE_TOKEN") or "").strip():
            raise MarketDataDownloadValidationError("请先设置环境变量 TUSHARE_TOKEN")

        source_config_path = _resolve_repo_path(request.config_path, ROOT / "config" / "fetch_kline.yaml")
        config = _load_config(source_config_path)
        _apply_overrides(config, request)

        output_dir = _resolve_repo_path(str(config.get("out") or "./data"), ROOT / "data" / "raw")
        run_artifact_dir = self.artifact_root / _safe_segment(run_id) / "market-data"
        run_artifact_dir.mkdir(parents=True, exist_ok=True)
        effective_config_path = run_artifact_dir / "fetch_kline.effective.yaml"
        log_path = _resolve_repo_path(request.log_path, run_artifact_dir / "fetch_kline.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)
        effective_config_path.write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self.run_repository.append_event(
            run_id,
            message=f"Market data config loaded from {_display_path(source_config_path)}",
        )
        self.run_repository.append_event(
            run_id,
            message=f"Market data output {_display_path(output_dir)}; log {_display_path(log_path)}",
        )
        self.run_repository.append_event(
            run_id,
            message=(
                f"Market data fetch starting for {config.get('start') or 'not set'} "
                f"to {config.get('end') or 'not set'}"
            ),
        )

        try:
            raise_if_cancelled(should_cancel)
            self._run_fetch_kline(
                config_path=effective_config_path,
                log_path=log_path,
                should_cancel=should_cancel,
                progress_callback=progress_callback,
            )
            raise_if_cancelled(should_cancel)
            local_latest = _latest_date_from_csv_dir(output_dir) or "无"
        except SystemExit as exc:
            raise MarketDataDownloadError(f"market data download exited with status {exc.code}") from exc
        except WorkflowCancellationRequested:
            raise
        except (MarketDataDownloadError, MarketDataDownloadValidationError):
            raise
        except (FileNotFoundError, ValueError) as exc:
            raise MarketDataDownloadValidationError(str(exc)) from exc
        except Exception as exc:
            if exc.__class__.__name__ == "FetchKlineCancelled":
                raise WorkflowCancellationRequested(str(exc)) from exc
            raise MarketDataDownloadError(str(exc)) from exc

        csv_file_count = _count_csv_files(output_dir)
        self.run_repository.append_event(
            run_id,
            message=(
                f"Market data fetch finished with {csv_file_count} CSV files; "
                f"latest local date {local_latest}"
            ),
        )
        artifact_payloads = [
            _artifact_payload(
                run_id=run_id,
                artifact_root=self.artifact_root,
                path=effective_config_path,
                kind="config",
                content_type="application/x-yaml",
                metadata={"source": "product:market_data", "label": "effective fetch_kline config"},
            )
        ]
        if _is_relative_to(log_path, self.artifact_root):
            artifact_payloads.append(
                _artifact_payload(
                    run_id=run_id,
                    artifact_root=self.artifact_root,
                    path=log_path,
                    kind="log",
                    content_type="text/plain",
                    metadata={"source": "product:market_data", "label": "fetch_kline log"},
                )
            )
        artifacts = self.run_repository.create_artifacts(artifact_payloads)
        stocklist_path = _resolve_repo_path(
            str(config.get("stocklist") or "./pipeline/stocklist.csv"),
            ROOT / "pipeline" / "stocklist.csv",
        )
        summary = {
            "mode": "market_data",
            "message": "completed",
            "config_path": _display_path(source_config_path),
            "effective_config_path": _display_path(effective_config_path),
            "out_dir": _display_path(output_dir),
            "log_path": _display_path(log_path),
            "start": str(config.get("start") or ""),
            "end": str(config.get("end") or ""),
            "workers": int(config.get("workers") or 0),
            "stocklist": _display_path(stocklist_path),
            "exclude_boards": list(config.get("exclude_boards") or []),
            "csv_file_count": csv_file_count,
            "local_latest_date": local_latest,
        }
        return CreatedMarketDataDownload(summary=summary, artifacts=artifacts)

    def _run_fetch_kline(
        self,
        *,
        config_path: Path,
        log_path: Path,
        should_cancel: CancellationCheck | None,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        if self.execution_mode == "in_process":
            _run_fetch_kline_in_process(
                config_path=config_path,
                log_path=log_path,
                should_cancel=should_cancel,
                progress_callback=progress_callback,
            )
            return
        _run_fetch_kline_subprocess(
            config_path=config_path,
            log_path=log_path,
            should_cancel=should_cancel,
            progress_callback=progress_callback,
            cancel_grace_seconds=self.cancel_grace_seconds,
        )


def _run_fetch_kline_in_process(
    *,
    config_path: Path,
    log_path: Path,
    should_cancel: CancellationCheck | None,
    progress_callback: Callable[[dict[str, Any]], None] | None,
) -> None:
    from pipeline import fetch_kline

    fetch_kline.main(
        config_path=config_path,
        log_path=log_path,
        should_cancel=should_cancel,
        progress_callback=progress_callback,
    )


def _run_fetch_kline_subprocess(
    *,
    config_path: Path,
    log_path: Path,
    should_cancel: CancellationCheck | None,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    cancel_grace_seconds: float,
) -> None:
    context = mp.get_context("spawn")
    progress_queue = context.Queue()
    cancel_event = context.Event()
    process = context.Process(
        target=_fetch_kline_child_main,
        args=(str(config_path), str(log_path), cancel_event, progress_queue),
        name="stocktrade-market-data-download",
    )
    process.start()
    child_error: dict[str, Any] | None = None
    try:
        while process.is_alive():
            child_error = _drain_fetch_kline_messages(progress_queue, progress_callback) or child_error
            if should_cancel is not None and should_cancel():
                cancel_event.set()
                process.join(timeout=cancel_grace_seconds)
                child_error = _drain_fetch_kline_messages(progress_queue, progress_callback) or child_error
                if process.is_alive():
                    _terminate_process(process)
                raise WorkflowCancellationRequested("market data download cancelled by user request")
            process.join(timeout=0.2)

        child_error = _drain_fetch_kline_messages(progress_queue, progress_callback) or child_error
        if child_error is not None:
            _raise_child_error(child_error)
        if process.exitcode not in (0, None):
            raise MarketDataDownloadError(f"market data subprocess exited with status {process.exitcode}")
    finally:
        if process.is_alive():
            _terminate_process(process)
        progress_queue.close()
        progress_queue.join_thread()


def _drain_fetch_kline_messages(
    progress_queue: Any,
    progress_callback: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any] | None:
    child_error: dict[str, Any] | None = None
    while True:
        try:
            message = progress_queue.get_nowait()
        except queue.Empty:
            return child_error
        if not isinstance(message, dict):
            continue
        if message.get("type") == "progress" and progress_callback is not None:
            payload = message.get("payload")
            if isinstance(payload, dict):
                progress_callback(payload)
        elif message.get("type") == "error":
            child_error = message


def _raise_child_error(error: dict[str, Any]) -> None:
    error_class = str(error.get("class") or "Exception")
    message = str(error.get("message") or error_class)
    if error_class == "FetchKlineCancelled":
        raise WorkflowCancellationRequested(message)
    if error_class in {"FileNotFoundError", "ValueError"}:
        raise MarketDataDownloadValidationError(message)
    raise MarketDataDownloadError(f"{error_class}: {message}")


def _terminate_process(process: Any) -> None:
    process.terminate()
    process.join(timeout=1.0)
    if process.is_alive():
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()
        else:
            process.terminate()
        process.join(timeout=1.0)


def _load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise MarketDataDownloadValidationError(f"找不到配置文件：{_display_path(config_path)}")
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise MarketDataDownloadValidationError("fetch_kline 配置必须是 YAML 对象")
    return dict(payload)


def _apply_overrides(config: dict[str, Any], request: MarketDataRunRequest) -> None:
    if request.start:
        config["start"] = _normalize_trade_date(request.start, field_name="start")
    if request.end:
        config["end"] = _normalize_trade_date(request.end, field_name="end")
    if request.out_dir:
        config["out"] = request.out_dir.strip()
    if request.workers is not None:
        config["workers"] = request.workers


def _normalize_trade_date(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if normalized.lower() == "today":
        return "today"
    normalized = normalized.replace("-", "")
    if not re.fullmatch(r"\d{8}", normalized):
        raise MarketDataDownloadValidationError(f"{field_name} must be YYYYMMDD, YYYY-MM-DD, or today")
    return normalized


def _artifact_payload(
    *,
    run_id: str,
    artifact_root: Path,
    path: Path,
    kind: str,
    content_type: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": uuid4().hex,
        "run_id": run_id,
        "kind": kind,
        "path": path.relative_to(artifact_root).as_posix(),
        "content_type": content_type,
        "metadata_json": metadata,
    }


def _resolve_repo_path(value: str | Path | None, default: Path) -> Path:
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve(strict=False)


def _resolve_root(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve(strict=False)


def _safe_segment(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", value.strip())
    return safe or "artifact"


def _count_csv_files(out_dir: Path) -> int:
    if not out_dir.is_dir():
        return 0
    return sum(1 for path in out_dir.glob("*.csv") if path.is_file())


def _latest_date_from_csv_dir(out_dir: Path) -> str | None:
    if not out_dir.is_dir():
        return None
    latest: str | None = None
    for path in out_dir.glob("*.csv"):
        try:
            last_line = ""
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        last_line = line.strip()
            if not last_line or last_line.startswith("date,"):
                continue
            normalized = _normalize_csv_date(last_line.split(",", 1)[0])
            if normalized is not None and (latest is None or normalized > latest):
                latest = normalized
        except OSError:
            continue
    return latest


def _normalize_csv_date(value: str) -> str | None:
    text = value.strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return datetime.fromisoformat(text[:10]).date().isoformat()
    except ValueError:
        return None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)
