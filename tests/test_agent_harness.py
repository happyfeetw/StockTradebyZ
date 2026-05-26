from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgentHarnessTests(unittest.TestCase):
    def run_gate(self, gate: str) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/harness/check.py", gate],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_docs_gate(self) -> None:
        self.run_gate("docs")

    def test_contracts_gate(self) -> None:
        self.run_gate("contracts")

    def test_product_refactor_readiness_gate(self) -> None:
        self.run_gate("product-refactor-readiness")

    def test_refactor_readiness_alias_gate(self) -> None:
        self.run_gate("refactor-readiness")


if __name__ == "__main__":
    unittest.main()
