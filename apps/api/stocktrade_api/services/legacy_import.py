from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..schemas.migrations import (
    LegacyCandidateImportPlan,
    LegacyCandidateImportRecord,
    LegacyRecommendationImportRecord,
    LegacyReviewImportPlan,
    LegacyReviewImportRecord,
    LegacyImportDryRunReport,
    LegacyImportIssue,
    LegacyImportSectionReport,
    LegacyImportTotals,
)

LEGACY_SECTIONS = ("candidates", "reviews", "history")
LEGACY_CANDIDATE_FIELDS = {"code", "date", "strategy", "close", "turnover_n", "brick_growth"}


class LegacyCandidateImportError(ValueError):
    def __init__(self, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


class LegacyReviewImportError(LegacyCandidateImportError):
    pass


def legacy_review_key(code: str, strategy: str = "") -> str:
    suffix = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(strategy or "").strip())
    return f"{code}_{suffix}" if suffix else code


@dataclass
class SectionAccumulator:
    files_seen: int = 0
    files_valid: int = 0
    records_seen: int = 0
    records_valid: int = 0
    by_kind: Counter[str] = field(default_factory=Counter)

    def report(self) -> LegacyImportSectionReport:
        return LegacyImportSectionReport(
            files_seen=self.files_seen,
            files_valid=self.files_valid,
            records_seen=self.records_seen,
            records_valid=self.records_valid,
            by_kind=dict(sorted(self.by_kind.items())),
        )


