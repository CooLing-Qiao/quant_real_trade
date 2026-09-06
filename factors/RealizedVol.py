"""
已实现波动率：过去 n 根K线小时收益率的标准差。用于识别"近期持续高波动/反复乱晃"的币——
跟 涨跌幅max/跌幅max（看单根K线的极值，会误伤 Acc_reverse 最想要的单次突破信号）不是同一类统计量，
这个看的是离散度（一段时间内波动的整体幅度），对"持续高波动但没有哪一根特别极端"的阶梯式阴跌/阳涨
更敏感。

诊断依据：对全部历史持仓episode（同一策略同一币连续持有>=24h的片段，n=1555）按建仓前48~96根K线
波动率分5档，波动率最高20%那组，持仓期间浮亏超30%的比例是波动率最低20%那组的3倍(7.7% vs 2.6%)，
相关系数-0.205，单调、非噪音（2026-09-01验证）。
"""


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]

    df[factor_name] = df['close'].pct_change().rolling(n, min_periods=n // 2).std()

    return df


def signal_multi_params(df, param_list) -> dict:
    ret = df['close'].pct_change()
    result = dict()
    for n in param_list:
        n = int(n)
        result[str(n)] = ret.rolling(n, min_periods=n // 2).std()
    return result
