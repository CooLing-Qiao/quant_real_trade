"""
暴跌观察期内的反弹幅度（按暴跌事件锚定，非滚动窗口）：复刻 Acc_reverse_v3 的分层锚点逻辑定位
"这次暴跌的基准K线"，然后统计从该锚点K线到当前为止 low 的累计最小值，计算当前收盘价相对这个
最低点的反弹幅度 close / min_low_since_anchor - 1。

跟原版 BounceFromLow(n) 的区别：原版用 low.rolling(n).min() —— 滚动窗口的最低点会随窗口滑动
不断被刷新，遇到"阶梯式震荡上行"的逼空行情时，反弹比例会反复跌回阈值以下导致过滤中途失效
（TUT-USDT 2026-08-14~08-19 那次98小时逼空，BounceFromLow(40) 触发后又反复"失忆"放行，实测
151根K线里只有19根真正拦住）。本因子锚定的是暴跌事件本身的最低点，只要还在同一次暴跌观察期内
就不会被刷新。

锚点规则（与 Acc_reverse_v3 完全一致）：
- 默认锚点 = 过去 crash_window=96 根K线内跌幅最深的那一根
- 分层锚点 = 若该窗口内最大跌幅进一步突破 severe_thresh，改用"离当前最近的、单根跌幅本身也破
  severe_thresh 的那一根"（Acc空头 配的是 -0.35）

仅在 is_crash（过去96根内出现过单根跌幅<-10%）期间输出反弹幅度；不在暴跌观察期时输出 0，
配合 val:<阈值 的过滤条件，等价于"期外不设防"。
"""
import numpy as np
import pandas as pd

CRASH_WINDOW = 96  # 与 Acc_reverse/Acc_reverse_v3 保持一致


def signal(*args):
    df = args[0]
    param = args[1]
    factor_name = args[2]
    # param: (anchor_mode, severe_thresh)
    #   anchor_mode='v3' → 跟随 Acc_reverse_v3 的分层锚点（跌幅最深那根，破 severe_thresh 后改用最近一次破阈值的那根）
    #   anchor_mode='ep' → 锚定 is_crash 首次变True的那根，同一个暴跌观察期内基准永不重置
    # 实测（TUT-USDT 2026-08-14~08-19 逼空）：'v3' 锚点会被行情中的急跌回调重新锚定，基准被抬到高位、
    # 反弹幅度瞬间归零（08-17 17:00 从0.880跌到0.026）导致过滤失效；'ep' 锚点全程维持0.37~0.69。
    anchor_mode, severe_thresh = param

    close = df['close'].to_numpy(dtype=np.float64)
    low = df['low'].to_numpy(dtype=np.float64)
    idx = np.arange(len(df))
    p_change = df['close'].pct_change().fillna(0)
    p_change_arr = p_change.to_numpy()

    # 旧锚点：窗口内跌幅最深的那一根
    bars_since_min = p_change.rolling(CRASH_WINDOW, min_periods=1).apply(
        lambda x: len(x) - 1 - np.argmin(x), raw=True
    ).astype(int).to_numpy()
    min_change = p_change.rolling(CRASH_WINDOW, min_periods=1).min().to_numpy()

    # 新锚点：离当前最近的、单根跌幅本身也破 severe_thresh 的那一根
    severe = p_change_arr < severe_thresh
    last_severe_idx = np.maximum.accumulate(np.where(severe, idx, -1))
    bars_since_severe = idx - last_severe_idx

    is_crash = min_change < -0.1

    if anchor_mode == 'ep':
        # 锚定 is_crash 首次变True的那根：同一个暴跌观察期内用 low 的累计最小值，基准不会被重置
        is_crash_prev = np.empty(len(df), dtype=bool)
        is_crash_prev[0] = False
        is_crash_prev[1:] = is_crash[:-1]
        episode_id = np.cumsum(is_crash & ~is_crash_prev)
        min_low = pd.Series(low).groupby(episode_id).cummin().to_numpy()
    else:
        # 分层：min_change 破 severe_thresh 时用新锚点，否则用旧锚点
        use_severe_anchor = min_change < severe_thresh
        bars_since_ref = np.where(use_severe_anchor, bars_since_severe, bars_since_min)
        anchor_idx = np.clip(idx - bars_since_ref, 0, None)

        # 从锚点K线到当前为止 low 的最小值。锚点跳变时局部重算（跨度受 crash_window 限制）
        min_low = np.empty(len(df), dtype=np.float64)
        cur_anchor = -1
        cur_min = np.nan
        for i in range(len(df)):
            a = anchor_idx[i]
            if a != cur_anchor:
                cur_anchor = a
                seg = low[a:i + 1]
                seg = seg[~np.isnan(seg)]
                cur_min = seg.min() if len(seg) else np.nan
            elif not np.isnan(low[i]):
                cur_min = low[i] if np.isnan(cur_min) else min(cur_min, low[i])
            min_low[i] = cur_min

    with np.errstate(divide='ignore', invalid='ignore'):
        bounce = close / min_low - 1.0
    bounce = np.where(np.isfinite(bounce), bounce, 0.0)

    # 不在暴跌观察期时输出 0，配合 val:<阈值 等价于不设防
    df[factor_name] = np.where(is_crash, bounce, 0.0)

    return df
