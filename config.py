"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import json
import os
import time
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path

from core.utils.path_kit import get_folder_path, get_file_path

# ====================================================================================================
# ** 数据配置 **
# ====================================================================================================
realtime_data_path = r'/home/ubuntu/datacenter/data/'  # 实盘数据路径
# 全局报错机器人通知
# - 创建企业微信机器人 参考帖子: https://bbs.quantclass.cn/thread/10975
# - 配置案例  https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxxxxxxxxxxxxx
error_webhook_url = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=80a8b84c-1051-4b19-a864-b57030eda378'
is_simulate = "none"  # debug, simulate, none。 debug：等同于 is_debug=True；simulate: 模拟盘；none：等同于 is_debug=False 实盘模式

# ====================================================================================================
# ** 实盘账户配置 **
# ‼️‼️‼️账户配置，需要在accounts下的文件中做配置 ‼️‼️‼️
# 此处只是展示配置的结构，具体配置情参考 accounts 文件夹下的 _55mBTC样例.py
# ====================================================================================================
account_config = {}  # 实盘账户配置，需要在accounts下的文件中做配置，此处只是结构上的展示

# ====================================================================================================
# ** 策略细节配置 **
# ‼️‼️‼️需要在accounts下的文件中做配置‼️‼️‼️
# 此处只是展示配置的结构，具体配置情参考 accounts 文件夹下的 _55mBTC样例.py
# ====================================================================================================
strategy_name = '动量超混策略'  # 当前账户运行策略的名称。可以自己任意取
min_kline_num = 300 # 最小 k 线数量限制
get_kline_num = 1800  # 获取多少根K线。这里跟策略日频和小时频影响。日线策略，代表多少根日线k。小时策略，代表多少根小时k
strategy_config = {}  # 策略配置
strategy_pool = []  # 策略池
factor_col_limit = 64  # 单次因子计算数量，放这里主要与网页版配合
leverage = 1  # 杠杆数。我看哪个赌狗要把这里改成大于1的。高杠杆如梦幻泡影。不要想着一夜暴富，脚踏实地赚自己该赚的钱。
black_list = ['BTC-USDT', 'ETH-USDT']  # 拉黑名单，永远不会交易。不喜欢的币、异常的币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
white_list = []  # 如果不为空，即只交易这些币，只在这些币当中进行选币。例：LUNA-USDT, 这里与实盘不太一样，需要有'-'
# rebalance_mode =  # 换仓模式控制
is_pure_long = False  # 纯多设置(https://bbs.quantclass.cn/thread/36230)

# ====================================================================================================
# ** 模拟器细节配置 **
# ====================================================================================================
simulator_config = dict(
    # 模拟下单回测设置
    initial_usdt=1_0000,  # 初始资金
    margin_rate=0.05,  # 维持保证金率，净值低于这个比例会爆仓
    swap_c_rate=5 / 10000,  # 合约手续费(包含滑点)
    spot_c_rate=1 / 1000,  # 现货手续费(包含滑点)
    swap_min_order_limit=5,  # 合约最小下单量。最小不能低于5
    spot_min_order_limit=10,  # 现货最小下单量。最小不能低于10
    avg_price_col='avg_price_1m',  # 用于模拟计算的平均价，预处理数据使用的是1m，'avg_price_1m'表示1分钟的均价, 'avg_price_5m'表示5分钟的均价。
    # 用于对齐 非24约数持仓周期(`strategy_config -> hold_period`)的资金曲线。
    # 以下配置不需要调整：1H,2H,3H,4H,6H,8H,12H
    # 其他持仓周期，可以根据回测与实盘的情况，调整一致
    unified_time='2017-01-01',
)