class LegacyImportDryRunScanner:
    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root).expanduser()
        self.sections = {section: SectionAccumulator() for section in LEGACY_SECTIONS}
        self.warnings: list[LegacyImportIssue] = []
        self.quarantine: list[LegacyImportIssue] = []

    def scan(self) -> LegacyImportDryRunReport:
        self._scan_candidates()
        self._scan_reviews()
        self._scan_history()
        return self._report()

    def _source_path(self, path: Path) -> str:
        try:
            return path.relative_to(self.data_root).as_posix()
        except ValueError:
            return path.as_posix()

    def _warning(self, section: str, path: Path, reason: str, message: str, record_key: str | None = None) -> None:
        self.warnings.append(
            LegacyImportIssue(
                section=section,
                source_path=self._source_path(path),
                reason=reason,
                message=message,
                record_key=record_key,
            )
        )

    def _quarantine(self, section: str, path: Path, reason: str, message: str, record_key: str | None = None) -> None:
        self.quarantine.append(
            LegacyImportIssue(
                section=section,
                source_path=self._source_path(path),
                reason=reason,
                message=message,
                record_key=record_key,
            )
        )

    def _load_json(self, section: str, path: Path) -> Any | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self._quarantine(section, path, "malformed_json", str(exc))
        except OSError as exc:
            self._quarantine(section, path, "unreadable_file", str(exc))
        return None

    def _json_files(self, section: str, root: Path, *, recursive: bool = False) -> list[Path]:
        if not root.exists():
            self._warning(section, root, "missing_directory", "legacy directory does not exist")
            return []
        pattern = "**/*" if recursive else "*"
        files = sorted(path for path in root.glob(pattern) if path.is_file())
        json_files: list[Path] = []
        for path in files:
            self.sections[section].files_seen += 1
            if path.suffix.lower() != ".json":
                self._warning(section, path, "unsupported_file", "only JSON legacy files are scanned")
                continue
            json_files.append(path)
        return json_files

    def _scan_candidates(self) -> None:
        section = "candidates"
        for path in self._json_files(section, self.data_root / "candidates"):
            if path.name != "candidates_latest.json" and not re.fullmatch(r"candidates_\d{4}-\d{2}-\d{2}\.json", path.name):
                self._warning(section, path, "unsupported_candidate_file", "candidate file name is outside the legacy contract")
                continue
            payload = self._load_json(section, path)
            if not isinstance(payload, dict):
                if payload is not None:
                    self._quarantine(section, path, "invalid_candidate_payload", "candidate file root must be an object")
                continue
            candidates = payload.get("candidates")
            if not isinstance(candidates, list):
                self._quarantine(section, path, "invalid_candidate_payload", "candidate file must contain a candidates list")
                continue
            if not payload.get("run_date") or not payload.get("pick_date"):
                self._quarantine(section, path, "missing_candidate_run_fields", "run_date and pick_date are required")
                continue

            self.sections[section].files_valid += 1
            self.sections[section].by_kind["latest" if path.name == "candidates_latest.json" else "dated"] += 1
            seen_keys: set[tuple[str, str]] = set()
            for index, candidate in enumerate(candidates):
                self.sections[section].records_seen += 1
                if not isinstance(candidate, dict):
                    self._quarantine(section, path, "invalid_candidate_record", "candidate row must be an object", str(index))
                    continue
                code = str(candidate.get("code") or "")
                strategy = str(candidate.get("strategy") or "")
                record_key = f"{code}:{strategy}" if code or strategy else str(index)
                missing = [field for field in ("code", "date", "strategy", "close", "turnover_n") if candidate.get(field) in (None, "")]
                if missing:
                    self._quarantine(
                        section,
                        path,
                        "missing_candidate_fields",
                        f"candidate missing fields: {', '.join(missing)}",
                        record_key,
                    )
                    continue
                identity = (code, strategy)
                if identity in seen_keys:
                    self._quarantine(
                        section,
                        path,
                        "duplicate_candidate_identity",
                        "duplicate (code, strategy) within one candidate batch",
                        record_key,
                    )
                    continue
                seen_keys.add(identity)
                self.sections[section].records_valid += 1

    def _scan_reviews(self) -> None:
        section = "reviews"
        for path in self._json_files(section, self.data_root / "review", recursive=True):
            payload = self._load_json(section, path)
            if payload is None:
                continue
            if path.name == "suggestion.json":
                self._scan_suggestion(path, payload)
            else:
                self._scan_review_result(path, payload)

    def _scan_suggestion(self, path: Path, payload: Any) -> None:
        section = "reviews"
        if not isinstance(payload, dict):
            self._quarantine(section, path, "invalid_suggestion_payload", "suggestion root must be an object")
            return
        recommendations = payload.get("recommendations") or []
        if not isinstance(recommendations, list):
            self._quarantine(section, path, "invalid_suggestion_payload", "recommendations must be a list")
            return
        self.sections[section].files_valid += 1
        self.sections[section].by_kind["suggestion"] += 1
        for index, item in enumerate(recommendations):
            self.sections[section].records_seen += 1
            if not isinstance(item, dict):
                self._quarantine(section, path, "invalid_recommendation_record", "recommendation row must be an object", str(index))
                continue
            code = str(item.get("code") or "")
            strategy = str(item.get("strategy") or "")
            expected = legacy_review_key(code, strategy)
            record_key = str(item.get("review_key") or expected)
            if not code:
                self._quarantine(section, path, "missing_recommendation_code", "recommendation code is required", str(index))
                continue
            if item.get("review_key") and item.get("review_key") != expected:
                self._quarantine(section, path, "review_key_mismatch", "recommendation review_key does not match (code, strategy)", record_key)
                continue
            self.sections[section].records_valid += 1

    def _scan_review_result(self, path: Path, payload: Any) -> None:
        section = "reviews"
        if not isinstance(payload, dict):
            self._quarantine(section, path, "invalid_review_payload", "review file root must be an object")
            return
        self.sections[section].files_valid += 1
        self.sections[section].by_kind["review"] += 1
        self.sections[section].records_seen += 1
        code = str(payload.get("code") or "")
        strategy = str(payload.get("strategy") or "")
        if not code:
            self._quarantine(section, path, "missing_review_code", "review code is required")
            return
        expected = legacy_review_key(code, strategy)
        stored_key = str(payload.get("review_key") or "")
        if stored_key and stored_key != expected:
            self._quarantine(section, path, "review_key_mismatch", "review_key does not match (code, strategy)", stored_key)
            return
        if not stored_key:
            self._warning(section, path, "missing_review_key", "review_key will be derived from (code, strategy)", expected)
        self.sections[section].records_valid += 1

    def _scan_history(self) -> None:
        section = "history"
        history_root = self.data_root / "history"
        for path in self._json_files(section, history_root, recursive=True):
            if path.name == "index.json" and path.parent == history_root:
                self._scan_history_index(path)
            elif path.name == "summary.json":
                self._scan_history_summary(path)
            elif path.name == "all.json":
                self._scan_history_rows(path)
            else:
                self._warning(section, path, "unsupported_history_file", "history file is outside the import dry-run contract")

    def _scan_history_index(self, path: Path) -> None:
        section = "history"
        payload = self._load_json(section, path)
        if not isinstance(payload, dict):
            if payload is not None:
                self._quarantine(section, path, "invalid_history_index", "history index root must be an object")
            return
        dates = payload.get("dates") or []
        if not isinstance(dates, list):
            self._quarantine(section, path, "invalid_history_index", "history index dates must be a list")
            return
        self.sections[section].files_valid += 1
        self.sections[section].by_kind["index"] += 1
        self.sections[section].records_seen += len(dates)
        self.sections[section].records_valid += len([item for item in dates if isinstance(item, dict) and item.get("date")])

    def _scan_history_summary(self, path: Path) -> None:
        section = "history"
        payload = self._load_json(section, path)
        if not isinstance(payload, dict):
            if payload is not None:
                self._quarantine(section, path, "invalid_history_summary", "history summary root must be an object")
            return
        if not payload.get("date") or not payload.get("run_id"):
            self._quarantine(section, path, "missing_history_summary_fields", "history summary requires date and run_id")
            return
        self.sections[section].files_valid += 1
        self.sections[section].by_kind["summary"] += 1
        self.sections[section].records_seen += 1
        self.sections[section].records_valid += 1

    def _scan_history_rows(self, path: Path) -> None:
        section = "history"
        payload = self._load_json(section, path)
        if not isinstance(payload, list):
            if payload is not None:
                self._quarantine(section, path, "invalid_history_rows", "history all.json root must be a list")
            return
        self.sections[section].files_valid += 1
        self.sections[section].by_kind["all"] += 1
        seen_keys: set[str] = set()
        for index, row in enumerate(payload):
            self.sections[section].records_seen += 1
            if not isinstance(row, dict):
                self._quarantine(section, path, "invalid_history_row", "history row must be an object", str(index))
                continue
            code = str(row.get("code") or "")
            strategy = str(row.get("strategy") or "")
            if not code:
                self._quarantine(section, path, "missing_history_code", "history row code is required", str(index))
                continue
            expected = legacy_review_key(code, strategy)
            stored_key = str(row.get("review_key") or "")
            if stored_key and stored_key != expected:
                self._quarantine(section, path, "review_key_mismatch", "history review_key does not match (code, strategy)", stored_key)
                continue
            record_key = stored_key or expected
            if record_key in seen_keys:
                self._quarantine(section, path, "duplicate_history_review_key", "duplicate review_key in history rows", record_key)
                continue
            seen_keys.add(record_key)
            self.sections[section].records_valid += 1

    def _report(self) -> LegacyImportDryRunReport:
        section_reports = {section: accumulator.report() for section, accumulator in self.sections.items()}
        totals = LegacyImportTotals(
            files_seen=sum(report.files_seen for report in section_reports.values()),
            files_valid=sum(report.files_valid for report in section_reports.values()),
            records_seen=sum(report.records_seen for report in section_reports.values()),
            records_valid=sum(report.records_valid for report in section_reports.values()),
            warning_count=len(self.warnings),
            quarantine_count=len(self.quarantine),
        )
        return LegacyImportDryRunReport(
            dry_run=True,
            data_root=self.data_root.as_posix(),
            sections=section_reports,
            totals=totals,
            warnings=sorted(self.warnings, key=lambda item: (item.section, item.source_path, item.reason, item.record_key or "")),
            quarantine=sorted(self.quarantine, key=lambda item: (item.section, item.source_path, item.reason, item.record_key or "")),
        )


