from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ResourceEnvelopeHarnessTests(unittest.TestCase):
    def test_resource_envelope_script_covers_product_workflow_and_growth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            work_root = Path(tmpdir) / "resource-envelope"
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/harness/resource_envelope.py",
                    "--work-root",
                    str(work_root),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)

            self.assertEqual(report["thresholds"]["status"], "passed")
            self.assertEqual(report["workflow"]["candidate_count"], 2)
            self.assertEqual(report["workflow"]["chart_artifact_count"], 4)
            self.assertEqual(report["workflow"]["review_count"], 2)
            self.assertEqual(report["workflow"]["recommendation_count"], 2)
            self.assertEqual(report["workflow"]["archive_row_count"], 2)

            total_delta = report["storage"]["total_delta"]
            self.assertGreater(total_delta["sqlite_bytes"], 0)
            self.assertGreater(total_delta["duckdb_bytes"], 0)
            self.assertGreater(total_delta["artifact_bytes"], 0)
            self.assertGreater(total_delta["artifact_files"], 0)
            self.assertGreater(report["memory"]["python_peak_mb"], 0)

            self.assertEqual(report["sqlite_counts"]["runs"], 4)
            self.assertEqual(report["duckdb_counts"]["candidate_facts"], 2)
            self.assertFalse((work_root / "data" / "trading").exists())


if __name__ == "__main__":
    unittest.main()
