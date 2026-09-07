#!/usr/bin/python3
# -*- coding: utf-8 -*-

import numpy as np

eps = 1e-8


def signal(*args):
    """
    量价共振动量因子（短周期）
    逻辑：单根K线的涨跌幅 * 当前量比（quote_volume / 近n根均量）。
    无量的暴涨暴跌大概率是插针或虚涨，会被量比打折；放量的上涨/下跌代表市场
    真实认可，会被放大。逐根打分后再取 n 根滚动均值，得到短周期的量价共振动量。
    对应笔记《动量因子思路》二、2 叠成交量 + 三、1 内叠。
    建议参数范围：n = 3~24（小时级短周期，捕捉快速的量价共振行情）
    """
    df = args[0]
    n = args[1]
    factor_name = args[2]

    mtm = df['close'].pct_change()
    vol_ratio = df['quote_volume'] / (df['quote_volume'].rolling(n, min_periods=1).mean() + eps)
    df[factor_name] = (mtm * vol_ratio).rolling(n, min_periods=1).mean()

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = dict()
    mtm = df['close'].pct_change()
    for param in param_list:
        n = int(param)
        vol_ratio = df['quote_volume'] / (df['quote_volume'].rolling(n, min_periods=1).mean() + eps)
        ret[str(param)] = (mtm * vol_ratio).rolling(n, min_periods=1).mean()
    return ret
