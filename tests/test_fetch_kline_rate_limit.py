from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

import pandas as pd

from pipeline import fetch_kline


class TushareRateLimitTests(unittest.TestCase):
    def test_limiter_spaces_calls_by_configured_rate(self) -> None:
        now = [0.0]
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        limiter = fetch_kline.TushareCallLimiter(
            requests_per_minute=120,
            cooldown_seconds=70,
            time_fn=lambda: now[0],
            sleep_fn=fake_sleep,
        )

        calls: list[float] = []
        limiter.run(lambda: calls.append(now[0]))
        limiter.run(lambda: calls.append(now[0]))

        self.assertEqual(calls, [0.0, 0.5])
        self.assertEqual(sleeps, [0.5])

    def test_limiter_applies_global_cooldown(self) -> None:
        now = [10.0]
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        limiter = fetch_kline.TushareCallLimiter(
            requests_per_minute=60,
            cooldown_seconds=70,
            time_fn=lambda: now[0],
            sleep_fn=fake_sleep,
        )

        limiter.run(lambda: None)
        limiter.cooldown(30)
        limiter.run(lambda: None)

        self.assertEqual(sleeps, [30.0])

    def test_rate_limit_pattern_matches_tushare_frequency_error(self) -> None:
        self.assertTrue(
            fetch_kline._looks_like_ip_ban_text(
                "抱歉，您访问接口(adj_factor)频率超限(200次/分钟)"
            )
        )

    def test_get_kline_treats_printed_tushare_error_as_rate_limit(self) -> None:
        old_pro_bar = fetch_kline.ts.pro_bar
        old_limiter = fetch_kline._tushare_call_limiter
        fetch_kline._tushare_call_limiter = fetch_kline.TushareCallLimiter(
            requests_per_minute=0,
            cooldown_seconds=1,
            sleep_fn=lambda seconds: None,
        )

        def fake_pro_bar(**kwargs):
            print("抱歉，您访问接口(adj_factor)频率超限(200次/分钟)")
            raise OSError("ERROR.")

        try:
            fetch_kline.ts.pro_bar = fake_pro_bar
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(fetch_kline.RateLimitError):
                    fetch_kline._get_kline_tushare("000001", "20260101", "20260102")
        finally:
            fetch_kline.ts.pro_bar = old_pro_bar
            fetch_kline._tushare_call_limiter = old_limiter

    def test_get_kline_passes_retry_count_one_to_tushare(self) -> None:
        old_pro_bar = fetch_kline.ts.pro_bar
        old_limiter = fetch_kline._tushare_call_limiter
        fetch_kline._tushare_call_limiter = fetch_kline.TushareCallLimiter(
            requests_per_minute=0,
            cooldown_seconds=1,
            sleep_fn=lambda seconds: None,
        )
        seen_kwargs: dict[str, object] = {}

        def fake_pro_bar(**kwargs):
            seen_kwargs.update(kwargs)
            return pd.DataFrame(
                [
                    {
                        "trade_date": "20260102",
                        "open": 1,
                        "close": 2,
                        "high": 3,
                        "low": 1,
                        "vol": 100,
                    }
                ]
            )

        try:
            fetch_kline.ts.pro_bar = fake_pro_bar
            df = fetch_kline._get_kline_tushare("000001", "20260101", "20260102")
        finally:
            fetch_kline.ts.pro_bar = old_pro_bar
            fetch_kline._tushare_call_limiter = old_limiter

        self.assertEqual(seen_kwargs["retry_count"], 1)
        self.assertEqual(seen_kwargs["adj"], "qfq")
        self.assertEqual(df["date"].dt.strftime("%Y%m%d").tolist(), ["20260102"])


if __name__ == "__main__":
    unittest.main()
