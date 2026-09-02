"""
过去 n 根K线内的最高价 vs 当前收盘价的回撤幅度：close / high.rolling(n).max() - 1，
恒 <= 0。用于后置/前置过滤，堵住"趋势因子太慢、币已经从高点跌了一大截还继续持有"的情况——
跟 跌幅max/涨跌幅max 只看单根K线极值不同，这个看的是从阶段性高点到现在的累计回撤。
"""


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    rolling_high = df['high'].rolling(n, min_periods=1).max()
    df[factor_name] = df['close'] / rolling_high - 1

    return df
