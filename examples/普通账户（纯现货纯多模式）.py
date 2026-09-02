# ====================================================================================================
# ** 实盘账户配置 **
# ‼️‼️‼️账户配置，需要在accounts下的文件中做配置 ‼️‼️‼️
# 此处只是展示配置的结构，具体配置情参考 accounts 文件夹下的 _55mBTC样例.py
# 文件名就是账户名，比如 `15m大学生.py` 或者 `_55mBTC样例.py`
# ====================================================================================================
account_config = {
    # 交易所API配置
    'apiKey': '',
    'secret': '',
    # ++++ 分钟偏移功能 ++++
    # 支持任意时间开始的小时级别K线
    "hour_offset": '0m',  # 分钟偏移设置，可以自由设置时间，配置必须是kline脚本中interval的倍数。默认：0m，表示不偏移。15m，表示每个小时偏移15m下单。
    # ++++ BNB抵扣手续费功能 ++++
    "if_use_bnb_burn": True,  # 是否开启BNB燃烧，抵扣手续费
    "buy_bnb_value": 11,  # 买多少U的bnb来抵扣手续费。建议最低11U，现货最小下单量限制10U
    # ++++ 企业微信机器人功能 ++++
    # 创建企业微信机器人 参考帖子: https://bbs.quantclass.cn/thread/10975
    # 配置案例  https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxxxxxxxxxxxxx
    "wechat_webhook_url": 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=',
}

# ====================================================================================================
# ** 策略细节配置 **
# ‼️‼️‼️需要在accounts下的文件中做配置‼️‼️‼️
# 此处只是展示配置的结构，具体配置情参考 accounts 文件夹下的 _55mBTC样例.py
# ====================================================================================================
strategy_name = 'Strategy_Spot'  # 当前账户运行策略的名称。可以自己任意取
get_kline_num = 999  # 获取多少根K线。这里跟策略日频和小时频影响。日线策略，代表多少根日线k。小时策略，代表多少根小时k
strategy_config = {
    'name': 'FixedRatioStrategy',  # *必填。使用什么策略，这里是固定比例融合
    'hold_period': '1H',  # *必填。聚合后策略持仓周期。目前回测支持日线级别、小时级别。例：1H，6H，3D，7D......
    'params': {'cap_ratios': [1]}
}
strategy_pool = [
    dict(
        name='演示策略',
        # ++++ 策略配置 ++++
        # 多策略融合功能（大杂烩）：一个账户下可以同时运行多个选币策略。例如可以在一个账户下，使用一份资金，运行策略A（参数1）、策略A（参数2）、策略A（参数3）、策略B（参数1）、策略B（参数2）。以此类推。
        # 多策略资金配比功能：一个账户运行多个策略时，每个策略可以配置不同的资金比例。
        # 多空分离选币：多头和空头可以使用不一样的策略。
        # 多空分离过滤（前置）：多头和空头的前置过滤条件可以不同。
        # 数据整理支持自定义数据：支持在策略中加入量价数据之外的任意第三方数据
        strategy_list=[{
            "strategy": "Strategy_Spot_80",
            # ++++ 多offset功能 ++++
            # 可以使用1个或者多个offset。可以看 https://bbs.quantclass.cn/thread/36188 了解什么是offset？
            "hold_period": '24H',
            "offset_list": [0],
            # 资金权重。程序会自动根据这个权重计算你的策略占比，具体可以看1.8的直播讲解
            "cap_weight": 1,
            "is_use_spot": True,  # 是否使用现货，如果为False，则使用合约
            "long_cap_weight": 1,  # 多头资金占比
            "short_cap_weight": 0,  # 空头资金占比
            "long_select_coin_num": 0.1,  # 多头选币数量
            "short_select_coin_num": 0,  # 空头选币数量
            "factor_list": [
                ('PctChange', True, 80, 1)  # 因子名（和factors文件中相同），排序方式，参数，权重。
            ],
            "filter_list": [
                ('PctChange', 80)
            ],
            "long_filter_list_post": [
                ('PctChange', 80, 'pct:<0.8', True)
            ],
            "short_filter_list_post": [
                ('PctChange', 80, 'pct:<0.8', True)
            ],
        }]
    )
]  # 策略池
leverage = 1  # 杠杆数。我看哪个赌狗要把这里改成大于1的。高杠杆如梦幻泡影。不要想着一夜暴富，脚踏实地赚自己该赚的钱。
black_list = ['BTCUSDT', 'ETHUSDT']  # 黑名单。不参与策略的选币，如果持有黑名单币种，将会自动清仓
white_list = []  # 如果不为空，即只交易这些币，只在这些币当中进行选币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
is_pure_long = True  # 纯多设置(https://bbs.quantclass.cn/thread/36230)
