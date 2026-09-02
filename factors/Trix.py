"""
邢不行™️ 策略分享会
选币回测框架

版权所有 ©️ 邢不行
微信: xbx6660

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行

# ** 因子文件功能说明 **
1. 因子库中的每个 Python 文件需实现 `signal` 函数，用于计算因子值。
2. 除 `signal` 外，可根据需求添加辅助函数，不影响因子计算逻辑。

# ** signal 函数参数与返回值说明 **
1. `signal` 函数的第一个参数为 `candle_df`，用于接收单个币种的 K 线数据。
2. `signal` 函数的第二个参数用于因子计算的主要参数，具体用法见函数实现。
3. `signal` 函数可以接收其他可选参数，按实际因子计算逻辑使用。
4. `signal` 函数的返回值应为包含因子数据的 K 线数据。

# ** candle_df 示例 **
    candle_begin_time         symbol      open      high       low     close       volume  quote_volume  trade_num  taker_buy_base_asset_volume  taker_buy_quote_asset_volume  funding_fee   first_candle_time  是否交易
0          2023-11-22  1000BONK-USDT  0.004780  0.004825  0.004076  0.004531  12700997933  5.636783e+07     320715                   6184933232                  2.746734e+07     0.001012 2023-11-22 14:00:00         1
1          2023-11-23  1000BONK-USDT  0.004531  0.004858  0.003930  0.004267  18971334686  8.158966e+07     573386                   8898242083                  3.831782e+07     0.001634 2023-11-22 14:00:00         1
2          2023-11-24  1000BONK-USDT  0.004267  0.004335  0.003835  0.004140  17168511399  6.992947e+07     475254                   7940993618                  3.239266e+07     0.005917 2023-11-22 14:00:00         1
注意：若为小时级别策略，`candle_begin_time` 格式为 2023-11-22 14:00:00；若为日线，则为 2023-11-22。

# ** signal 参数示例 **
- 如果策略配置中 `factor_list` 包含 ('QuoteVolumeMean', True, 7, 1)，则 `param` 为 7，`args[0]` 为 'QuoteVolumeMean_7'。
- 如果策略配置中 `filter_list` 包含 ('QuoteVolumeMean', 7, 'pct:<0.8')，则 `param` 为 7，`args[0]` 为 'QuoteVolumeMean_7'。
"""


def signal(*args):
    df = args[0]
    n = args[1]
    factor_name = args[2]
    
    df['ema'] = df['close'].ewm(n, adjust=False).mean()  # EMA(CLOSE,N)
    df['ema_ema'] = df['ema'].ewm(n, adjust=False).mean()  # EMA(EMA(CLOSE,N),N)
    df['ema_ema_ema'] = df['ema_ema'].ewm(n, adjust=False).mean()  # EMA(EMA(EMA(CLOSE,N),N),N)
    # TRIX=(TRIPLE_EMA-REF(TRIPLE_EMA,1))/REF(TRIPLE_EMA,1)
    df[factor_name] = (df['ema_ema_ema'] - df['ema_ema_ema'].shift(1)) / (df['ema_ema_ema'].shift(1) + 1e-8)

    # 删除多余列
    del df['ema'], df['ema_ema'], df['ema_ema_ema']
    
    return df