def scan_legacy_import_dry_run(data_root: str | Path) -> LegacyImportDryRunReport:
    return LegacyImportDryRunScanner(data_root).scan()


def load_legacy_candidate_import_plan(data_root: str | Path, pick_date: str) -> LegacyCandidateImportPlan:
    root = Path(data_root).expanduser()
    source_path = root / "candidates" / f"candidates_{pick_date}.json"
    payload = _load_candidate_import_payload(source_path)
    if payload.get("pick_date") != pick_date:
        raise LegacyCandidateImportError("candidate file pick_date does not match requested pick_date")

    run_date = str(payload.get("run_date") or "")
    if not run_date:
        raise LegacyCandidateImportError("candidate file requires run_date")

    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise LegacyCandidateImportError("candidate file must contain a candidates list")

    records: list[LegacyCandidateImportRecord] = []
    seen_keys: set[tuple[str, str]] = set()
    strategy_counts: Counter[str] = Counter()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise LegacyCandidateImportError(f"candidate row {index} must be an object")

        missing = [
            field
            for field in ("code", "date", "strategy", "close", "turnover_n")
            if candidate.get(field) in (None, "")
        ]
        if missing:
            raise LegacyCandidateImportError(f"candidate row {index} missing fields: {', '.join(missing)}")

        code = str(candidate["code"])
        strategy = str(candidate["strategy"])
        identity = (code, strategy)
        if identity in seen_keys:
            raise LegacyCandidateImportError(f"duplicate candidate identity in import file: {code}:{strategy}")
        seen_keys.add(identity)

        candidate_date = str(candidate["date"])
        if candidate_date != pick_date:
            raise LegacyCandidateImportError(f"candidate row {index} date does not match requested pick_date")

        extra = {str(key): value for key, value in candidate.items() if key not in LEGACY_CANDIDATE_FIELDS}
        records.append(
            LegacyCandidateImportRecord(
                code=code,
                date=candidate_date,
                strategy=strategy,
                close=_candidate_float(candidate["close"], f"candidate row {index} close"),
                turnover_n=_candidate_float(candidate["turnover_n"], f"candidate row {index} turnover_n"),
                brick_growth=_candidate_optional_float(
                    candidate.get("brick_growth"),
                    f"candidate row {index} brick_growth",
                ),
                extra=extra,
            )
        )
        strategy_counts[strategy] += 1

    return LegacyCandidateImportPlan(
        data_root=root.as_posix(),
        source_path=_relative_source_path(root, source_path),
        run_date=run_date,
        pick_date=pick_date,
        strategy_counts=dict(sorted(strategy_counts.items())),
        candidates=records,
    )


