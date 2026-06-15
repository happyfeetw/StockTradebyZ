from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import fetch_kline  # noqa: E402


class FetchKlineCancellationTests(unittest.TestCase):
    def test_main_stops_submitting_codes_after_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            stocklist = tmp / "stocklist.csv"
            out_dir = tmp / "raw"
            log_path = tmp / "fetch.log"
            config_path = tmp / "fetch_kline.yaml"
            stocklist.write_text(
                "ts_code,symbol,name\n000001.SZ,000001,平安银行\n000002.SZ,000002,万科A\n",
                encoding="utf-8",
            )
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "start": "20260101",
                        "end": "20260102",
                        "stocklist": str(stocklist),
                        "exclude_boards": [],
                        "out": str(out_dir),
                        "workers": 1,
                    },
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            cancelled = False
            progress_events: list[dict[str, object]] = []

            def fake_get_kline(_code: str, _start: str, _end: str) -> pd.DataFrame:
                return pd.DataFrame(
                    [
                        {
                            "date": pd.Timestamp("2026-01-02"),
                            "open": 1.0,
                            "close": 2.0,
                            "high": 2.5,
                            "low": 0.9,
                            "volume": 100.0,
                        }
                    ]
                )

            def should_cancel() -> bool:
                return cancelled

            def progress_callback(payload: dict[str, object]) -> None:
                nonlocal cancelled
                progress_events.append(payload)
                if payload.get("current") == 1:
                    cancelled = True

            with mock.patch.dict(os.environ, {"TUSHARE_TOKEN": "fake-token"}, clear=False):
                with mock.patch.object(fetch_kline.ts, "pro_api", return_value=object()):
                    with mock.patch.object(fetch_kline, "_get_kline_tushare", side_effect=fake_get_kline):
                        with self.assertRaises(fetch_kline.FetchKlineCancelled):
                            fetch_kline.main(
                                config_path=config_path,
                                log_path=log_path,
                                should_cancel=should_cancel,
                                progress_callback=progress_callback,
                            )

            self.assertTrue((out_dir / "000001.csv").is_file())
            self.assertFalse((out_dir / "000002.csv").exists())
            self.assertTrue(any(event.get("current") == 1 for event in progress_events))


if __name__ == "__main__":
    unittest.main()
