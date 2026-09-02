#!/usr/bin/python3
# -*- coding: utf-8 -*-

import pandas as pd
import numpy  as np
eps = 1e-8

def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    # 暴跌惩罚
    df['p_change'] = df['close'].pct_change().fillna(0)
    df['min_change'] = df['p_change'].rolling(96, min_periods=1).min()

    # 反转时候，涨太多不做空
    min_change_idx = df['p_change'].rolling(96, min_periods=1).apply(lambda window: window.idxmin(), raw=False)
    df['close_at_min'] = min_change_idx.map(df['close'])
    grouped = df.groupby('close_at_min').cumcount()
    df['adjusted_close_at_min'] = df['close_at_min'] * (1.005 ** grouped) * 1.02

    # 反转惩罚
    df['punish_reverse'] = np.where((df['min_change'] < -0.11 ) & (df['close'] < df['adjusted_close_at_min']), -1.4,
                                    1)
    # df['punish_reverse_i'] = np.where((df['min_change'] < -0.11) & (df['close'] < df['adjusted_close_at_min']), -0.5, 1)

    # 暴涨惩罚
    df['m_change'] = df['p_change'].rolling(int(n), min_periods=1).max()
    df['punish'] = np.where(df['m_change'] < 0.08, 1, 1 + (12.5 * df['m_change'] - 1) ** 5)


    df[f'mean'] = df['close'].rolling(n).mean()
    df['std'] = df['close'].rolling(n).std(ddof=0)
    df['upper'] = df['mean'] + 2 * df['std']
    df['lower'] = df['mean'] - 2 * df['std']

    # 偏离惩罚
    df['height'] = 0
    df.loc[df['low'] > df['upper'], 'height'] = (df['close'] - df['upper']) / df['std']
    df['height_max'] = df['height'].rolling(n, min_periods=1).max()
    df['punish_1'] = np.where(df['height_max'] <= 5, 1, (df['height_max']/5)**2)


    # count计算
    df['count'] = 0
    df.loc[df['low'] > df['upper'], 'count'] = 1
    df.loc[df['high'] < df['lower'], 'count'] = -1
    df['deviate'] = np.where(df['count'] == 1, (df['low'] - df['upper']) / df['mean'] / df['punish'] / df['punish_1'],
                             np.where(df['count'] == -1, (df['high'] - df['lower']) / df['mean'], 0))

    df['sum'] =  (df['deviate'].rolling(n,min_periods=1).sum())

    # 没有acc的时候，用mtm来比较
    df['mtm'] = (df['close'] / df['close'].shift(32) - 1) / 100000

    df[factor_name] = np.where(df['sum'] > 0, df['sum'] / df['punish_reverse'], df['sum']) - df['mtm']

    drop_col = [
        'p_change', 'min_change', 'close_at_min', 'adjusted_close_at_min', 'punish_reverse', 
        'm_change', 'punish', 'mean',
        'std', 'upper', 'lower', 'height', 'height_max', 'punish_1', 'count',
        'deviate', 'sum', 'mtm'
    ]
    df.drop(columns=drop_col, inplace=True)


    return df