# ====================================================================================================
# ** 多账户功能加载区域 **
# 以下是自动化的代码，非请勿动哦~~~
# ====================================================================================================
trading_account: str = os.getenv("X3S_TRADING_ACCOUNT")
if trading_account:
    # 不一定要填写尾缀，提高兼容性
    if not trading_account.endswith('.py'):
        trading_account = f'{trading_account}.py'

    # 允许直接提供路径，不一定要放在accounts下的哦（除非你是高手，否则不要作死）
    account_config_path = Path(trading_account)
    if not account_config_path.exists():
        account_config_path = get_file_path('accounts', trading_account, as_path_type=True)

    allowed_attrs = (
        'account_config', 'strategy_name', 'strategy_config', 'strategy_pool', 'leverage', 'black_list', 'white_list',
        'rebalance_mode', 'is_pure_long', 'get_kline_num', 'min_kline_num')
    if account_config_path.is_file():
        spec = spec_from_file_location("account_config", account_config_path)
        account_module = module_from_spec(spec)
        spec.loader.exec_module(account_module)

        # 覆盖变量
        for attr_name in dir(account_module):
            if (not attr_name.startswith('_')) and (attr_name in allowed_attrs):
                # 避免带入一些意想不到的属性，我们做一些硬性限制，想玩的花，可以调整这段逻辑哦~~~
                globals()[attr_name] = getattr(account_module, attr_name)
    else:
        print(f"Warning: The specified account config file '{trading_account}' does not exist.")

    # 生成策略名称，避免同策略明覆盖重复的策略
    account_name = f'{account_config_path.stem}'  # 账户名等于文件名
    strategy_name = f'{account_config_path.stem}_{globals().get("strategy_name")}'
else:
    account_name = ''  # 防御性编程，避免提示器出问题


# 如果有 json 配置，则使用 json 配置覆盖掉 config.py 中的配置
json_file_config = get_file_path('config.json', as_path_type=True)
if json_file_config.exists():
    with open(json_file_config, 'r', encoding='utf-8') as f:
        config_data = json.load(f)

        # 目前只支持配置这些
        allowed_attrs = (
            'realtime_data_path', 'error_webhook_url', 'is_simulate', 'factor_col_limit'
        )

        for _attr in allowed_attrs:
            if _attr in config_data:
                globals()[_attr] = config_data[_attr]  # 自动转换为全局变量


# ====================================================================================================
# ** is_debug 与 is_simulate 兼容性处理 **
# 兼容旧版配置文件，支持 is_debug 和 is_simulate 的双向映射
# ====================================================================================================
if 'is_simulate' in globals():
    # 新版配置：存在 is_simulate，根据其值生成 is_debug
    match is_simulate:
        case 'debug':
            is_debug = True
        case 'simulate':
            is_debug = True
        case 'none':
            is_debug = False
        case _:
            is_debug = False
else:
    if 'is_debug' not in globals():
        is_debug = False
    # 旧版配置：不存在 is_simulate，根据 is_debug 生成 is_simulate
    if is_debug:
        is_simulate = "debug"
    else:
        is_simulate = "none"


# ====================================================================================================
# ** 回测全局设置 **
# ====================================================================================================
data_config = dict(
    realtime_data_path=realtime_data_path,  # 实盘数据路径
    min_kline_num=min_kline_num,  # 最少上市多久，不满该K线根数的币剔除，即剔除刚刚上市的新币。168：标识168个小时，即：7*24
    get_kline_num=get_kline_num,  # 获取多少根K线。这里跟策略日频和小时频影响。日线策略，代表多少根日线k。小时策略，代表多少根小时k
    reserved_cache=['select'],  # 用于缓存控制：['select']表示只缓存选币结果，不缓存其他数据，['all']表示缓存所有数据。
    # 目前支持选项：
    # - select: 选币结果pkl
    # - strategy: 大杂烩中策略选币pkl
    # - all: 无视上述配置细节，包含 `all` 就代表我全要
    # 缓存东西越多，硬盘消耗越大，对于参数比较多硬盘没那么大的童鞋，可以在这边设置
)
clean_start = True  # 启动是是否删除缓存。如果你不了解这个选项，保持为True，滥用缓存会有灾难性后果。

job_num = max(os.cpu_count() - 1, 1)  # 回测并行数量
# job_num = 2  # 回测并行数量

