#!/usr/bin/python3
# -*- coding: utf-8 -*-

import pandas as pd
import numpy  as np
eps = 1e-8

def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]
    crash_window = 96  # 暴跌检测与最低点定位统一使用同一个窗口

    close = df['close'].to_numpy()
    p_change = df['close'].pct_change().fillna(0)

    # 过去 crash_window 根K线内最大跌幅发生在几根之前，及当时的收盘价
    bars_since_min = p_change.rolling(crash_window, min_periods=1).apply(
        lambda x: len(x) - 1 - np.argmin(x), raw=True
    ).astype(int).to_numpy()
    min_change = p_change.rolling(crash_window, min_periods=1).min().to_numpy()

    src_idx = np.clip(np.arange(len(df)) - bars_since_min, 0, None)
    adjusted_close_at_min = close[src_idx] * (1.005 ** bars_since_min) * 1.02

    is_crash = min_change < -0.1

    # 暴跌后已反弹较多，惩罚做空信号（避免在反转时做空）
    punish_reverse = np.where(is_crash & (close < adjusted_close_at_min), -1.4, 1)

    # 最狠暴跌当根（bars_since_min==0）即触发；紧接着的下一根先空仓观察，不参与本期选币
    is_trigger_bar = is_crash & (bars_since_min == 0)
    skip_bar = pd.Series(is_trigger_bar, index=df.index).shift(1, fill_value=False).to_numpy()

    mean = df['close'].rolling(n).mean()
    std = df['close'].rolling(n).std(ddof=0)
    upper = mean + 2 * std
    lower = mean - 2 * std

    deviate = np.select(
        [df['low'] > upper, df['high'] < lower],
        [(df['low'] - upper) / mean, (df['high'] - lower) / mean],
        default=0.0,
    )
    dev_sum = pd.Series(deviate, index=df.index).rolling(n, min_periods=1).sum()

    # 没有acc的时候，用mtm来比较
    mtm = (df['close'] / df['close'].shift(32) - 1) / 100000

    factor_values = np.where(dev_sum > 0, dev_sum / punish_reverse, dev_sum) - mtm
    df[factor_name] = np.where(skip_bar, np.nan, factor_values)

    return df