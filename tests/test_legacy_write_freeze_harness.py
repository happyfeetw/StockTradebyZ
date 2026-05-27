from __future__ import annotations

import contextlib
import io
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


LEGACY_GENERATED_PATTERNS = (
    "data/candidates",
    "data/review",
    "data/history",
    "data/kline",
    "data/runs",
    "candidates_latest.json",
    "suggestion.json",
)

PRODUCT_LEGACY_READ_ALLOWLIST = {
    Path("apps/api/stocktrade_api/services/legacy_import.py"),
    Path("apps/api/stocktrade_api/services/legacy_verify.py"),
}


def product_source_files() -> list[Path]:
    roots = [
        ROOT / "apps" / "api" / "stocktrade_api",
        ROOT / "apps" / "web" / "src",
        ROOT / "src" / "stocktrade",
    ]
    suffixes = {".py", ".ts", ".tsx"}
    files: list[Path] = []
    for root in roots:
        files.extend(path for path in sorted(root.rglob("*")) if path.suffix in suffixes)
    return files


class LegacyWriteFreezeHarnessTests(unittest.TestCase):
    def test_product_code_reads_legacy_generated_paths_only_through_import_boundary(self) -> None:
        violations: list[str] = []
        for path in product_source_files():
            relative = path.relative_to(ROOT)
            if relative in PRODUCT_LEGACY_READ_ALLOWLIST:
                continue
            text = path.read_text(encoding="utf-8")
            hits = [pattern for pattern in LEGACY_GENERATED_PATTERNS if pattern in text]
            if hits:
                violations.append(f"{relative.as_posix()}: {', '.join(hits)}")
        self.assertEqual(violations, [])

    def test_legacy_notice_can_be_emitted_and_suppressed(self) -> None:
        from legacy_compat import print_legacy_write_freeze_notice

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            print_legacy_write_freeze_notice(
                surface="test legacy surface",
                replacement="product API",
                writes="data/test",
            )
        self.assertIn("R7 legacy write freeze", stderr.getvalue())
        self.assertIn("compatibility-only", stderr.getvalue())

        previous = os.environ.get("STOCKTRADE_SUPPRESS_LEGACY_FREEZE_NOTICE")
        os.environ["STOCKTRADE_SUPPRESS_LEGACY_FREEZE_NOTICE"] = "1"
        try:
            suppressed = io.StringIO()
            with contextlib.redirect_stderr(suppressed):
                print_legacy_write_freeze_notice(
                    surface="test legacy surface",
                    replacement="product API",
                    writes="data/test",
                )
            self.assertEqual(suppressed.getvalue(), "")
        finally:
            if previous is None:
                os.environ.pop("STOCKTRADE_SUPPRESS_LEGACY_FREEZE_NOTICE", None)
            else:
                os.environ["STOCKTRADE_SUPPRESS_LEGACY_FREEZE_NOTICE"] = previous


if __name__ == "__main__":
    unittest.main()
