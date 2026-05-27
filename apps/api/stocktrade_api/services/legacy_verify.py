from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..schemas.migrations import (
    LegacyImportVerifyCounts,
    LegacyImportVerifyMismatches,
    LegacyImportVerifyReport,
    LegacyImportVerifyRequest,
)
from ..storage.duckdb import connect_duckdb, resolve_duckdb_path
from ..storage.sqlite_models import ArchiveRow, Candidate, CandidateBatch, Review, ReviewRun
from .legacy_import import (
    load_legacy_archive_import_plan,
    load_legacy_candidate_import_plan,
    load_legacy_review_import_plan,
)


def verify_legacy_import(
    request: LegacyImportVerifyRequest,
    *,
    session_factory: sessionmaker[Session],
    duckdb_path: str | Path | None,
) -> LegacyImportVerifyReport:
    legacy_keys, source_path = _legacy_keys(request)
    sqlite_keys = _sqlite_keys(request, session_factory=session_factory)
    duckdb_keys = _duckdb_keys(request, duckdb_path=duckdb_path)
    mismatches = _mismatches(legacy_keys, sqlite_keys, duckdb_keys)
    duckdb_checked = duckdb_keys is not None
    passed = (
        not mismatches.missing_in_sqlite
        and not mismatches.extra_in_sqlite
        and (not duckdb_checked or (not mismatches.missing_in_duckdb and not mismatches.extra_in_duckdb))
    )
    return LegacyImportVerifyReport(
        passed=passed,
        data_root=Path(request.data_root).expanduser().as_posix(),
        scope=request.scope,
        pick_date=request.pick_date,
        run_id=request.run_id,
        source_path=source_path,
        duckdb_checked=duckdb_checked,
        counts=LegacyImportVerifyCounts(
            legacy=len(legacy_keys),
            sqlite=len(sqlite_keys),
            duckdb=len(duckdb_keys) if duckdb_keys is not None else None,
        ),
        mismatches=mismatches,
    )


def _legacy_keys(request: LegacyImportVerifyRequest) -> tuple[list[str], str]:
    if request.scope == "candidates":
        plan = load_legacy_candidate_import_plan(request.data_root, request.pick_date)
        return [_candidate_key(candidate.code, candidate.strategy) for candidate in plan.candidates], plan.source_path
    if request.scope == "reviews":
        plan = load_legacy_review_import_plan(request.data_root, request.pick_date)
        return [review.review_key for review in plan.reviews], plan.source_path

    plan = load_legacy_archive_import_plan(request.data_root, request.pick_date)
    return [row.review_key for row in plan.rows], plan.source_path


def _sqlite_keys(
    request: LegacyImportVerifyRequest,
    *,
    session_factory: sessionmaker[Session],
) -> list[str]:
    with session_factory() as session:
        if request.scope == "candidates":
            statement = (
                select(Candidate.code, Candidate.strategy)
                .join(CandidateBatch)
                .where(Candidate.pick_date == request.pick_date, CandidateBatch.source == "legacy:candidates")
                .order_by(Candidate.code, Candidate.strategy)
            )
            if request.run_id:
                statement = statement.where(CandidateBatch.run_id == request.run_id)
            return [_candidate_key(code, strategy) for code, strategy in session.execute(statement).all()]

        if request.scope == "reviews":
            statement = (
                select(Review.review_key)
                .join(ReviewRun)
                .where(ReviewRun.pick_date == request.pick_date)
                .order_by(Review.review_key)
            )
            if request.run_id:
                statement = statement.where(ReviewRun.run_id == request.run_id)
            return [str(row[0]) for row in session.execute(statement).all()]

        statement = (
            select(ArchiveRow.review_key)
            .where(ArchiveRow.pick_date == request.pick_date)
            .order_by(ArchiveRow.review_key)
        )
        if request.run_id:
            statement = statement.where(ArchiveRow.run_id == request.run_id)
        return [str(row[0]) for row in session.execute(statement).all()]


def _duckdb_keys(request: LegacyImportVerifyRequest, *, duckdb_path: str | Path | None) -> list[str] | None:
    if duckdb_path is None:
        return None
    resolved = resolve_duckdb_path(duckdb_path)
    if resolved != ":memory:" and not Path(resolved).exists():
        return []

    with connect_duckdb(duckdb_path, read_only=True) as connection:
        if request.scope == "candidates":
            sql = """
                SELECT code, strategy
                FROM candidate_facts
                WHERE pick_date = CAST(? AS DATE)
            """
            params: list[str] = [request.pick_date]
            if request.run_id:
                sql += " AND run_id = ?"
                params.append(request.run_id)
            sql += " ORDER BY code, strategy"
            return [_candidate_key(code, strategy) for code, strategy in connection.execute(sql, params).fetchall()]

        if request.scope == "reviews":
            sql = """
                SELECT review_key
                FROM review_facts
                WHERE pick_date = CAST(? AS DATE)
            """
            params = [request.pick_date]
            if request.run_id:
                sql += " AND run_id = ?"
                params.append(request.run_id)
            sql += " ORDER BY review_key"
            return [str(row[0]) for row in connection.execute(sql, params).fetchall()]

        sql = """
            SELECT payload_json
            FROM archive_facts
            WHERE pick_date = CAST(? AS DATE)
        """
        params = [request.pick_date]
        if request.run_id:
            sql += " AND run_id = ?"
            params.append(request.run_id)
        sql += " ORDER BY code, strategy"
        return sorted(_archive_review_keys(row[0] for row in connection.execute(sql, params).fetchall()))


def _archive_review_keys(payloads: Iterable[str | None]) -> list[str]:
    keys: list[str] = []
    for payload in payloads:
        if not payload:
            continue
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        review_key = parsed.get("review_key")
        if review_key:
            keys.append(str(review_key))
    return keys


def _mismatches(
    legacy_keys: list[str],
    sqlite_keys: list[str],
    duckdb_keys: list[str] | None,
) -> LegacyImportVerifyMismatches:
    legacy_set = set(legacy_keys)
    sqlite_set = set(sqlite_keys)
    duckdb_set = set(duckdb_keys or [])
    return LegacyImportVerifyMismatches(
        missing_in_sqlite=sorted(legacy_set - sqlite_set),
        extra_in_sqlite=sorted(sqlite_set - legacy_set),
        missing_in_duckdb=sorted(legacy_set - duckdb_set) if duckdb_keys is not None else [],
        extra_in_duckdb=sorted(duckdb_set - legacy_set) if duckdb_keys is not None else [],
    )


def _candidate_key(code: str, strategy: str) -> str:
    return f"{code}:{strategy}"
