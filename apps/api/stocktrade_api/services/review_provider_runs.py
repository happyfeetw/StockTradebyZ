from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any, Callable, Protocol
from uuid import uuid4

from stocktrade.domain.review import candidate_review_key, result_review_key

from ..schemas.reviews import ReviewProviderRunCreateRequest, ReviewRunCreateRequest
from .cancellation import CancellationCheck, raise_if_cancelled
from ..storage.duckdb import DuckDBAnalyticsWriter
from ..storage.review_repository import CreatedReviewRun, ReviewRepository
from ..storage.run_repository import RunRepository
from ..storage.sqlite import ROOT
from ..storage.sqlite_models import Artifact, Candidate, CandidateBatch
from .review_runs import ReviewRunService


class ReviewProviderValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewProviderItem:
    candidate_id: int
    code: str
    strategy: str
    review_key: str
    candidate: dict[str, Any]
    chart_artifact_id: str | None
    chart_path: str | None


@dataclass(frozen=True)
class ReviewProviderInput:
    candidate_batch_id: str
    pick_date: str
    provider: str
    reviewer: str
    items: list[ReviewProviderItem]
    provider_config: dict[str, Any]


class ReviewProviderExecutor(Protocol):
    def run(self, request: ReviewProviderInput) -> list[dict[str, Any]]:
        ...


class UnconfiguredReviewProviderExecutor:
    def run(self, request: ReviewProviderInput) -> list[dict[str, Any]]:
        raise ReviewProviderValidationError(f"review provider executor is not configured: {request.provider}")