# ==== factor_col_limit 介绍 ====
# 这个写法，主要与网页版配合，如需修改，请调整上面的同名变量
factor_col_limit = factor_col_limit or 64  # 内存优化选项，一次性计算多少列因子。64是 16GB内存 电脑的典型值
# - 数字越大，计算速度越快，但同时内存占用也会增加。
# - 该数字是在 "因子数量 * 参数数量" 的基础上进行优化的。
#   - 例如，当你遍历 200 个因子，每个因子有 10 个参数，总共生成 2000 列因子。
#   - 如果 `factor_col_limit` 设置为 64，则计算会拆分为 ceil(2000 / 64) = 32 个批次，每次最多处理 64 列因子。
# - 对于16GB内存的电脑，在跑含现货的策略时，64是一个合适的设置。
# - 如果是在16GB内存下跑纯合约策略，则可以考虑将其提升到 128，毕竟数值越高计算速度越快。
# - 以上数据仅供参考，具体值会根据机器配置、策略复杂性、回测周期等有所不同。建议大家根据实际情况，逐步测试自己机器的性能极限，找到适合的最优值。

# 获取当前服务器时区，距离UTC 0点的偏差
utc_offset = int(time.localtime().tm_gmtoff / 60 / 60)  # 如果服务器在上海，那么utc_offset=8

# ====================================================================================================
# ** 交易所配置 **
# ====================================================================================================
# 如果使用代理 注意替换IP和Port
proxy = {}
# proxy = {'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}  # 如果你用clash的话
exchange_basic_config = {
    'timeout': 30000,
    'rateLimit': 30,
    'enableRateLimit': False,
    'options': {
        'adjustForTimeDifference': True,
        'recvWindow': 10000,
    },
    'proxies': proxy,
}

# 稳定币信息，不参与交易的币种
stable_symbol = ['BKRW', 'USDC', 'USDP', 'TUSD', 'BUSD', 'FDUSD', 'DAI', 'EUR', 'GBP', 'USBP', 'SUSD', 'PAXG', 'AEUR',
                 'EURI']

# ====================================================================================================
# ** 文件系统相关配置 **
# ⚠️⚠️⚠️ 以下的部分别动，除非你知道这些是做什么的 ⚠️⚠️⚠️
# - 获取一些全局路径
# - 自动创建缺失的文件夹们
# ====================================================================================================
# 运行时缓存数据存放位置
runtime_folder = get_folder_path('data', 'runtime', as_path_type=True)
# 预处理数据路径
bmac_data_path = Path(data_config['realtime_data_path']) / 'preprocess_1h_resample'
# 比重信息的路径
bmac_exginfo_path = Path(data_config['realtime_data_path']) / 'exginfo'
# 仓位管理回测结果
backtest_path = get_folder_path('data', '仓位管理回测结果', as_path_type=True)
# 子策略回测结果
backtest_iter_path = get_folder_path('data', '子策略回测结果', as_path_type=True)
# 增量计算用来存储快照的路径
snapshot_path = get_folder_path('data', 'snapshot', as_path_type=True)
# 配置实盘需要的额外数据
data_source_dict = {
    # 数据源的标签: ('加载数据的函数名', '数据存储的绝对路径')
    # 说明：数据源的标签,需要与因子文件中的 extra_data_dict 中的 key 保持一致，数据存储的路径需要表达清楚
    # 如果使用 BMAC，这里的路径就不需要配置
    "coin-cap": ('load_coin_cap', Path(data_config['realtime_data_path']) / 'coin_cap',),
    # 如果使用 BMAC，这里的路径就不需要配置
    "coin-btc": ('load_coin_btc', Path(data_config['realtime_data_path']) / 'binance_spot_1h_resample',),
}

debug_flag = '🟠ON' if is_debug else '🔘OFF'

if bmac_data_path.exists() is False or bmac_exginfo_path.exists() is False:
    print('⚠️ 请准确配置实盘数据中心的路径，并且启动数据中心，完成实盘数据初始化后，再运行本脚本')
    exit()
