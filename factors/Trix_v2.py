"""
邢不行™️ 策略分享会
选币回测框架

版权所有 ©️ 邢不行
微信: xbx6660

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行

在原版 Trix 基础上加了跟 Acc_reverse_v3 同结构的"暴跌惩罚"机制：
过去96根K线内出现过单根跌幅破 severe_thresh（分层锚点：优先取"最近一次破阈值"的K线，
而不是窗口内最深的那根）、且价格还没涨回参考价时，如果当前 Trix 是负的（会被 Trix空头 选中做空），
就把它除以 -1.4 翻成正的，退出空头候选——避免在暴跌尚未走完反弹的阶段追空。
Trix 为正（多头候选）或不在暴跌未恢复状态时，完全不受影响，跟原版 Trix 一致。
"""

import numpy as np


def signal(*args):
    df = args[0]
    param = args[1]
    factor_name = args[2]
    crash_window = 96

    if isinstance(param, (tuple, list)):
        n, severe_thresh = param
    else:
        n = param
        severe_thresh = -0.35

    close = df['close'].to_numpy()
    idx = np.arange(len(df))
    p_change = df['close'].pct_change().fillna(0)
    p_change_arr = p_change.to_numpy()

    # 旧锚点：过去 crash_window 根K线内最深的一根
    bars_since_min = p_change.rolling(crash_window, min_periods=1).apply(
        lambda x: len(x) - 1 - np.argmin(x), raw=True
    ).astype(int).to_numpy()
    min_change = p_change.rolling(crash_window, min_periods=1).min().to_numpy()

    # 新锚点：离当前最近的、单根跌幅本身也破 severe_thresh 的K线
    severe = p_change_arr < severe_thresh
    last_severe_idx = np.maximum.accumulate(np.where(severe, idx, -1))
    bars_since_severe = idx - last_severe_idx

    use_severe_anchor = min_change < severe_thresh
    bars_since_ref = np.where(use_severe_anchor, bars_since_severe, bars_since_min)

    src_idx = np.clip(idx - bars_since_ref, 0, None)
    adjusted_close_at_ref = close[src_idx] * (1.005 ** bars_since_ref) * 1.02

    is_crash = min_change < -0.1
    punish_active = is_crash & (close < adjusted_close_at_ref)

    ema = df['close'].ewm(n, adjust=False).mean()
    ema_ema = ema.ewm(n, adjust=False).mean()
    ema_ema_ema = ema_ema.ewm(n, adjust=False).mean()
    trix = ((ema_ema_ema - ema_ema_ema.shift(1)) / (ema_ema_ema.shift(1) + 1e-8)).to_numpy()

    punish_reverse = np.where(punish_active & (trix < 0), -1.4, 1)
    factor_values = np.where(trix < 0, trix / punish_reverse, trix)

    df[factor_name] = factor_values

    return df