class ReviewProviderRunService:
    def __init__(
        self,
        repository: ReviewRepository,
        *,
        executor: ReviewProviderExecutor,
        analytics_writer: DuckDBAnalyticsWriter | None = None,
        run_repository: RunRepository | None = None,
        artifact_root: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.executor = executor
        self.run_repository = run_repository
        self.artifact_root = _resolve_artifact_root(artifact_root) if artifact_root is not None else None
        self.review_service = ReviewRunService(repository, analytics_writer=analytics_writer)

    def run(
        self,
        *,
        run_id: str,
        request: ReviewProviderRunCreateRequest,
        should_cancel: CancellationCheck | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> CreatedReviewRun:
        sources = self.repository.get_review_provider_sources(request.candidate_batch_id)
        raise_if_cancelled(should_cancel)
        _report_provider_progress(
            progress_callback,
            phase="读取候选",
            current=0,
            total=0,
            message="正在读取 provider 复评输入",
            force=True,
        )
        items = _provider_items(
            sources.candidate_batch,
            chart_artifacts_by_review_key=sources.chart_artifacts_by_review_key,
            chart_artifacts_by_code=sources.chart_artifacts_by_code,
            codes=request.codes,
            strategies=request.strategies,
            require_charts=request.require_charts,
        )
        if not items:
            raise ReviewProviderValidationError("candidate filters selected no provider review items")
        _report_provider_progress(
            progress_callback,
            phase="调用复评提供方",
            current=0,
            total=len(items),
            message=f"准备复评 {len(items)} 个候选",
            force=True,
        )

        reviewer = request.reviewer or request.provider
        provider_input = ReviewProviderInput(
            candidate_batch_id=sources.candidate_batch.id,
            pick_date=sources.candidate_batch.pick_date,
            provider=request.provider,
            reviewer=reviewer,
            items=items,
            provider_config=request.provider_config,
        )
        raise_if_cancelled(should_cancel)
        raw_results = self.executor.run(provider_input)
        raise_if_cancelled(should_cancel)
        _report_provider_progress(
            progress_callback,
            phase="解析复评结果",
            current=len(raw_results),
            total=len(items),
            message=f"复评提供方返回 {len(raw_results)} 条结果",
            force=True,
        )
        results = _results_with_lineage(raw_results, items=items, reviewer=reviewer)
        if self.run_repository is not None and self.artifact_root is not None:
            raise_if_cancelled(should_cancel)
            _attach_provider_evidence_artifacts(
                results,
                run_id=run_id,
                provider=request.provider,
                candidate_batch_id=sources.candidate_batch.id,
                pick_date=sources.candidate_batch.pick_date,
                run_repository=self.run_repository,
                artifact_root=self.artifact_root,
            )
        for result in results:
            result.pop("provider_evidence_files", None)
        source = {
            "mode": "review_provider",
            "provider": request.provider,
            "candidate_batch_id": sources.candidate_batch.id,
            "pick_date": sources.candidate_batch.pick_date,
            "selected_candidates": len(items),
            "chart_artifact_count": sum(1 for item in items if item.chart_artifact_id),
            "require_charts": request.require_charts,
            "filters": {
                "codes": list(request.codes or []),
                "strategies": list(request.strategies or []),
            },
        }
        review_request = ReviewRunCreateRequest(
            candidate_batch_id=sources.candidate_batch.id,
            provider=request.provider,
            reviewer=reviewer,
            min_score=request.min_score,
            classic_pattern_config=request.classic_pattern_config,
            results=results,
        )
        raise_if_cancelled(should_cancel)
        return self.review_service.run(
            run_id=run_id,
            request=review_request,
            source=source,
            should_cancel=should_cancel,
            progress_callback=progress_callback,
        )


def _provider_items(
    batch: CandidateBatch,
    *,
    chart_artifacts_by_review_key: dict[str, Artifact],
    chart_artifacts_by_code: dict[str, Artifact],
    codes: list[str] | None,
    strategies: list[str] | None,
    require_charts: bool,
) -> list[ReviewProviderItem]:
    code_filter = {str(code).strip() for code in (codes or []) if str(code).strip()}
    strategy_filter = {str(strategy).strip() for strategy in (strategies or []) if str(strategy).strip()}
    items: list[ReviewProviderItem] = []
    missing_charts: list[str] = []
    for candidate in batch.candidates:
        if code_filter and candidate.code not in code_filter:
            continue
        if strategy_filter and candidate.strategy not in strategy_filter:
            continue
        payload = _candidate_payload(candidate)
        review_key = candidate_review_key(payload)
        chart_artifact = chart_artifacts_by_review_key.get(review_key) or chart_artifacts_by_code.get(candidate.code)
        if require_charts and chart_artifact is None:
            missing_charts.append(review_key)
        items.append(
            ReviewProviderItem(
                candidate_id=candidate.id,
                code=candidate.code,
                strategy=candidate.strategy,
                review_key=review_key,
                candidate=payload,
                chart_artifact_id=chart_artifact.id if chart_artifact else None,
                chart_path=chart_artifact.path if chart_artifact else None,
            )
        )
    if missing_charts:
        raise ReviewProviderValidationError(
            "missing chart artifacts for provider review items: " + ", ".join(sorted(missing_charts))
        )
    return items


def _results_with_lineage(
    raw_results: list[dict[str, Any]],
    *,
    items: list[ReviewProviderItem],
    reviewer: str,
) -> list[dict[str, Any]]:
    expected = {item.review_key: item for item in items}
    seen: dict[str, dict[str, Any]] = {}
    for raw_result in raw_results:
        result = dict(raw_result)
        review_key = result_review_key(result)
        if not review_key:
            raise ReviewProviderValidationError("provider result is missing code/strategy identity")
        if review_key in seen:
            raise ReviewProviderValidationError(f"duplicate provider result for {review_key}")
        seen[review_key] = result

    missing = sorted(set(expected) - set(seen))
    extra = sorted(set(seen) - set(expected))
    if missing:
        raise ReviewProviderValidationError("provider result missing review items: " + ", ".join(missing))
    if extra:
        raise ReviewProviderValidationError("provider returned items outside selected batch: " + ", ".join(extra))

    results: list[dict[str, Any]] = []
    for item in items:
        result = seen[item.review_key]
        result["code"] = item.code
        result["strategy"] = item.strategy
        result["review_key"] = item.review_key
        result["reviewer"] = str(result.get("reviewer") or reviewer)
        result["chart_artifact_id"] = item.chart_artifact_id
        result["chart_path"] = item.chart_path
        result["provider_source"] = {
            "candidate_batch_id": item.candidate.get("batch_id"),
            "candidate_id": item.candidate_id,
            "chart_artifact_id": item.chart_artifact_id,
            "chart_path": item.chart_path,
        }
        results.append(result)
    return results


def _candidate_payload(candidate: Candidate) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "batch_id": candidate.batch_id,
        "candidate_id": candidate.id,
        "code": candidate.code,
        "strategy": candidate.strategy,
        "close": candidate.close,
        "turnover_n": candidate.turnover_n,
        "brick_growth": candidate.brick_growth,
    }
    if candidate.extra_json:
        payload.update(candidate.extra_json)
    return payload


def _report_provider_progress(
    progress_callback: Callable[[dict[str, Any]], None] | None,
    *,
    phase: str,
    current: int,
    total: int,
    message: str,
    force: bool = False,
) -> None:
    if progress_callback is None:
        return
    progress_callback(
        {
            "label": "复评进度",
            "phase": phase,
            "current": current,
            "total": total,
            "unit": "候选",
            "message": message,
            "force": force,
        }
    )


def _attach_provider_evidence_artifacts(
    results: list[dict[str, Any]],
    *,
    run_id: str,
    provider: str,
    candidate_batch_id: str,
    pick_date: str,
    run_repository: RunRepository,
    artifact_root: Path,
) -> None:
    records: dict[Path, dict[str, Any]] = {}
    sources_by_review_key: dict[str, list[Path]] = {}
    for result in results:
        review_key = result_review_key(result)
        if not review_key:
            continue
        for entry in _provider_evidence_entries(result.get("provider_evidence_files")):
            source = _product_evidence_source_path(entry["path"], artifact_root)
            if source is None:
                continue
            record = records.setdefault(source, {"roles": set(), "review_keys": set()})
            record["roles"].add(entry["role"])
            record["review_keys"].add(review_key)
            sources_by_review_key.setdefault(review_key, [])
            if source not in sources_by_review_key[review_key]:
                sources_by_review_key[review_key].append(source)

    if not records:
        return

    artifacts: list[dict[str, Any]] = []
    source_to_artifact_path: dict[Path, str] = {}
    for index, (source, record) in enumerate(sorted(records.items(), key=lambda item: item[0].as_posix()), 1):
        role_label = _safe_artifact_part("-".join(sorted(record["roles"])))
        target_name = f"{index:03d}-{role_label}-{_safe_artifact_part(source.name)}"
        target_relative = f"{run_id}/provider-evidence/{target_name}"
        target = artifact_root / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_to_artifact_path[source] = target_relative
        artifacts.append(
            {
                "id": f"artifact-{uuid4().hex}",
                "run_id": run_id,
                "kind": "provider_evidence",
                "path": target_relative,
                "content_type": _content_type_for_path(source),
                "metadata_json": {
                    "source": "product:review_provider_evidence",
                    "provider": provider,
                    "candidate_batch_id": candidate_batch_id,
                    "pick_date": pick_date,
                    "roles": sorted(record["roles"]),
                    "review_keys": sorted(record["review_keys"]),
                    "source_relative_path": source.relative_to(artifact_root).as_posix(),
                },
            }
        )

    created_by_path = {artifact.path: artifact.id for artifact in run_repository.create_artifacts(artifacts)}
    for result in results:
        review_key = result_review_key(result)
        if not review_key:
            continue
        evidence_paths = [source_to_artifact_path[source] for source in sources_by_review_key.get(review_key, [])]
        evidence_ids = [created_by_path[path] for path in evidence_paths if path in created_by_path]
        if not evidence_ids:
            continue
        provider_source = result.setdefault("provider_source", {})
        provider_source["provider_evidence_artifact_ids"] = evidence_ids
        provider_source["provider_evidence_paths"] = evidence_paths


def _provider_evidence_entries(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str):
            entries.append({"role": "provider_evidence", "path": item})
        elif isinstance(item, dict) and item.get("path"):
            entries.append(
                {
                    "role": str(item.get("role") or "provider_evidence"),
                    "path": str(item["path"]),
                }
            )
    return entries


def _product_evidence_source_path(path_value: str, artifact_root: Path) -> Path | None:
    raw_path = Path(path_value).expanduser()
    if raw_path.is_absolute():
        source = raw_path.resolve(strict=False)
    elif len(raw_path.parts) >= 2 and raw_path.parts[:2] == ("var", "artifacts"):
        source = (ROOT / raw_path).resolve(strict=False)
    else:
        source = (artifact_root / raw_path).resolve(strict=False)

    if source.is_symlink() or not source.is_file():
        return None
    if not _is_relative_to(source, artifact_root):
        return None
    return source


def _resolve_artifact_root(path: str | Path) -> Path:
    root = Path(path).expanduser()
    if not root.is_absolute():
        root = ROOT / root
    return root.resolve(strict=False)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_artifact_part(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return safe[:80] or "evidence"


def _content_type_for_path(path: Path) -> str | None:
    suffix = path.suffix.lower()
    return {
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".txt": "text/plain",
        ".log": "text/plain",
    }.get(suffix)