def build_legacy_candidate_import_report(plan: LegacyCandidateImportPlan) -> LegacyImportDryRunReport:
    section_reports = {section: LegacyImportSectionReport() for section in LEGACY_SECTIONS}
    section_reports["candidates"] = LegacyImportSectionReport(
        files_seen=1,
        files_valid=1,
        records_seen=len(plan.candidates),
        records_valid=len(plan.candidates),
        by_kind={"dated": 1},
    )
    totals = LegacyImportTotals(
        files_seen=1,
        files_valid=1,
        records_seen=len(plan.candidates),
        records_valid=len(plan.candidates),
        warning_count=0,
        quarantine_count=0,
    )
    return LegacyImportDryRunReport(
        dry_run=False,
        data_root=plan.data_root,
        sections=section_reports,
        totals=totals,
        warnings=[],
        quarantine=[],
    )


def load_legacy_review_import_plan(data_root: str | Path, pick_date: str) -> LegacyReviewImportPlan:
    root = Path(data_root).expanduser()
    review_root = root / "review" / pick_date
    if not review_root.exists():
        raise LegacyReviewImportError("review directory does not exist", status_code=404)
    if not review_root.is_dir():
        raise LegacyReviewImportError("review path must be a directory")

    suggestion_payload = _load_optional_review_json(review_root / "suggestion.json")
    provider = _review_provider(suggestion_payload)
    reviews: list[LegacyReviewImportRecord] = []
    seen_review_keys: set[str] = set()
    for path in sorted(item for item in review_root.glob("*.json") if item.name != "suggestion.json"):
        payload = _load_review_payload(path)
        record = _review_record_from_payload(root, path, payload)
        if record.review_key in seen_review_keys:
            raise LegacyReviewImportError(f"duplicate review_key in import directory: {record.review_key}")
        seen_review_keys.add(record.review_key)
        reviews.append(record)

    recommendations = _recommendations_from_suggestion(root, review_root / "suggestion.json", suggestion_payload)
    reviewed_keys = {record.review_key for record in reviews}
    missing_review_keys = [record.review_key for record in recommendations if record.review_key not in reviewed_keys]
    if missing_review_keys:
        raise LegacyReviewImportError(
            f"recommendations reference missing review files: {', '.join(sorted(missing_review_keys))}"
        )

    if not reviews and not recommendations:
        raise LegacyReviewImportError("review directory contains no importable review records")

    return LegacyReviewImportPlan(
        data_root=root.as_posix(),
        source_path=_relative_source_path(root, review_root),
        pick_date=pick_date,
        provider=provider,
        reviews=reviews,
        recommendations=recommendations,
    )


