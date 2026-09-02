#!/usr/bin/python3
# -*- coding: utf-8 -*-

import pandas as pd
import numpy  as np
eps = 1e-8

def signal(*args):
    df = args[0]
    param = args[1]
    factor_name = args[2]
    crash_window = 96  # 暴跌检测与最低点定位统一使用同一个窗口
    # param 支持两种写法：单个 n（沿用默认阈值 -0.15），或 (n, severe_thresh) 元组（用于遍历阈值）
    if isinstance(param, (tuple, list)):
        n, severe_thresh = param
    else:
        n = param
        severe_thresh = -0.15  # 分层锚点的阈值：单根K线跌幅超过这个值，锚点改用"最近一次破阈值"的K线

    close = df['close'].to_numpy()
    idx = np.arange(len(df))
    p_change = df['close'].pct_change().fillna(0)
    p_change_arr = p_change.to_numpy()

    # 过去 crash_window 根K线内最大跌幅发生在几根之前，及当时的收盘价（旧锚点：最深的一根）
    bars_since_min = p_change.rolling(crash_window, min_periods=1).apply(
        lambda x: len(x) - 1 - np.argmin(x), raw=True
    ).astype(int).to_numpy()
    min_change = p_change.rolling(crash_window, min_periods=1).min().to_numpy()

    # 新锚点：离当前最近的、单根跌幅本身也超过 severe_thresh 的K线（不要求是窗口内最深的那根）
    severe = p_change_arr < severe_thresh
    last_severe_idx = np.maximum.accumulate(np.where(severe, idx, -1))
    bars_since_severe = idx - last_severe_idx

    # 分层锚点：min_change 破 severe_thresh 时用"最近一次破阈值"的K线；否则维持旧逻辑（最深的一根）
    use_severe_anchor = min_change < severe_thresh
    bars_since_ref = np.where(use_severe_anchor, bars_since_severe, bars_since_min)

    src_idx = np.clip(idx - bars_since_ref, 0, None)
    adjusted_close_at_ref = close[src_idx] * (1.005 ** bars_since_ref) * 1.02

    is_crash = min_change < -0.1

    # 暴跌后已反弹较多，惩罚做空信号（避免在反转时做空）
    punish_reverse = np.where(is_crash & (close < adjusted_close_at_ref), -1.4, 1)

    # 锚点K线（新锚点或旧锚点，取决于上面分层）当根触发；紧接着的下一根先空仓观察，不参与本期选币
    is_trigger_bar = is_crash & (bars_since_ref == 0)
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
