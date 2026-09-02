"""
DrawdownFromHigh 的镜像版本，给空头用：过去 n 根K线内的最低价 vs 当前收盘价的反弹幅度，
close / low.rolling(n).min() - 1，恒 >= 0。用于空头的后置过滤——如果一个币最近从阶段性低点
反弹太多（正在被挤仓），就不再让它继续被选中做空。
"""


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    rolling_low = df['low'].rolling(n, min_periods=1).min()
    df[factor_name] = df['close'] / rolling_low - 1

    return df