def build_legacy_review_import_report(plan: LegacyReviewImportPlan) -> LegacyImportDryRunReport:
    section_reports = {section: LegacyImportSectionReport() for section in LEGACY_SECTIONS}
    files_valid = len(plan.reviews) + (1 if plan.recommendations else 0)
    section_reports["reviews"] = LegacyImportSectionReport(
        files_seen=files_valid,
        files_valid=files_valid,
        records_seen=len(plan.reviews) + len(plan.recommendations),
        records_valid=len(plan.reviews) + len(plan.recommendations),
        by_kind={
            "review": len(plan.reviews),
            **({"suggestion": 1} if plan.recommendations else {}),
        },
    )
    totals = LegacyImportTotals(
        files_seen=files_valid,
        files_valid=files_valid,
        records_seen=len(plan.reviews) + len(plan.recommendations),
        records_valid=len(plan.reviews) + len(plan.recommendations),
        warning_count=0,
        quarantine_count=0,
    )
    return LegacyImportDryRunReport(
        dry_run=False,
        data_root=plan.data_root,
        sections=section_reports,
        totals=totals,
        warnings=[],
        quarantine=[],
    )


def _review_record_from_payload(root: Path, path: Path, payload: dict[str, Any]) -> LegacyReviewImportRecord:
    code = str(payload.get("code") or "")
    if not code:
        raise LegacyReviewImportError(f"{_relative_source_path(root, path)} requires code")
    strategy = str(payload.get("strategy") or "")
    expected_key = legacy_review_key(code, strategy)
    stored_key = str(payload.get("review_key") or "")
    if stored_key and stored_key != expected_key:
        raise LegacyReviewImportError(
            f"{_relative_source_path(root, path)} review_key does not match (code, strategy)"
        )
    review_key = stored_key or expected_key
    normalized_payload = dict(payload)
    normalized_payload.setdefault("review_key", review_key)
    normalized_payload.setdefault("strategy", strategy)
    normalized_payload.setdefault("legacy_source_path", _relative_source_path(root, path))
    return LegacyReviewImportRecord(
        code=code,
        strategy=strategy,
        review_key=review_key,
        verdict=_optional_string(payload.get("verdict")),
        total_score=_candidate_optional_float(payload.get("total_score"), f"{_relative_source_path(root, path)} total_score"),
        reviewer=_optional_string(payload.get("reviewer")),
        payload=normalized_payload,
    )


