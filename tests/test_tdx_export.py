from __future__ import annotations

import base64
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import tdx_export  # noqa: E402


class TdxExportTests(unittest.TestCase):
    def test_tdx_code_market_prefixes(self) -> None:
        self.assertEqual(tdx_export.to_tdx_code("600000"), "1600000")
        self.assertEqual(tdx_export.to_tdx_code("300001"), "0300001")
        self.assertEqual(tdx_export.to_tdx_code("830001"), "2830001")
        self.assertIsNone(tdx_export.to_tdx_code("ABC"))

    def test_import_download_filenames_use_pick_date(self) -> None:
        self.assertEqual(tdx_export.date_suffix("2026-06-03"), "20260603")
        self.assertEqual(tdx_export.import_bat_filename("2026-06-03"), "import_to_tdx_20260603.bat")
        self.assertEqual(
            tdx_export.import_html_filename("2026-06-03", "仅推荐"),
            "tdx_import_20260603_recommended.html",
        )

    def test_cfg_record_merge_is_fixed_width_and_deduped(self) -> None:
        record = tdx_export.cfg_record_bytes("0602QB1")

        self.assertEqual(len(record), 120)
        self.assertEqual(record[:7], b"0602QB1")
        self.assertEqual(record[50:57], b"0602QB1")

        blocks = [{"name": "0602QB1"}, {"name": "0602QB2"}]
        merged, modified, added = tdx_export.merge_cfg_records(record, blocks)

        self.assertTrue(modified)
        self.assertEqual(added, 1)
        self.assertEqual(len(merged), 240)

        merged_again, modified_again, added_again = tdx_export.merge_cfg_records(merged, blocks)
        self.assertFalse(modified_again)
        self.assertEqual(added_again, 0)
        self.assertEqual(merged_again, merged)

        with self.assertRaises(ValueError):
            tdx_export.merge_cfg_records(b"not-a-valid-cfg", blocks)

    def test_build_blocks_reads_history_for_selected_date(self) -> None:
        old_root = tdx_export._PROJECT_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            history_dir = project / "data" / "history" / "2026-06-02"
            history_dir.mkdir(parents=True)
            (history_dir / "all.json").write_text(
                json.dumps(
                    {
                        "date": "2026-06-02",
                        "results": [
                            {
                                "code": "600000",
                                "strategy": "b1",
                                "status": "recommended",
                                "rank": 1,
                                "review": {"total_score": 4.1},
                            },
                            {
                                "code": "000001",
                                "strategy": "b1",
                                "status": "reviewed",
                                "review": {"total_score": 4.8},
                            },
                            {
                                "code": "300001",
                                "strategy": "brick",
                                "status": "recommended",
                                "rank": 2,
                                "review": {"total_score": 5.0},
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            review_dir = project / "data" / "review" / "2026-06-02"
            review_dir.mkdir(parents=True)
            (review_dir / "suggestion.json").write_text(
                json.dumps({"min_score_threshold": 4.0}, ensure_ascii=False),
                encoding="utf-8",
            )

            try:
                tdx_export._PROJECT_ROOT = project
                recommended = tdx_export.build_blocks("2026-06-02", mode="recommended")
                all_blocks = tdx_export.build_blocks("2026-06-02", mode="all")
            finally:
                tdx_export._PROJECT_ROOT = old_root

        by_name = {block["name"]: block for block in recommended}
        self.assertEqual(sorted(by_name), ["0602QB1", "0602QBrick"])
        self.assertEqual(base64.b64decode(by_name["0602QB1"]["content_b64"]), b"1600000\r\n")
        self.assertEqual(len(base64.b64decode(by_name["0602QB1"]["cfg_record_b64"])), 120)

        all_by_name = {block["name"]: block for block in all_blocks}
        self.assertEqual(base64.b64decode(all_by_name["0602QB1"]["content_b64"]), b"1600000\r\n0000001\r\n")

    def test_export_to_tdx_writes_blk_and_cfg(self) -> None:
        blocks = [
            {
                "name": "0602QB1",
                "content_b64": base64.b64encode(b"1600000\r\n").decode("ascii"),
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            result = tdx_export.export_to_tdx(target, blocks)

            self.assertEqual(result["succeeded"], 1)
            self.assertTrue(result["cfg_ok"])
            self.assertEqual((target / "0602QB1.blk").read_bytes(), b"1600000\r\n")
            cfg = (target / "blocknew.cfg").read_bytes()
            self.assertEqual(len(cfg), 120)
            self.assertEqual(cfg[:7], b"0602QB1")
            self.assertEqual(cfg[50:57], b"0602QB1")

    def test_generated_bat_keeps_window_open(self) -> None:
        bat = tdx_export.generate_import_bat([
            {
                "name": "0602QB1",
                "content_b64": base64.b64encode(b"1600000\r\n").decode("ascii"),
            }
        ])

        bat.encode("ascii")
        self.assertIn("powershell.exe -NoProfile -ExecutionPolicy Bypass", bat)
        self.assertIn('-File "%TMPPS1%"', bat)
        self.assertNotIn("Invoke-Expression", bat)
        self.assertIn('set "AGENTTRADER_BAT_DIR=%~dp0"', bat)
        self.assertIn("pause >nul", bat)

        chunks = re.findall(r'(?:>|>>) "%TMPPS%" echo ([A-Za-z0-9+/=]+)', bat)
        ps_script = base64.b64decode("".join(chunks)).decode("utf-16-le")
        self.assertIn("Verifying written files", ps_script)
        self.assertIn("blocknew.cfg does not contain block", ps_script)


if __name__ == "__main__":
    unittest.main()
