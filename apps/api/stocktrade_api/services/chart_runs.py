from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..schemas.charts import ChartExportRunCreateRequest
from .cancellation import CancellationCheck, WorkflowCancellationRequested, raise_if_cancelled
from ..storage.candidate_repository import CandidateRepository
from ..storage.run_repository import RunRepository
from ..storage.sqlite import ROOT
from ..storage.sqlite_models import Artifact, Candidate


class ChartExportValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CreatedChartExport:
    artifacts: list[Artifact]
    summary: dict[str, Any]


class ChartExportRunService:
    def __init__(
        self,
        candidate_repository: CandidateRepository,
        run_repository: RunRepository,
        *,
        artifact_root: str | Path,
    ) -> None:
        self.candidate_repository = candidate_repository
        self.run_repository = run_repository
        self.artifact_root = _resolve_root(artifact_root)

    def run(
        self,
        *,
        run_id: str,
        request: ChartExportRunCreateRequest,
        should_cancel: CancellationCheck | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> CreatedChartExport:
        detail = self.candidate_repository.get_candidate_batch(request.candidate_batch_id)
        raise_if_cancelled(should_cancel)
        batch = detail.summary.batch
        raw_dir = _resolve_repo_path(request.raw_dir, ROOT / "data" / "raw")
        candidates_by_code = _candidates_by_code(detail.candidates, limit=request.limit)
        chart_config = _load_chart_config()
        _report_progress(
            progress_callback,
            current=0,
            total=len(candidates_by_code),
            phase="准备导出",
            message=f"准备导出 {len(candidates_by_code)} 支股票图表",
            force=True,
        )

        artifacts: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for index, (code, candidates) in enumerate(candidates_by_code.items(), 1):
            raise_if_cancelled(should_cancel)
            raw_path = raw_dir / f"{code}.csv"
            if not raw_path.is_file():
                skipped.append({"code": code, "reason": "raw csv missing", "path": _display_path(raw_path)})
                _report_progress(
                    progress_callback,
                    current=index,
                    total=len(candidates_by_code),
                    phase="导出图表",
                    code=code,
                    message=f"跳过 {code}，缺少原始 CSV",
                )
                continue

            try:
                compatibility_path = Path(run_id) / "charts" / _safe_segment(batch.id) / f"{_safe_segment(code)}_day.jpg"
                _export_daily_chart(
                    raw_path=raw_path,
                    code=code,
                    strategy="",
                    out_path=self.artifact_root / compatibility_path,
                    bars=request.bars,
                    chart_config=chart_config,
                )
                artifacts.append(
                    _chart_artifact_payload(
                        run_id=run_id,
                        path=compatibility_path,
                        batch_id=batch.id,
                        candidate_run_id=batch.run_id,
                        pick_date=batch.pick_date,
                        code=code,
                        raw_path=raw_path,
                        bars=request.bars,
                        strategies=sorted({candidate.strategy for candidate in candidates}),
                        artifact_scope="code",
                    )
                )

                seen_review_keys: set[str] = set()
                for candidate in candidates:
                    raise_if_cancelled(should_cancel)
                    review_key = _review_key(code, candidate.strategy)
                    if review_key in seen_review_keys:
                        continue
                    seen_review_keys.add(review_key)
                    strategy_path = (
                        Path(run_id)
                        / "charts"
                        / _safe_segment(batch.id)
                        / f"{_safe_segment(code)}_{_safe_segment(candidate.strategy)}_day.jpg"
                    )
                    _export_daily_chart(
                        raw_path=raw_path,
                        code=code,
                        strategy=candidate.strategy,
                        out_path=self.artifact_root / strategy_path,
                        bars=request.bars,
                        chart_config=chart_config,
                    )
                    artifacts.append(
                        _chart_artifact_payload(
                            run_id=run_id,
                            path=strategy_path,
                            batch_id=batch.id,
                            candidate_run_id=batch.run_id,
                            pick_date=batch.pick_date,
                            code=code,
                            raw_path=raw_path,
                            bars=request.bars,
                            strategies=[candidate.strategy],
                            artifact_scope="strategy",
                            strategy=candidate.strategy,
                            review_key=review_key,
                        )
                    )
            except WorkflowCancellationRequested:
                raise
            except Exception as exc:
                skipped.append({"code": code, "reason": str(exc), "path": _display_path(raw_path)})
            _report_progress(
                progress_callback,
                current=index,
                total=len(candidates_by_code),
                phase="导出图表",
                code=code,
                message=f"已处理 {index}/{len(candidates_by_code)} 支股票",
            )

        if detail.candidates and not artifacts:
            raise ChartExportValidationError("no charts were exported for the selected candidate batch")

        raise_if_cancelled(should_cancel)
        created_artifacts = self.run_repository.create_artifacts(artifacts)
        _report_progress(
            progress_callback,
            current=len(candidates_by_code),
            total=len(candidates_by_code),
            phase="完成",
            message=f"已生成 {len(created_artifacts)} 个图表产物",
            finished=True,
            force=True,
        )
        summary = {
            "mode": "chart_export",
            "candidate_batch_id": batch.id,
            "candidate_run_id": batch.run_id,
            "pick_date": batch.pick_date,
            "raw_dir": _display_path(raw_dir),
            "artifact_root": _display_path(self.artifact_root),
            "candidate_count": len(detail.candidates),
            "unique_code_count": len(candidates_by_code),
            "strategy_chart_count": sum(
                1
                for artifact in artifacts
                if artifact["metadata_json"].get("artifact_scope") == "strategy"
            ),
            "compatibility_chart_count": sum(
                1
                for artifact in artifacts
                if artifact["metadata_json"].get("artifact_scope") == "code"
            ),
            "exported_count": len(created_artifacts),
            "skipped_count": len(skipped),
            "skipped": skipped,
        }
        return CreatedChartExport(artifacts=created_artifacts, summary=summary)


def _report_progress(
    progress_callback: Callable[[dict[str, Any]], None] | None,
    *,
    current: int,
    total: int,
    phase: str,
    message: str,
    code: str | None = None,
    finished: bool = False,
    force: bool = False,
) -> None:
    if progress_callback is None:
        return
    payload: dict[str, Any] = {
        "label": "图表导出进度",
        "phase": phase,
        "current": current,
        "total": total,
        "unit": "股票",
        "message": message,
        "finished": finished,
        "force": force,
    }
    if code:
        payload["code"] = code
    progress_callback(payload)


def _chart_artifact_payload(
    *,
    run_id: str,
    path: Path,
    batch_id: str,
    candidate_run_id: str,
    pick_date: str,
    code: str,
    raw_path: Path,
    bars: int,
    strategies: list[str],
    artifact_scope: str,
    strategy: str | None = None,
    review_key: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": "product:chart_export",
        "artifact_scope": artifact_scope,
        "candidate_batch_id": batch_id,
        "candidate_run_id": candidate_run_id,
        "pick_date": pick_date,
        "code": code,
        "strategies": strategies,
        "raw_path": _display_path(raw_path),
        "bars": bars,
        "contains_zx_lines": True,
        "contains_brick_panel": (strategy or "").strip().lower() == "brick",
    }
    if strategy:
        metadata["strategy"] = strategy
    if review_key:
        metadata["review_key"] = review_key
    return {
        "id": uuid4().hex,
        "run_id": run_id,
        "kind": "chart",
        "path": path.as_posix(),
        "content_type": "image/jpeg",
        "metadata_json": metadata,
    }


def _export_daily_chart(
    *,
    raw_path: Path,
    code: str,
    strategy: str,
    out_path: Path,
    bars: int,
    chart_config: dict[str, Any],
) -> None:
    from PIL import Image, ImageDraw, ImageFont

    rows = _load_raw_rows(raw_path)
    if bars > 0:
        rows = rows[-bars:]
    if not rows:
        raise ChartExportValidationError("raw csv contains no rows")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    is_brick = strategy.strip().lower() == "brick"
    width, height = 1400, 900 if is_brick else 760
    margin_l, margin_r = 74, 28
    price_top, price_bottom = 82, 440 if is_brick else 500
    brick_top, brick_bottom = 486, 610
    vol_top, vol_bottom = (660, 850) if is_brick else (560, 710)
    chart_w = width - margin_l - margin_r
    step = chart_w / max(len(rows), 1)
    body_w = max(3, int(step * 0.58))

    zx_short, zx_long = _zx_lines([row["close"] for row in rows], chart_config.get("zx_lines") or {})
    prices = [value for row in rows for value in (row["open"], row["high"], row["low"], row["close"])]
    prices.extend(zx_short)
    prices.extend(zx_long)
    price_low = min(prices)
    price_high = max(prices)
    pad = max((price_high - price_low) * 0.08, 0.01)
    price_low -= pad
    price_high += pad
    vol_high = max(max(row["volume"] for row in rows), 1.0)

    img = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(img)
    title_font = ImageFont.load_default(size=24)
    label_font = ImageFont.load_default(size=16)
    small_font = ImageFont.load_default(size=14)

    first_date = rows[0]["date"]
    last_date = rows[-1]["date"]
    strategy_label = f" {strategy}" if strategy else ""
    draw.text(
        (margin_l, 28),
        f"{code}{strategy_label} daily {first_date} to {last_date}",
        fill="#111827",
        font=title_font,
    )
    for y in _linspace(price_top, price_bottom, 5):
        draw.line((margin_l, y, width - margin_r, y), fill="#e5e7eb", width=1)
    for y in _linspace(vol_top, vol_bottom, 3):
        draw.line((margin_l, y, width - margin_r, y), fill="#edf2f7", width=1)
    draw.rectangle((margin_l, price_top, width - margin_r, price_bottom), outline="#d1d5db", width=1)
    draw.rectangle((margin_l, vol_top, width - margin_r, vol_bottom), outline="#d1d5db", width=1)
    if is_brick:
        draw.rectangle((margin_l, brick_top, width - margin_r, brick_bottom), outline="#d1d5db", width=1)

    zx_short_points: list[tuple[int, int]] = []
    zx_long_points: list[tuple[int, int]] = []
    for index, row in enumerate(rows):
        x = int(margin_l + index * step + step / 2)
        up = row["close"] >= row["open"]
        color = "#dc2626" if up else "#16a34a"
        y_open = _scale(row["open"], price_low, price_high, price_top, price_bottom)
        y_close = _scale(row["close"], price_low, price_high, price_top, price_bottom)
        y_high = _scale(row["high"], price_low, price_high, price_top, price_bottom)
        y_low = _scale(row["low"], price_low, price_high, price_top, price_bottom)
        draw.line((x, y_high, x, y_low), fill=color, width=1)
        body_top = min(y_open, y_close)
        body_bottom = max(y_open, y_close)
        if body_top == body_bottom:
            body_bottom += 1
        draw.rectangle((x - body_w // 2, body_top, x + body_w // 2, body_bottom), fill=color, outline=color)

        vol_y = _scale(row["volume"], 0.0, vol_high, vol_top, vol_bottom)
        draw.rectangle((x - body_w // 2, vol_y, x + body_w // 2, vol_bottom), fill=color)
        zx_short_points.append((x, _scale(zx_short[index], price_low, price_high, price_top, price_bottom)))
        zx_long_points.append((x, _scale(zx_long[index], price_low, price_high, price_top, price_bottom)))

    _draw_line(draw, zx_short_points, "#facc15", width=3)
    _draw_line(draw, zx_long_points, "#ffffff", width=3, outline="#374151")
    if is_brick:
        _draw_brick_panel(draw, rows, margin_l, width - margin_r, brick_top, brick_bottom)

    draw.line((margin_l, price_top - 16, margin_l + 28, price_top - 16), fill="#facc15", width=3)
    draw.text((margin_l + 34, price_top - 25), "ZX short", fill="#374151", font=small_font)
    draw.line(
        (margin_l + 128, price_top - 16, margin_l + 160, price_top - 16),
        fill="#374151",
        width=5,
    )
    draw.line((margin_l + 130, price_top - 16, margin_l + 158, price_top - 16), fill="#ffffff", width=3)
    draw.text((margin_l + 164, price_top - 25), "ZX long", fill="#374151", font=small_font)
    if is_brick:
        draw.text((margin_l, brick_top - 24), "brick panel", fill="#374151", font=label_font)
    draw.text((margin_l, price_bottom + 16), "volume", fill="#374151", font=label_font)
    draw.text((margin_l, height - 32), first_date, fill="#6b7280", font=small_font)
    draw.text((width - margin_r - 90, height - 32), last_date, fill="#6b7280", font=small_font)
    draw.text((width - margin_r - 80, price_top - 10), f"{price_high:.2f}", fill="#6b7280", font=small_font)
    draw.text((width - margin_r - 80, price_bottom - 18), f"{price_low:.2f}", fill="#6b7280", font=small_font)
    img.save(out_path, format="JPEG", quality=92, optimize=True)


def _load_raw_rows(raw_path: Path) -> list[dict[str, Any]]:
    with raw_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for raw in reader:
            try:
                rows.append(
                    {
                        "date": str(raw["date"]),
                        "open": float(raw["open"]),
                        "high": float(raw["high"]),
                        "low": float(raw["low"]),
                        "close": float(raw["close"]),
                        "volume": float(raw["volume"]),
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ChartExportValidationError(f"invalid raw csv row in {raw_path.name}") from exc
    return sorted(rows, key=lambda row: row["date"])


def _scale(value: float, low: float, high: float, top: int, bottom: int) -> int:
    if high <= low:
        return (top + bottom) // 2
    ratio = (value - low) / (high - low)
    return int(bottom - ratio * (bottom - top))


def _draw_line(
    draw: Any,
    points: list[tuple[int, int]],
    color: str,
    *,
    width: int = 2,
    outline: str | None = None,
) -> None:
    if len(points) >= 2:
        if outline:
            draw.line(points, fill=outline, width=width + 2)
        draw.line(points, fill=color, width=width)


def _draw_brick_panel(
    draw: Any,
    rows: list[dict[str, Any]],
    left: int,
    right: int,
    top: int,
    bottom: int,
) -> None:
    closes = [float(row["close"]) for row in rows]
    if not closes:
        return
    low, high = min(closes), max(closes)
    pad = max((high - low) * 0.08, 0.01)
    low -= pad
    high += pad
    step = (right - left) / max(len(closes), 1)
    body_w = max(5, int(step * 0.7))
    previous = closes[0]
    for index, close in enumerate(closes):
        x = int(left + index * step + step / 2)
        up = close >= previous
        color = "#dc2626" if up else "#16a34a"
        y_close = _scale(close, low, high, top, bottom)
        y_previous = _scale(previous, low, high, top, bottom)
        body_top = min(y_close, y_previous)
        body_bottom = max(y_close, y_previous)
        if body_bottom == body_top:
            body_bottom += 2
        draw.rectangle((x - body_w // 2, body_top, x + body_w // 2, body_bottom), fill=color, outline=color)
        previous = close


def _zx_lines(closes: list[float], config: dict[str, Any]) -> tuple[list[float], list[float]]:
    short_span = _positive_int(config.get("zxdq_span"), default=10)
    windows = [
        _positive_int(config.get("m1"), default=14),
        _positive_int(config.get("m2"), default=28),
        _positive_int(config.get("m3"), default=57),
        _positive_int(config.get("m4"), default=114),
    ]
    short = _ema(_ema(closes, span=short_span), span=short_span)
    moving = [_moving_average(closes, window=window) for window in windows]
    long = [
        sum(series[index] for series in moving) / len(moving)
        for index in range(len(closes))
    ]
    return short, long


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(parsed, 1)


def _ema(values: list[float], *, span: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (span + 1)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(float(value) * alpha + result[-1] * (1 - alpha))
    return result


def _moving_average(values: list[float], *, window: int) -> list[float]:
    result: list[float] = []
    for index in range(len(values)):
        start = max(0, index - window + 1)
        window_values = values[start : index + 1]
        result.append(sum(window_values) / len(window_values))
    return result


def _linspace(start: int, stop: int, count: int) -> list[int]:
    if count <= 1:
        return [start]
    step = (stop - start) / (count - 1)
    return [int(start + index * step) for index in range(count)]


def _candidates_by_code(candidates: list[Candidate], *, limit: int) -> dict[str, list[Candidate]]:
    grouped: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        if candidate.code not in grouped:
            if limit > 0 and len(grouped) >= limit:
                continue
            grouped[candidate.code] = []
        grouped[candidate.code].append(candidate)
    return grouped


def _resolve_repo_path(value: str | None, default: Path) -> Path:
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


def _review_key(code: str, strategy: str = "") -> str:
    if not strategy.strip():
        return code
    suffix = _safe_segment(strategy)
    return f"{code}_{suffix}" if suffix else code


def _load_chart_config() -> dict[str, Any]:
    config_path = ROOT / "config" / "dashboard.yaml"
    try:
        import yaml

        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        payload = {}
    chart = payload.get("chart") if isinstance(payload, dict) else {}
    return chart if isinstance(chart, dict) else {}


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)
