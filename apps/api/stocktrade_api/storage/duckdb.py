from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import duckdb

from .sqlite_models import (
    ArchiveRow,
    ArchiveSnapshot,
    Candidate,
    CandidateBatch,
    Review,
    ReviewRun,
)

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DUCKDB_PATH = ROOT / "var" / "db" / "analytics.duckdb"
DEFAULT_MIGRATIONS_DIR = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "duckdb" / "versions"


class DuckDBMigrationError(RuntimeError):
    pass


class DuckDBFactWriteError(RuntimeError):
    pass


class DuckDBAnalyticsReadError(RuntimeError):
    pass


@dataclass(frozen=True)
class DuckDBMigration:
    version: str
    path: Path


@dataclass(frozen=True)
class StrategySummaryMetric:
    pick_date: str
    run_id: str
    strategy: str
    total: int
    reviewed: int
    recommended: int
    unreviewed: int


def resolve_duckdb_path(path: str | Path = DEFAULT_DUCKDB_PATH) -> Path | str:
    if str(path) == ":memory:":
        return ":memory:"

    db_path = Path(path).expanduser()
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return db_path


def connect_duckdb(path: str | Path = DEFAULT_DUCKDB_PATH, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    resolved = resolve_duckdb_path(path)
    if resolved != ":memory:":
        assert isinstance(resolved, Path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return duckdb.connect(str(resolved), read_only=read_only)
    return duckdb.connect(resolved, read_only=read_only)


def list_migration_files(migrations_dir: str | Path = DEFAULT_MIGRATIONS_DIR) -> list[DuckDBMigration]:
    migration_root = Path(migrations_dir)
    return [
        DuckDBMigration(version=path.stem, path=path)
        for path in sorted(migration_root.glob("*.sql"))
        if path.is_file()
    ]


def ensure_schema_version_table(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS duckdb_schema_versions (
            version VARCHAR PRIMARY KEY,
            applied_at TIMESTAMP NOT NULL DEFAULT current_timestamp
        )
        """
    )


def applied_versions(connection: duckdb.DuckDBPyConnection) -> set[str]:
    ensure_schema_version_table(connection)
    rows = connection.execute("SELECT version FROM duckdb_schema_versions").fetchall()
    return {str(row[0]) for row in rows}


def apply_migrations(
    path: str | Path = DEFAULT_DUCKDB_PATH,
    *,
    migrations_dir: str | Path = DEFAULT_MIGRATIONS_DIR,
) -> list[str]:
    applied: list[str] = []
    with connect_duckdb(path) as connection:
        ensure_schema_version_table(connection)
        existing_versions = applied_versions(connection)
        for migration in list_migration_files(migrations_dir):
            if migration.version in existing_versions:
                continue

            sql = migration.path.read_text(encoding="utf-8")
            try:
                connection.execute("BEGIN TRANSACTION")
                connection.execute(sql)
                connection.execute(
                    "INSERT INTO duckdb_schema_versions (version) VALUES (?)",
                    [migration.version],
                )
                connection.execute("COMMIT")
            except Exception as exc:
                connection.execute("ROLLBACK")
                raise DuckDBMigrationError(f"failed to apply DuckDB migration {migration.version}") from exc

            existing_versions.add(migration.version)
            applied.append(migration.version)
    return applied


class DuckDBAnalyticsWriter:
    def __init__(self, path: str | Path = DEFAULT_DUCKDB_PATH):
        self.path = path

    def record_candidate_import(
        self,
        *,
        run_id: str,
        batch: CandidateBatch,
        candidates: list[Candidate],
    ) -> None:
        def write(connection: duckdb.DuckDBPyConnection) -> None:
            connection.execute("DELETE FROM candidate_facts WHERE run_id = ? OR batch_id = ?", [run_id, batch.id])
            rows = [
                (
                    candidate.id,
                    candidate.pick_date,
                    run_id,
                    batch.id,
                    candidate.code,
                    candidate.strategy,
                    candidate.close,
                    candidate.turnover_n,
                    candidate.brick_growth,
                    _json_payload(candidate.extra_json),
                )
                for candidate in candidates
            ]
            if rows:
                connection.executemany(
                    """
                    INSERT INTO candidate_facts (
                        candidate_id,
                        pick_date,
                        run_id,
                        batch_id,
                        code,
                        strategy,
                        close,
                        turnover_n,
                        brick_growth,
                        extra_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

        self._write_facts("candidate import facts", write)

    def record_review_import(
        self,
        *,
        run_id: str,
        review_run: ReviewRun,
        reviews: list[Review],
    ) -> None:
        def write(connection: duckdb.DuckDBPyConnection) -> None:
            connection.execute(
                "DELETE FROM review_facts WHERE run_id = ? OR review_run_id = ?",
                [run_id, review_run.id],
            )
            rows = [
                (
                    review.id,
                    review.review_run_id,
                    review_run.pick_date,
                    run_id,
                    review.code,
                    review.strategy,
                    review.review_key,
                    review.verdict,
                    review.total_score,
                    _json_payload(review.payload_json),
                )
                for review in reviews
            ]
            if rows:
                connection.executemany(
                    """
                    INSERT INTO review_facts (
                        review_id,
                        review_run_id,
                        pick_date,
                        run_id,
                        code,
                        strategy,
                        review_key,
                        verdict,
                        total_score,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

        self._write_facts("review import facts", write)

    def record_archive_import(
        self,
        *,
        run_id: str,
        snapshot: ArchiveSnapshot,
        rows: list[ArchiveRow],
    ) -> None:
        def write(connection: duckdb.DuckDBPyConnection) -> None:
            connection.execute(
                "DELETE FROM archive_facts WHERE pick_date = ? AND run_id = ?",
                [snapshot.pick_date, run_id],
            )
            connection.execute(
                "DELETE FROM strategy_run_metrics WHERE pick_date = ? AND run_id = ?",
                [snapshot.pick_date, run_id],
            )
            archive_rows = [
                (
                    row.pick_date,
                    run_id,
                    row.code,
                    row.strategy,
                    row.status,
                    row.rank,
                    row.chart_artifact_id,
                    _json_payload(_archive_payload(row, snapshot)),
                )
                for row in rows
            ]
            if archive_rows:
                connection.executemany(
                    """
                    INSERT INTO archive_facts (
                        pick_date,
                        run_id,
                        code,
                        strategy,
                        status,
                        rank,
                        chart_artifact_id,
                        payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    archive_rows,
                )
            metrics_rows = [
                (
                    snapshot.pick_date,
                    run_id,
                    strategy,
                    metrics["total"],
                    metrics["reviewed"],
                    metrics["recommended"],
                    metrics["unreviewed"],
                )
                for strategy, metrics in sorted(_strategy_metrics(rows).items())
            ]
            if metrics_rows:
                connection.executemany(
                    """
                    INSERT INTO strategy_run_metrics (
                        pick_date,
                        run_id,
                        strategy,
                        total,
                        reviewed,
                        recommended,
                        unreviewed
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    metrics_rows,
                )

        self._write_facts("archive import facts", write)

    def _write_facts(self, label: str, writer: Callable[[duckdb.DuckDBPyConnection], None]) -> None:
        try:
            apply_migrations(self.path)
            with connect_duckdb(self.path) as connection:
                connection.execute("BEGIN TRANSACTION")
                try:
                    writer(connection)
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
        except Exception as exc:
            raise DuckDBFactWriteError(f"failed to write DuckDB {label}") from exc


class DuckDBAnalyticsReader:
    def __init__(self, path: str | Path = DEFAULT_DUCKDB_PATH):
        self.path = path

    def strategy_summary(
        self,
        *,
        pick_date: str | None = None,
        run_id: str | None = None,
        strategy: str | None = None,
        limit: int = 100,
    ) -> list[StrategySummaryMetric]:
        clauses: list[str] = []
        params: list[Any] = []
        if pick_date:
            clauses.append("pick_date = ?")
            params.append(pick_date)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if strategy:
            clauses.append("strategy = ?")
            params.append(strategy)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        query = f"""
            SELECT
                CAST(pick_date AS VARCHAR) AS pick_date,
                run_id,
                strategy,
                total,
                reviewed,
                recommended,
                unreviewed
            FROM strategy_run_metrics
            {where}
            ORDER BY pick_date DESC, run_id DESC, strategy ASC
            LIMIT ?
        """
        try:
            apply_migrations(self.path)
            with connect_duckdb(self.path, read_only=True) as connection:
                rows = connection.execute(query, params).fetchall()
        except Exception as exc:
            raise DuckDBAnalyticsReadError("failed to read DuckDB strategy summary") from exc

        return [
            StrategySummaryMetric(
                pick_date=str(row[0]),
                run_id=str(row[1]),
                strategy=str(row[2]),
                total=int(row[3] or 0),
                reviewed=int(row[4] or 0),
                recommended=int(row[5] or 0),
                unreviewed=int(row[6] or 0),
            )
            for row in rows
        ]


def _json_payload(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _archive_payload(row: ArchiveRow, snapshot: ArchiveSnapshot) -> dict[str, Any]:
    return {
        "archive_row_id": row.id,
        "snapshot_id": snapshot.id,
        "review_key": row.review_key,
        "close": row.close,
        "turnover_n": row.turnover_n,
        "brick_growth": row.brick_growth,
        "extra": row.extra_json or {},
        "review_payload": row.review_payload_json,
        "chart_path": row.chart_path,
        "candidate_id": row.candidate_id,
        "review_id": row.review_id,
        "recommendation_id": row.recommendation_id,
    }


def _strategy_metrics(rows: list[ArchiveRow]) -> dict[str, dict[str, int]]:
    metrics: dict[str, dict[str, int]] = {}
    for row in rows:
        strategy_metrics = metrics.setdefault(
            row.strategy,
            {"total": 0, "reviewed": 0, "recommended": 0, "unreviewed": 0},
        )
        strategy_metrics["total"] += 1
        if row.status in {"recommended", "reviewed", "unreviewed"}:
            strategy_metrics[row.status] += 1
    return metrics
