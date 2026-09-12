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
    # 单次最大下单金额（超过会自动拆单，实际值随机 ±20%）。2026-09-11 由 300 提到 1500：
    # 300 时一个 ~16k 的仓要拆 50 多单、下单 60~70 秒，推送图"执行成本"显示正常换仓每 100% 换手要付 0.3~0.5%，
    # 主要就是这 1 分多钟的延迟；提到 1500 后拆成十几单、十几秒下完。若观察到单笔滑点明显变大再往回调
    "max_one_order_amount": 1500,
    "twap_interval": 1,            # 拆单间隔：1秒
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
    # 第十八轮最终配置，与根目录 config.py 的 strategy_list 逐字一致（同步于 2026-09-12），
    # 依据见 research/optimization_round18/RESULTS.md；回测侧改动后必须把这一段重新同步过来。
    {
        # Acc 多头，短窗口 89。Acc_reverse 是布林带突破的滚动积分因子（动量，不是均值回归），
        # 参数 (n, 上轨倍数, 下轨倍数, taker_pow)：上轨守 Bollinger 标准的 2.0；下轨外推到 2.3
        # 减少向下突破对多头的拖累（第十四~十六轮）；taker_pow=2.5 让每根向上突破按
        # (2*主动买入成交额占比)^2.5 加权，买方主动推上去的突破计入更多（第十八轮，p∈[2,3] 是平台）。
        "strategy": "Strategy_Acc多头_89",
        "offset_list": list(range(0, 1, 1)),
        "hold_period": "1H",
        "is_use_spot": False,
        'cap_weight': 0.17051392405063293,
        'long_cap_weight': 1,
        'short_cap_weight': 0,
        'long_select_coin_num': 1,
        'short_select_coin_num': 0,
        "factor_list": [
            ('Acc_reverse', False, (89, 2.0, 2.3, 2.5), 1),
        ],
        "long_filter_list": [],
        # 不要给 Acc 多头加"收盘价须在均线上方"之类的方向过滤：dev_sum 是滚动求和，被选中的币
        # 常已回踩到均线下方，那正是它要抓的"突破后回踩"入场点，加了会把入场点全部过滤掉（详见 CLAUDE.md）。
        # DrawdownFromHigh(18) 是例外：拦的是买入瞬间正撞上单根拉高砸回的极端插针，不是回踩节奏。
        "long_filter_list_post": [
            ('DrawdownFromHigh', 18, 'val:>-0.25', False),
            ('VolumeMeanRatio', 48, 'val:>0.45', False),
            ('WickReverse', 24, 'val:<0.016958115', False),
            ('Cci', 24, 'val:>-36.829517', False),
            ('VolumeMeanRatio', 168, 'val:>1.0740995', False),
            ('Rsimean', 24, 'val:>0.38125909', False),
            ('PctChange', 168, 'val:>0.087897425', False),
            ('BounceFromLow', 22, 'val:>0.068314742', False),
            ('涨跌幅max', 24, 'val:>0.04240766', False),
        ],
        "short_filter_list": [],
        "use_custom_func": False
    },
] + [
    {
        # Acc 多头，长窗口 550。下轨外推到 3.0（邻域 2.8~3.2 是平台）；未加 taker 加权（实测为负）。
        "strategy": "Strategy_Acc多头_550",
        "offset_list": list(range(0, 1, 1)),
        "hold_period": "1H",
        "is_use_spot": False,
        'cap_weight': 0.25577088607594933,
        'long_cap_weight': 1,
        'short_cap_weight': 0,
        'long_select_coin_num': 1,
        'short_select_coin_num': 0,
        "factor_list": [
            ('Acc_reverse', False, (550, 2.0, 3.0), 1),
        ],
        "long_filter_list": [],
        # ('Acc_reverse', 89, ...) 是另一个因子实例（int 参数 → 上下轨都是 2.0），阈值针对该分布调出，保持 int 写法不动。
        "long_filter_list_post": [
            ('DrawdownFromHigh', 400, 'val:>-0.30', False),
            ('VolumeMeanRatio', 24, 'val:>0.45', False),
            ('DrawdownFromHigh', 168, 'val:>-0.26511014', False),
            ('Dbcd', 17, 'val:<3.9267862', False),
            ('Acc_reverse', 89, 'val:>-0.00088426011', False),
            ('ZfStd', 48, 'val:>0.017593278', False),
            ('WickReverse', 24, 'val:<0.087966821', False),   # 第十八轮，q0.9
        ],
        "short_filter_list": [],
        "use_custom_func": False
    },
] + [
    {
        # Acc 空头，窗口 230。Acc_reverse_v3 在 Acc_reverse 基础上给"暴跌后反弹"的惩罚加了分层锚点：
        # 过去 96 根内单根跌幅破 severe_thresh(-35%) 时，参考价改用离当前最近的那根深跌 K 线，
        # 避免拿很久以前的深跌当基准；布林倍数内部硬编码 2.05（第十一轮）。
        "strategy": "Strategy_Acc空头",
        "offset_list": list(range(0, 1, 1)),
        "hold_period": "1H",
        "is_use_spot": False,
        'cap_weight': 0.1365,
        'long_cap_weight': 0,
        'short_cap_weight': 1,
        'long_select_coin_num': 0,
        'short_select_coin_num': 1,
        "factor_list": [
            ('Acc_reverse_v3', False, (230, -0.35), 1),
        ],
        "long_filter_list": [],
        "short_filter_list": [],
        # BounceFromCrashLow：只在暴跌事件期间生效、按暴跌锚点算反弹幅度，反弹超过 55% 不做空
        # （拦截率仅 0.45%，价值集中在极少数极端挤仓事件）。挡得更死的 'ep' 锚点实测惨败——
        # Acc 空头的 edge 就来自"暴跌币反弹后继续跌"，挡太死会把 edge 一起挡掉。
        "short_filter_list_post": [
            ('BounceFromCrashLow', ('v3', -0.35), 'val:<0.55', False),
            ('VolumeMeanRatio', 48, 'val:>0.45', False),
        ],
        "use_custom_func": False
    },
] + [
    {
        # Trix 多头，短窗口 55。Trix = 三重 EMA 的一阶相对变化（注意 ewm 的参数是 com，等价 span=111）；
        # 内部参数第十二、十七轮扫过，现值全部最优，不要再动。
        "strategy": "Strategy_Trix多头_55",
        "offset_list": list(range(0, 1, 1)),
        "hold_period": "1H",
        "is_use_spot": False,
        'cap_weight': 0.14209493670886075,
        'long_cap_weight': 1,
        'short_cap_weight': 0,
        'long_select_coin_num': 1,
        'short_select_coin_num': 0,
        "factor_list": [
            ('Trix', False, 55, 1),
        ],
        "long_filter_list": [],
        "long_filter_list_post": [
            ('DrawdownFromHigh', 6, 'val:>-0.12460311', False),
            ('DrawdownFromHigh', 75, 'val:>-0.55', False),
            ('RealizedVol', 48, 'val:<0.025', False),
            ('VolumeMeanRatio', 48, 'val:>0.49776389', False),
            ('涨跌幅max', 6, 'val:>0.020889022', False),
            ('RealizedVol', 168, 'val:>0.017184945', False),
            ('MtmVolume', 6, 'val:<0.027182522', False),
            ('RealizedVol', 60, 'val:<0.044555571', False),
            ('VolumeMeanRatio', 12, 'val:>0.63202026', False),
            ('跌幅max', 48, 'val:>0.028835826', False),
        ],
        "short_filter_list": [],
        "use_custom_func": False
    },
] + [
    {
        # Trix 多头，长窗口 610。
        "strategy": "Strategy_Trix多头_610",
        "offset_list": list(range(0, 1, 1)),
        "hold_period": "1H",
        "is_use_spot": False,
        'cap_weight': 0.14209493670886075,
        'long_cap_weight': 1,
        'short_cap_weight': 0,
        'long_select_coin_num': 1,
        'short_select_coin_num': 0,
        "factor_list": [
            ('Trix', False, 610, 1),
        ],
        "long_filter_list": [],
        "long_filter_list_post": [
            ('VolumeMeanRatio', 24, 'val:>0.63555158', False),
            ('BounceFromLow', 96, 'val:>0.061111134', False),
            ('ZfStd', 48, 'val:>0.020861992', False),
            ('跌幅max', 48, 'val:>0.02576173', False),
            ('RealizedVol', 24, 'val:>0.007657194', False),
            ('Rsimean', 12, 'val:>0.38715301', False),   # 第十八轮，q0.25；砍掉本子策略 19.7% 的持仓小时，消融贡献最大
        ],
        "short_filter_list": [],
        "use_custom_func": False
    },
] + [
    {
        # Trix 空头，窗口 145。Trix_v2 = Trix + 与 Acc_reverse_v3 同结构的暴跌护栏：暴跌尚未反弹回参考价时，
        # 把负的 trix 翻正、退出空头候选（不追空死猫跳）；只作用于 trix<0 的分支，多头候选不受影响。
        "strategy": "Strategy_Trix空头",
        "offset_list": list(range(0, 1, 1)),
        "hold_period": "1H",
        "is_use_spot": False,
        'cap_weight': 0.1530253164556962,
        'long_cap_weight': 0,
        'short_cap_weight': 1,
        'long_select_coin_num': 0,
        'short_select_coin_num': 1,
        "factor_list": [
            ('Trix_v2', False, (145, -0.35), 1),
        ],
        "long_filter_list": [],
        "short_filter_list": [],
        # 两个 BounceFromLow：短窗口抓单小时暴力插针，长窗口抓多天慢速逼空，单一窗口只能堵一类。
        "short_filter_list_post": [
            ('BounceFromLow', 22, 'val:<0.28', False),
            ('BounceFromLow', 400, 'val:<0.55', False),
            ('Trix', 610, 'val:>-0.00033041095', False),   # 第十八轮，q0.1：长周期 Trix 跌得最凶的 10% 不做空
        ],
        "use_custom_func": False
    },
]
    ),
]

leverage = 4.2  # 杠杆数。我看哪个赌狗要把这里改成大于1的。高杠杆如梦幻泡影。不要想着一夜暴富，脚踏实地赚自己该赚的钱。
black_list = ['BTC-USDT', 'ETH-USDT']  # 拉黑名单，永远不会交易。不喜欢的币、异常的币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
white_list = []  # 如果不为空，即只交易这些币，只在这些币当中进行选币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
rebalance_mode = {'mode': 'RebByEquityRatio',
                  'params': {'min_order_usdt_ratio': 0.0138}}
is_pure_long = False  # 纯多设置(https://bbs.quantclass.cn/thread/36230)
