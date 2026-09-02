# ====================================================================================================
# ** 账户配置 **
# ====================================================================================================
account_config = {
    # 交易所API配置
    'apiKey': '',
    'secret': '',
    # ++++ 分钟偏移功能 ++++
    # 支持任意时间开始的小时级别K线
    "hour_offset": '55m',  # 分钟偏移设置，可以自由设置时间，配置必须是kline脚本中interval的倍数。默认：0m，表示不偏移。15m，表示每个小时偏移15m下单。
    # ++++ 企业微信机器人功能 ++++
    "wechat_webhook_url": 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=',
}

# ====================================================================================================
# ** 策略细节配置 **
# 案例策略，需要自己探索，不保证可用
# ====================================================================================================
strategy_name = 'BTC单币择时覆盖'  # 当前账户运行策略的名称。可以自己任意取
get_kline_num = 1500  # 获取多少根K线。这里跟策略日频和小时频影响。日线策略，代表多少根日线k。小时策略，代表多少根小时k
strategy_config = {
    'name': 'FixedRatioStrategy',  # *必填。使用什么策略，这里是固定比例融合
    'hold_period': '1H',  # *必填。聚合后策略持仓周期。目前回测支持日线级别、小时级别。例：1H，6H，3D，7D......
    'params': {
        'cap_ratios': [
            1 / 3, 1 / 3, 1 / 3,
        ]
    }
}
# 策略池
strategy_pool = [
    # 1.BTC择时覆盖
    dict(
        name='BTC择时34x24',
        strategy_list=[
            {
                "strategy": "Strategy_BTC",
                "offset_list": list(range(0, 1, 1)),
                "hold_period": "1H",
                "is_use_spot": True,
                # 资金权重。程序会自动根据这个权重计算你的策略占比，具体可以看1.8的直播讲解
                'cap_weight': 1,
                'long_cap_weight': 1,
                'short_cap_weight': 0,
                'long_select_coin_num': 1,
                'short_select_coin_num': 0,
                # 选币因子信息列表，用于`2_选币_单offset.py`，`3_计算多offset资金曲线.py`共用计算资金曲线
                "factor_list": [
                    ('OnlyBTC', False, 1, 1),  # 多头因子名（和factors文件中相同），排序方式，参数，权重。
                ],
                "filter_list": [],
            }
        ],
        # 配置再择时之后，可以使用 re_timing.py 进行再择时的资金曲线模拟
        re_timing={'name': 'MovingAverage', 'params': [34 * 24]}  # 可选，配置再择时策略
    ),
    dict(
        name='BTC择时55x24',
        strategy_list=[
            {
                "strategy": "Strategy_BTC",
                "offset_list": list(range(0, 1, 1)),
                "hold_period": "1H",
                "is_use_spot": True,
                # 资金权重。程序会自动根据这个权重计算你的策略占比，具体可以看1.8的直播讲解
                'cap_weight': 1,
                'long_cap_weight': 1,
                'short_cap_weight': 0,
                'long_select_coin_num': 1,
                'short_select_coin_num': 0,
                # 选币因子信息列表，用于`2_选币_单offset.py`，`3_计算多offset资金曲线.py`共用计算资金曲线
                "factor_list": [
                    ('OnlyBTC', False, 1, 1),  # 多头因子名（和factors文件中相同），排序方式，参数，权重。
                ],
                "filter_list": [],
            }
        ],
        # 配置再择时之后，可以使用 re_timing.py 进行再择时的资金曲线模拟
        re_timing={'name': 'MovingAverage', 'params': [55 * 24]}  # 可选，配置再择时策略
    ),
    dict(
        name='BTC择时377x24',
        strategy_list=[
            {
                "strategy": "Strategy_BTC",
                "offset_list": list(range(0, 1, 1)),
                "hold_period": "1H",
                "is_use_spot": True,
                # 资金权重。程序会自动根据这个权重计算你的策略占比，具体可以看1.8的直播讲解
                'cap_weight': 1,
                'long_cap_weight': 1,
                'short_cap_weight': 0,
                'long_select_coin_num': 1,
                'short_select_coin_num': 0,
                # 选币因子信息列表，用于`2_选币_单offset.py`，`3_计算多offset资金曲线.py`共用计算资金曲线
                "factor_list": [
                    ('OnlyBTC', False, 1, 1),  # 多头因子名（和factors文件中相同），排序方式，参数，权重。
                ],
                "filter_list": [],
            }
        ],
        # 配置再择时之后，可以使用 re_timing.py 进行再择时的资金曲线模拟
        re_timing={'name': 'MovingAverage', 'params': [377 * 24]}  # 可选，配置再择时策略
    ),
]

leverage = 1  # 杠杆数。我看哪个赌狗要把这里改成大于1的。高杠杆如梦幻泡影。不要想着一夜暴富，脚踏实地赚自己该赚的钱。
black_list = []  # 拉黑名单，永远不会交易。不喜欢的币、异常的币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
white_list = []  # 如果不为空，即只交易这些币，只在这些币当中进行选币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
# rebalance_mode =
is_pure_long = False  # 纯多设置(https://bbs.quantclass.cn/thread/36230)