def _recommendations_from_suggestion(
    root: Path,
    path: Path,
    payload: dict[str, Any] | None,
) -> list[LegacyRecommendationImportRecord]:
    if payload is None:
        return []
    if payload.get("date") and str(payload.get("date")) != path.parent.name:
        raise LegacyReviewImportError("suggestion.json date does not match requested pick_date")
    recommendations = payload.get("recommendations") or []
    if not isinstance(recommendations, list):
        raise LegacyReviewImportError("suggestion.json recommendations must be a list")

    records: list[LegacyRecommendationImportRecord] = []
    seen_keys: set[str] = set()
    seen_ranks: set[int] = set()
    for index, item in enumerate(recommendations):
        if not isinstance(item, dict):
            raise LegacyReviewImportError(f"suggestion recommendation row {index} must be an object")
        code = str(item.get("code") or "")
        if not code:
            raise LegacyReviewImportError(f"suggestion recommendation row {index} requires code")
        strategy = str(item.get("strategy") or "")
        expected_key = legacy_review_key(code, strategy)
        stored_key = str(item.get("review_key") or "")
        if stored_key and stored_key != expected_key:
            raise LegacyReviewImportError(f"suggestion recommendation row {index} review_key mismatch")
        review_key = stored_key or expected_key
        rank = _positive_int(item.get("rank") or index + 1, f"suggestion recommendation row {index} rank")
        if review_key in seen_keys:
            raise LegacyReviewImportError(f"duplicate recommendation review_key: {review_key}")
        if rank in seen_ranks:
            raise LegacyReviewImportError(f"duplicate recommendation rank: {rank}")
        seen_keys.add(review_key)
        seen_ranks.add(rank)
        normalized_payload = dict(item)
        normalized_payload.setdefault("review_key", review_key)
        normalized_payload.setdefault("strategy", strategy)
        normalized_payload.setdefault("legacy_source_path", _relative_source_path(root, path))
        records.append(
            LegacyRecommendationImportRecord(
                rank=rank,
                code=code,
                strategy=strategy,
                review_key=review_key,
                verdict=_optional_string(item.get("verdict")),
                total_score=_candidate_optional_float(item.get("total_score"), f"suggestion recommendation row {index} total_score"),
                payload=normalized_payload,
            )
        )
    return sorted(records, key=lambda record: record.rank)


def _review_provider(payload: dict[str, Any] | None) -> str:
    if payload:
        reviewer = _optional_string(payload.get("reviewer"))
        if reviewer:
            return reviewer
    return "legacy-review"


def _load_optional_review_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _load_review_payload(path)


def _load_review_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LegacyReviewImportError(f"{path.name} is malformed JSON: {exc}") from exc
    except OSError as exc:
        raise LegacyReviewImportError(f"{path.name} is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise LegacyReviewImportError(f"{path.name} root must be an object")
    return payload


def _load_candidate_import_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LegacyCandidateImportError("candidate file does not exist", status_code=404) from exc
    except json.JSONDecodeError as exc:
        raise LegacyCandidateImportError(f"candidate file is malformed JSON: {exc}") from exc
    except OSError as exc:
        raise LegacyCandidateImportError(f"candidate file is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise LegacyCandidateImportError("candidate file root must be an object")
    return payload


def _candidate_float(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise LegacyCandidateImportError(f"{label} must be numeric") from exc


def _candidate_optional_float(value: Any, label: str) -> float | None:
    if value in (None, ""):
        return None
    return _candidate_float(value, label)


def _positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise LegacyReviewImportError(f"{label} must be a positive integer") from exc
    if result <= 0:
        raise LegacyReviewImportError(f"{label} must be a positive integer")
    return result


def _optional_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _relative_source_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
