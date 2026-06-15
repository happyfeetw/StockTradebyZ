from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "src"))

from stocktrade.domain.selection import PreselectParameters, PreselectResult, SelectionCandidate  # noqa: E402
from stocktrade_api.dependencies import get_preselect_service  # noqa: E402
from stocktrade_api.main import create_app  # noqa: E402
from stocktrade_api.storage.duckdb import apply_migrations, connect_duckdb  # noqa: E402

SQLITE_MIGRATIONS = ROOT / "apps" / "api" / "stocktrade_api" / "migrations" / "sqlite"


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(SQLITE_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def migrate_sqlite(db_path: Path) -> None:
    command.upgrade(alembic_config(db_path), "head")


class FixturePreselectService:
    def __init__(self) -> None:
        self.parameters: list[PreselectParameters] = []

    def run(self, parameters: PreselectParameters) -> PreselectResult:
        self.parameters.append(parameters)
        pick_date = parameters.pick_date or "2026-05-25"
        return PreselectResult(
            run_date="2026-05-27",
            pick_date=pick_date,
            candidates=[
                SelectionCandidate(
                    code="000001",
                    date=pick_date,
                    strategy="b2",
                    close=10.8,
                    turnover_n=2.3,
                    extra={"fixture_source": "product_workflow_proof"},
                ),
                SelectionCandidate(
                    code="000002",
                    date=pick_date,
                    strategy="brick",
                    close=12.6,
                    turnover_n=1.7,
                    brick_growth=0.18,
                    extra={"fixture_source": "product_workflow_proof"},
                ),
            ],
            meta={
                "mode": "fixture_preselect",
                "strategy_candidate_counts": {"b2": 1, "brick": 1},
                "data_dir": parameters.data_dir,
                "strategy_ids": list(parameters.strategy_ids) if parameters.strategy_ids is not None else None,
                "executed_strategies": list(parameters.strategy_ids) if parameters.strategy_ids is not None else ["b2", "brick"],
            },
        )


class ProductEvidenceReviewProviderExecutor:
    def __init__(self, artifact_root: Path) -> None:
        self.artifact_root = artifact_root
        self.requests: list[object] = []

    def run(self, request) -> list[dict]:
        self.requests.append(request)
        state_root = self.artifact_root / "review-provider" / request.candidate_batch_id / "gemini-cli"
        raw_dir = state_root / "runs" / "call-1"
        result_cache_dir = state_root / "results"
        raw_dir.mkdir(parents=True)
        result_cache_dir.mkdir(parents=True)
        evidence_files = [
            ("raw_prompt", raw_dir / "prompt.txt", "product workflow prompt"),
            ("raw_meta", raw_dir / "meta.json", '{"status":"finished"}'),
            ("raw_stdout", raw_dir / "stdout.jsonl", '{"role":"assistant"}\n'),
            ("raw_stderr", raw_dir / "stderr.log", ""),
            ("checkpoint", state_root / "gemini_cli_review_checkpoint.json", '{"status":"finished"}'),
            ("usage", state_root / ".gemini_cli_usage.json", '{"count":1}'),
        ]
        for _role, path, body in evidence_files:
            path.write_text(body, encoding="utf-8")

        results = []
        for item in request.items:
            cache_path = result_cache_dir / f"{item.review_key}.json"
            cache_path.write_text('{"cached":true}', encoding="utf-8")
            result = review_result(item.code, item.strategy)
            result["provider_evidence_files"] = [
                {"role": role, "path": str(path)}
                for role, path, _body in evidence_files
            ]
            result["provider_evidence_files"].append({"role": "result_cache", "path": str(cache_path)})
            results.append(result)
        return results


def review_result(code: str, strategy: str) -> dict:
    return {
        "code": code,
        "strategy": strategy,
        "signal_type": "product-proof",
        "comment": f"{code} {strategy} product workflow proof",
        "scores": {
            "trend_structure": 5,
            "price_position": 5,
            "volume_behavior": 5,
            "previous_abnormal_move": 5,
            "classic_pattern_match": 5,
        },
    }


def write_raw_csv(raw_dir: Path, code: str) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    lines = ["date,open,high,low,close,volume"]
    for day in range(1, 8):
        open_price = 10.0 + day
        close = open_price + 0.3
        lines.append(
            f"2026-05-{day:02d},{open_price:.2f},{open_price + 0.6:.2f},"
            f"{open_price - 0.4:.2f},{close:.2f},{1000 + day * 100}"
        )
    (raw_dir / f"{code}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


class ProductWorkflowStorageContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_product_api_chain_writes_sqlite_duckdb_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            sqlite_path = tmp / "app.sqlite"
            duckdb_path = tmp / "analytics.duckdb"
            artifact_root = tmp / "artifacts"
            raw_dir = tmp / "raw"
            for code in ("000001", "000002"):
                write_raw_csv(raw_dir, code)
            migrate_sqlite(sqlite_path)
            apply_migrations(duckdb_path)

            app = create_app(sqlite_path=sqlite_path, duckdb_path=duckdb_path, artifact_root=artifact_root)
            preselect_service = FixturePreselectService()
            app.dependency_overrides[get_preselect_service] = lambda: preselect_service
            app.state.review_provider_executor = ProductEvidenceReviewProviderExecutor(artifact_root)
            transport = httpx.ASGITransport(app=app)

            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                preselect_response = await client.post(
                    "/api/runs/preselect",
                    json={
                        "pick_date": "2026-05-25",
                        "data_dir": raw_dir.as_posix(),
                        "strategy_ids": ["b2", "brick"],
                    },
                )
                self.assertEqual(preselect_response.status_code, 200, preselect_response.text)
                preselect = preselect_response.json()
                batch_id = preselect["batch"]["id"]
                self.assertEqual(preselect["batch"]["total"], 2)
                self.assertEqual(preselect_service.parameters[0].data_dir, raw_dir.as_posix())
                self.assertEqual(preselect_service.parameters[0].strategy_ids, ("b2", "brick"))
                self.assertEqual(preselect["run"]["summary"]["strategy_ids"], ["b2", "brick"])

                chart_response = await client.post(
                    "/api/runs/chart-export",
                    json={"candidate_batch_id": batch_id, "raw_dir": raw_dir.as_posix(), "bars": 7},
                )
                self.assertEqual(chart_response.status_code, 200, chart_response.text)
                chart_payload = chart_response.json()
                self.assertEqual(len(chart_payload["artifacts"]), 4)
                chart_artifact = next(
                    artifact
                    for artifact in chart_payload["artifacts"]
                    if artifact["metadata"]["artifact_scope"] == "strategy"
                    and artifact["metadata"]["review_key"] == "000001_b2"
                )
                chart_file = await client.get(f"/api/artifacts/{chart_artifact['id']}")
                self.assertEqual(chart_file.status_code, 200)
                self.assertTrue(chart_file.headers["content-type"].startswith("image/jpeg"))

                review_response = await client.post(
                    "/api/runs/review/provider",
                    json={"candidate_batch_id": batch_id, "provider": "gemini-cli", "min_score": 4.0},
                )
                self.assertEqual(review_response.status_code, 200, review_response.text)
                review_payload = review_response.json()
                review_run_id = review_payload["review_run"]["id"]
                self.assertEqual(len(review_payload["reviews"]), 2)
                self.assertEqual(len(review_payload["recommendations"]), 2)
                first_review_payload = review_payload["reviews"][0]["payload"]
                self.assertIn("provider_evidence_artifact_ids", first_review_payload["provider_source"])
                self.assertNotIn("provider_evidence_files", first_review_payload)

                evidence_response = await client.get(f"/api/runs/{review_payload['run']['id']}/artifacts")
                self.assertEqual(evidence_response.status_code, 200)
                evidence_artifacts = evidence_response.json()["artifacts"]
                self.assertTrue(any(artifact["kind"] == "provider_evidence" for artifact in evidence_artifacts))

                archive_response = await client.post(
                    "/api/runs/archive",
                    json={"candidate_batch_id": batch_id, "review_run_id": review_run_id},
                )
                self.assertEqual(archive_response.status_code, 200, archive_response.text)
                archive_payload = archive_response.json()
                self.assertEqual(archive_payload["snapshot"]["candidate_count"], 2)
                self.assertEqual(archive_payload["snapshot"]["recommended_count"], 2)
                self.assertTrue(all(row["chart_artifact_id"] for row in archive_payload["rows"]))

            with connect_duckdb(duckdb_path, read_only=True) as connection:
                candidate_count = connection.execute(
                    "SELECT count(*) FROM candidate_facts WHERE batch_id = ?",
                    [batch_id],
                ).fetchone()[0]
                review_count = connection.execute(
                    "SELECT count(*) FROM review_facts WHERE review_run_id = ?",
                    [review_run_id],
                ).fetchone()[0]
                archive_count = connection.execute(
                    "SELECT count(*) FROM archive_facts WHERE run_id = ?",
                    [archive_payload["run"]["id"]],
                ).fetchone()[0]
            self.assertEqual(candidate_count, 2)
            self.assertEqual(review_count, 2)
            self.assertEqual(archive_count, 2)

            if app.state.sqlite_engine is not None:
                app.state.sqlite_engine.dispose()
