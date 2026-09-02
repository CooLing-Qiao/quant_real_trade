#!/usr/bin/python3
# -*- coding: utf-8 -*-

import pandas as pd
import numpy  as np





def signal(*args):
    # Boll_Count
    df = args[0]
    n = args[1]
    factor_name = args[2]

    #
    df[f'mean'] = df['close'].rolling(n).mean()
    df['std'] = df['close'].rolling(n).std(ddof=0)
    df['upper'] = df['mean'] + 2 * df['std']
    df['lower'] = df['mean'] - 2 * df['std']
    df['count'] = 0
    df.loc[df['low'] > df['upper'], 'count'] = 1
    df.loc[df['high'] < df['lower'], 'count'] = -1
    df['deviate'] = np.where(df['count'] == 1, (df['low'] - df['upper']) / df['mean'],
                             np.where(df['count'] == -1, (df['high'] - df['lower']) / df['mean'], 0))

    df['count_extra'] = 0
    df.loc[df['low'] > df['mean'], 'count_extra'] = 1
    df.loc[df['high'] < df['mean'], 'count_extra'] = -1
    df['deviate_extra'] = (np.where(df['count_extra'] == 1, (df['low'] - df['mean']) / df['mean'],
                             np.where(df['count_extra'] == -1, (df['high'] - df['mean']) / df['mean'], 0))) / 10000

    df['deviate_sum'] = df['deviate'] + df['deviate_extra']
    df[factor_name] = df['deviate_sum'].rolling(n).sum()

    del df['mean']
    del df['std']
    del df['upper']
    del df['lower']
    del df['count']
    del df['deviate']
    del df['count_extra']
    del df['deviate_extra']
    del df['deviate_sum']

    return df