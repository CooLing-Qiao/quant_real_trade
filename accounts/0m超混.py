import os

from dotenv import load_dotenv

from core.utils.path_kit import get_file_path

load_dotenv(get_file_path('.env'))

# ====================================================================================================
# ** 账户配置 **
# ====================================================================================================
account_config = {
    # 交易所API配置（真实值配置在 .env 中，见 .env.example）
    'apiKey': os.getenv('ACCOUNT_0M_CHAOHUN_API_KEY', ''),
    'secret': os.getenv('ACCOUNT_0M_CHAOHUN_API_SECRET', ''),
    # ++++ 分钟偏移功能 ++++
    # 支持任意时间开始的小时级别K线
    "hour_offset": '0m',  # 分钟偏移设置，可以自由设置时间，配置必须是kline脚本中interval的倍数。默认：0m，表示不偏移。0m，表示每小时整点下单。
    # ++++ 企业微信机器人功能 ++++
    "wechat_webhook_url": 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=80a8b84c-1051-4b19-a864-b57030eda378',
    # ++++ 下单量配置 ++++
    "max_one_order_amount": 300,  # 单次最大下单金额：300 USDT（超过此金额会自动拆单）
    "twap_interval": 1,            # 拆单间隔：3秒
    "order_swap_money_limit": 10,  # 合约最小下单金额：10 USDT
}

# ====================================================================================================
# ** 策略细节配置 **
# ====================================================================================================
strategy_name = '动量超混策略'  # 当前账户运行策略的名称。可以自己任意取
get_kline_num = 1800  # 获取多少根K线。这里跟策略日频和小时频影响。日线策略，代表多少根日线k。小时策略，代表多少根小时k
strategy_config = {
    'name': 'FixedRatioStrategy',  # *必填。使用什么策略，这里是固定比例融合
    'hold_period': '1H',  # *必填。聚合后策略持仓周期。目前回测支持日线级别、小时级别。例：1H，6H，3D，7D......
    'params': {
        'cap_ratios': [
            1,  # 只有1个策略池，分配100%资金
        ]
    }
}
# 策略池配置 - 所有策略合并在一个策略池中，框架会自动按照cap_weight归一化分配资金
strategy_pool = [
    dict(
        name='动量超混策略',
        strategy_list=[
    {
        "strategy": "Strategy_Acc多头_89",
        "offset_list": list(range(0, 1, 1)),
        "hold_period": "1H",
        "is_use_spot": False,
        'cap_weight': 0.204,
        'long_cap_weight': 1,
        'short_cap_weight': 0,
        'long_select_coin_num': 1,
        'short_select_coin_num': 0,
        "factor_list": [
            ('Acc_reverse', False, 89, 1),
        ],
        "long_filter_list": [],
        "long_filter_list_post": [
            ('DrawdownFromHigh', 18, 'val:>-0.30', False),
        ],
        "short_filter_list": [],
        "use_custom_func": False
    },
] + [
    {
        "strategy": "Strategy_Acc多头_550",
        "offset_list": list(range(0, 1, 1)),
        "hold_period": "1H",
        "is_use_spot": False,
        'cap_weight': 0.204,
        'long_cap_weight': 1,
        'short_cap_weight': 0,
        'long_select_coin_num': 1,
        'short_select_coin_num': 0,
        "factor_list": [
            ('Acc_reverse', False, 550, 1),
        ],
        "long_filter_list": [],
        "long_filter_list_post": [
            ('DrawdownFromHigh', 400, 'val:>-0.30', False),
        ],
        "short_filter_list": [],
        "use_custom_func": False
    },
] + [
    {
        "strategy": "Strategy_Acc空头",
        "offset_list": list(range(0, 1, 1)),
        "hold_period": "1H",
        "is_use_spot": False,
        'cap_weight': 0.32,
        'long_cap_weight': 0,
        'short_cap_weight': 1,
        'long_select_coin_num': 0,
        'short_select_coin_num': 1,
        "factor_list": [
            ('Acc_reverse_v3', False, (230, -0.35), 1),
        ],
        "long_filter_list": [],
        "short_filter_list": [],
        "short_filter_list_post": [
            ('BounceFromCrashLow', ('v3', -0.35), 'val:<0.55', False),
        ],
        "use_custom_func": False
    },
] + [
    {
        "strategy": "Strategy_Trix多头_55",
        "offset_list": list(range(0, 1, 1)),
        "hold_period": "1H",
        "is_use_spot": False,
        'cap_weight': 0.068,
        'long_cap_weight': 1,
        'short_cap_weight': 0,
        'long_select_coin_num': 1,
        'short_select_coin_num': 0,
        "factor_list": [
            ('Trix', False, 55, 1),
        ],
        "long_filter_list": [],
        "long_filter_list_post": [
            ('DrawdownFromHigh', 6, 'val:>-0.30', False),
            ('DrawdownFromHigh', 75, 'val:>-0.55', False),
        ],
        "short_filter_list": [],
        "use_custom_func": False
    },
] + [
    {
        "strategy": "Strategy_Trix多头_610",
        "offset_list": list(range(0, 1, 1)),
        "hold_period": "1H",
        "is_use_spot": False,
        'cap_weight': 0.068,
        'long_cap_weight': 1,
        'short_cap_weight': 0,
        'long_select_coin_num': 1,
        'short_select_coin_num': 0,
        "factor_list": [
            ('Trix', False, 610, 1),
        ],
        "long_filter_list": [],
        "long_filter_list_post": [
            ('DrawdownFromHigh', 67, 'val:>-0.35', False),
        ],
        "short_filter_list": [],
        "use_custom_func": False
    },
] + [
    {
        "strategy": "Strategy_Trix空头",
        "offset_list": list(range(0, 1, 1)),
        "hold_period": "1H",
        "is_use_spot": False,
        'cap_weight': 0.136,
        'long_cap_weight': 0,
        'short_cap_weight': 1,
        'long_select_coin_num': 0,
        'short_select_coin_num': 1,
        "factor_list": [
            ('Trix_v2', False, (145, -0.35), 1),
        ],
        "long_filter_list": [],
        "short_filter_list": [],
        "short_filter_list_post": [
            ('BounceFromLow', 22, 'val:<0.28', False),
            ('BounceFromLow', 400, 'val:<0.55', False),
        ],
        "use_custom_func": False
    },
]
    ),
]

leverage = 2  # 杠杆数。我看哪个赌狗要把这里改成大于1的。高杠杆如梦幻泡影。不要想着一夜暴富，脚踏实地赚自己该赚的钱。
black_list = ['BTC-USDT', 'ETH-USDT']  # 拉黑名单，永远不会交易。不喜欢的币、异常的币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
white_list = []  # 如果不为空，即只交易这些币，只在这些币当中进行选币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
# rebalance_mode =
is_pure_long = False  # 纯多设置(https://bbs.quantclass.cn/thread/36230)
