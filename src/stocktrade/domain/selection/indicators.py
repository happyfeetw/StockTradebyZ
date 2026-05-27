from __future__ import annotations

import numpy as np
import pandas as pd

try:
    from numba import njit as _njit
except ImportError:

    def _njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda func: func


@_njit(cache=True)
def _kdj_core(rsv: np.ndarray) -> tuple:
    n = len(rsv)
    k = np.empty(n, dtype=np.float64)
    d = np.empty(n, dtype=np.float64)
    k[0] = d[0] = 50.0
    for i in range(1, n):
        k[i] = 2.0 / 3.0 * k[i - 1] + 1.0 / 3.0 * rsv[i]
        d[i] = 2.0 / 3.0 * d[i - 1] + 1.0 / 3.0 * k[i]
    j = 3.0 * k - 2.0 * d
    return k, d, j


def compute_kdj(frame: pd.DataFrame, n: int = 9) -> pd.DataFrame:
    if frame.empty:
        return frame.assign(K=np.nan, D=np.nan, J=np.nan)

    low_n = frame["low"].rolling(window=n, min_periods=1).min()
    high_n = frame["high"].rolling(window=n, min_periods=1).max()
    rsv = ((frame["close"] - low_n) / (high_n - low_n + 1e-9) * 100).to_numpy(dtype=np.float64)

    k, d, j = _kdj_core(rsv)
    return frame.assign(K=k, D=d, J=j)
