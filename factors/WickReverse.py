#!/usr/bin/python3
# -*- coding: utf-8 -*-

eps = 1e-8


def signal(*args):
    """
    影线反转因子（短周期）
    逻辑：下影线越长（探底后被拉回，抄底盘介入）→ 越看多；
          上影线越长（冲高后回落，多头力竭）→ 越看空。
    单根K线算出 (下影线占比 - 上影线占比)，再取 n 根滚动均值，捕捉近期
    "插针但未破位"这种典型短周期反转行为。
    对应笔记《因子胡说》反转类 + dingzhen研究中对 wick_pct 的关注。
    建议参数范围：n = 3~12（小时级，插针后的反转通常发生得很快）
    """
    df = args[0]
    n = args[1]
    factor_name = args[2]

    full_range = df['high'] - df['low'] + eps
    body_high = df[['open', 'close']].max(axis=1)
    body_low = df[['open', 'close']].min(axis=1)
    upper_wick = (df['high'] - body_high) / full_range
    lower_wick = (body_low - df['low']) / full_range
    wick_diff = lower_wick - upper_wick

    df[factor_name] = wick_diff.rolling(n, min_periods=1).mean()

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    full_range = df['high'] - df['low'] + eps
    body_high = df[['open', 'close']].max(axis=1)
    body_low = df[['open', 'close']].min(axis=1)
    upper_wick = (df['high'] - body_high) / full_range
    lower_wick = (body_low - df['low']) / full_range
    wick_diff = lower_wick - upper_wick
    for param in param_list:
        n = int(param)
        ret[str(param)] = wick_diff.rolling(n, min_periods=1).mean()
    return ret
