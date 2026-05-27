from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..schemas.charts import ChartExportRunCreateRequest
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

    def run(self, *, run_id: str, request: ChartExportRunCreateRequest) -> CreatedChartExport:
        detail = self.candidate_repository.get_candidate_batch(request.candidate_batch_id)
        batch = detail.summary.batch
        raw_dir = _resolve_repo_path(request.raw_dir, ROOT / "data" / "raw")
        candidates_by_code = _candidates_by_code(detail.candidates, limit=request.limit)

        artifacts: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for code, candidates in candidates_by_code.items():
            raw_path = raw_dir / f"{code}.csv"
            if not raw_path.is_file():
                skipped.append({"code": code, "reason": "raw csv missing", "path": _display_path(raw_path)})
                continue

            artifact_relative = Path(run_id) / "charts" / _safe_segment(batch.id) / f"{_safe_segment(code)}_day.jpg"
            artifact_path = self.artifact_root / artifact_relative
            try:
                _export_daily_chart(raw_path=raw_path, code=code, out_path=artifact_path, bars=request.bars)
            except Exception as exc:
                skipped.append({"code": code, "reason": str(exc), "path": _display_path(raw_path)})
                continue

            artifacts.append(
                {
                    "id": uuid4().hex,
                    "run_id": run_id,
                    "kind": "chart",
                    "path": artifact_relative.as_posix(),
                    "content_type": "image/jpeg",
                    "metadata_json": {
                        "source": "product:chart_export",
                        "candidate_batch_id": batch.id,
                        "candidate_run_id": batch.run_id,
                        "pick_date": batch.pick_date,
                        "code": code,
                        "strategies": sorted({candidate.strategy for candidate in candidates}),
                        "raw_path": _display_path(raw_path),
                        "bars": request.bars,
                    },
                }
            )

        if detail.candidates and not artifacts:
            raise ChartExportValidationError("no charts were exported for the selected candidate batch")

        created_artifacts = self.run_repository.create_artifacts(artifacts)
        summary = {
            "mode": "chart_export",
            "candidate_batch_id": batch.id,
            "candidate_run_id": batch.run_id,
            "pick_date": batch.pick_date,
            "raw_dir": _display_path(raw_dir),
            "artifact_root": _display_path(self.artifact_root),
            "candidate_count": len(detail.candidates),
            "unique_code_count": len(candidates_by_code),
            "exported_count": len(created_artifacts),
            "skipped_count": len(skipped),
            "skipped": skipped,
        }
        return CreatedChartExport(artifacts=created_artifacts, summary=summary)


def _export_daily_chart(*, raw_path: Path, code: str, out_path: Path, bars: int) -> None:
    from PIL import Image, ImageDraw, ImageFont

    rows = _load_raw_rows(raw_path)
    if bars > 0:
        rows = rows[-bars:]
    if not rows:
        raise ChartExportValidationError("raw csv contains no rows")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1400, 760
    margin_l, margin_r = 74, 28
    price_top, price_bottom = 82, 500
    vol_top, vol_bottom = 560, 710
    chart_w = width - margin_l - margin_r
    step = chart_w / max(len(rows), 1)
    body_w = max(3, int(step * 0.58))

    prices = [value for row in rows for value in (row["open"], row["high"], row["low"], row["close"])]
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
    draw.text((margin_l, 28), f"{code} daily {first_date} to {last_date}", fill="#111827", font=title_font)
    for y in _linspace(price_top, price_bottom, 5):
        draw.line((margin_l, y, width - margin_r, y), fill="#e5e7eb", width=1)
    for y in _linspace(vol_top, vol_bottom, 3):
        draw.line((margin_l, y, width - margin_r, y), fill="#edf2f7", width=1)
    draw.rectangle((margin_l, price_top, width - margin_r, price_bottom), outline="#d1d5db", width=1)
    draw.rectangle((margin_l, vol_top, width - margin_r, vol_bottom), outline="#d1d5db", width=1)

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


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)
