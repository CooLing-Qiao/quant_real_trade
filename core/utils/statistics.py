"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import os
import sys
import time
import traceback
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import ccxt
import matplotlib.lines as mtlines
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd
from tqdm import tqdm

_ = os.path.abspath(os.path.dirname(__file__))  # 返回当前文件路径
_ = os.path.abspath(os.path.join(_, '..'))  # 返回根目录文件夹
sys.path.append(_)  # _ 表示上级绝对目录，系统中添加上级目录，可以解决导入不存的问题
sys.path.append('..')  # '..' 表示上级相对目录，系统中添加上级目录，可以解决导入不存的问题
sys.path.append(os.path.dirname(os.path.abspath(__file__)))  # 当前目录
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 上级目录
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # 根目录
from core.account_manager import init_system, load_multi_accounts
from core.binance.base_client import BinanceClient
from core.model.account_config import AccountConfig
from core.model.backtest_config import MultiEquityBacktestConfig
from core.utils.path_kit import get_file_path, get_folder_path
from core.utils.functions import del_hist_files, refresh_diff_time, ignore_error
from core.utils.statis_func import calc_swap_position, calc_spot_position, save_send_pos_info, \
    calculate_and_send_pnl_report, send_img_for_dataframe
from core.utils.dingding import send_wechat_work_msg, send_wechat_work_img
from config import error_webhook_url, exchange_basic_config, utc_offset, strategy_pool

warnings.filterwarnings('ignore')
pd.set_option('display.max_rows', 1000)
pd.set_option('expand_frame_repr', False)  # 当列太多时不换行
pd.set_option('display.unicode.ambiguous_as_wide', True)  # 设置命令行输出时的列对齐功能
pd.set_option('display.unicode.east_asian_width', True)

"""
dataframe_image.export参数简要说明

table_conversion默认是chrome，需要安装chrome，安装麻烦，速度又慢，偶尔卡死进程

table_conversion='matplotlib'，速度快，中文需要修改源码等处理，用英文吧

如果df超过100行会报错，设置 max_rows = -1

如果df超过30列会报错，设置 max_cols = -1

存在计算机生成图片崩溃的可能，主要df别太大
"""
# =画图
plt.rcParams['figure.figsize'] = [12, 4]
plt.rcParams['font.sans-serif'] = ['Source Han Sans SC', 'SimHei', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
fmt = '%.2f%%'
yticks = mtick.FormatStrFormatter(fmt)

# 获取数据的公共交易所对象
common_exchange = ccxt.binance(exchange_basic_config)

data_path = get_folder_path('data', as_path_type=True)


def orders(account_config: AccountConfig, symbol, run_time, hour_offset):
    """
    获取某个币种历史订单数据，做多获取1000条
    :param account_config: 交易所对象
    :param symbol: 币种名
    :param run_time: 运行时间
    :param hour_offset: 分钟偏移
    :return:
                      time     symbol   price    qty  quoteQty  commission commissionAsset  方向
    0  2023-10-08 19:00:00  AERGOUSDT  0.1024  526.0   53.8624       0.526           AERGO     1
    1  2023-10-08 20:00:00  AERGOUSDT  0.1026  225.0   23.0850       0.225           AERGO     1
    """
    trades = account_config.bn.fetch_spot_trades(symbol, run_time)
    if trades.empty:
        return pd.DataFrame()

    # =修改一下time列的格式
    trades['time'] = pd.to_datetime(trades['time'], unit='ms')
    trades['time'] = trades['time'].map(lambda x: x.strftime('%Y-%m-%d %H:%M:%S'))
    trades['time'] = pd.to_datetime(trades['time'])
    trades['time'] = trades['time'] + timedelta(hours=utc_offset)
    trades['time'] = trades['time'].map(
        lambda x: x.replace(minute=int(hour_offset[:-1]), second=0, microsecond=0).strftime(
            "%Y-%m-%d %H:%M:%S") if x.minute < (int(hour_offset[:-1]) + 30) else (
                x.replace(minute=int(hour_offset[:-1]), second=0, microsecond=0) + pd.to_timedelta('1h')).strftime(
            "%Y-%m-%d %H:%M:%S"))
    trades = trades.sort_values('time').reset_index(drop=True)

    # sleep 3秒
    time.sleep(3)

    return trades


def get_orders(account_config: AccountConfig, symbol, run_time, start_time=None, end_time=None,
               default_time='2023-09-28 00:00:00', nums=100,
               hour_offset='0m'):
    """
    获取某个币种所有的历史订单数据
    :param account_config: 账户配置对象
    :param symbol: 币种名
    :param run_time: 运行时间
    :param start_time: 开始时间，非必要
    :param end_time: 结束时间，非必要
    :param default_time: 默认时间，只获取默认时间之后的数据
    :param nums: 循环次数，如果历史订单数据超过1000条，每次取出1000条订单数据，默认循环100次
    :param hour_offset: 分钟偏移
    :return:
                     time     symbol   price    qty  quoteQty  commission commissionAsset  方向
    0 2023-10-08 19:00:00  AERGOUSDT  0.1024  526.0   53.8624       0.526           AERGO     1
    1 2023-10-08 20:00:00  AERGOUSDT  0.1026  225.0   23.0850       0.225           AERGO     1
    """

    # =定义一个保存订单数据的列表，一次最多能拿到1000条数据（可能拿不完）
    trades_list = []
    # =根据run_time拿到订单数据（最多拿到run_time前1000条订单数据）
    trades = orders(account_config, symbol, run_time=run_time, hour_offset=hour_offset)
    if trades.empty:  # 如果拿到的是空df，即没有历史订单信息，则直接返回空df
        return pd.DataFrame()

    # =因为取一次订单信息最多只能取1000条，可能获取不完，这里会循环获取
    for i in range(nums):
        # =如果取到的订单数据为1000条，则代表实际的订单数据已经超过1000条了，一次api返回的订单数据不全，需要重复获取订单数据
        if len(trades) == 1000:
            # =获取订单时可能只会获取最老时间的一部分，不完整，这里将这个时间截掉
            trades = trades[trades['time'] != trades['time'].min()].reset_index(drop=True)
            if trades.empty:
                break
            # =将删除了第一笔交易时间的数据append到列表中
            trades_list.append(trades)
            # =以此时的第一笔订单交易时间为准，向前推半小时，再次获取这个时间点之前的订单数据
            trades = orders(account_config, symbol,
                            run_time=(pd.to_datetime(trades['time'].iloc[0]) - timedelta(minutes=30)),
                            hour_offset=hour_offset)
        else:
            # =如果得到的trades<1000条，则已经获取了完整的订单数据，将得到的订单数据append到列表中，以后进行合并
            trades_list.append(trades)
            break  # 退出循环
    # =合并数据并整理数据
    all_trades = pd.concat(trades_list, axis=0, copy=False)
    all_trades = all_trades[all_trades['time'] > default_time]  # 截取默认时间之后的订单数据
    all_trades = all_trades.sort_values('time', ascending=True).reset_index(drop=True)  # 整理数据

    # =如果限定了开始时间和结束时间，则截取下来
    if start_time:
        all_trades = all_trades[all_trades['time'] >= start_time].reset_index(drop=True)
    if end_time:
        all_trades = all_trades[all_trades['time'] <= end_time].reset_index(drop=True)
    # =转化time列的格式
    all_trades['time'] = pd.to_datetime(all_trades['time'])

    return all_trades


def save_position_info(all_trades, path, ticker_price):
    """
    保存币种的历史持仓信息
    :param all_trades: 该币种的所有订单数据
    :param path: 保存路径
    :param ticker_price: 该币种的最新tick价格
    :return:
    """
    # =新建df保存持仓信息
    symbol_info = pd.DataFrame()
    # =取出币种名
    symbol = all_trades['symbol'].iloc[0]
    # =将 成交量 减去 手续费消耗的该币种的数量 得到实际持仓量
    all_trades['qty_real'] = all_trades.apply(
        lambda x: (x['qty'] - x['commission']) if x['commissionAsset'] == symbol.split('USDT')[0] else x['qty'], axis=1)
    # =成交额按 持仓量/成交量 比例乘一下得到实际持仓额
    all_trades['quoteQty_real'] = all_trades['quoteQty'] * all_trades['qty_real'] / all_trades['qty']

    # =对time进行groupby，将同一时间的订单聚合为一笔交易
    for _time, data in all_trades.groupby('time'):
        # =获取索引
        max_idx = 0 if symbol_info.empty else symbol_info.index.max() + 1
        symbol_info.loc[max_idx, 'time'] = pd.to_datetime(_time)  # 记录时间
        symbol_info.loc[max_idx, 'symbol'] = symbol  # 记录币种
        # =因为存在同一时间既出现买入该币种，又出现卖出该种的情况，所以这里计算成交量和成交额时考虑下方向
        symbol_info.loc[max_idx, '成交量'] = (data['qty_real'] * data['方向']).sum()  # 记录成交量
        symbol_info.loc[max_idx, '成交额'] = (data['quoteQty_real'] * data['方向']).sum()  # 记录成交额
        if symbol_info.loc[max_idx, '成交量'] >= 0:
            symbol_info.loc[max_idx, '方向'] = 1  # 记录方向
        else:
            symbol_info.loc[max_idx, '方向'] = -1  # 记录方向
        # =判断订单数据是否只有一笔
        if len(data) == 1:  # 如果只有一笔数据，则成交均价即为那笔数据的成交价格
            symbol_info.loc[max_idx, '成交均价'] = data['price'].iloc[0]
        else:  # 如果为多笔订单数据，则成交均价为 成交额/成交量
            symbol_info.loc[max_idx, '成交均价'] = symbol_info.loc[max_idx, '成交额'] / symbol_info.loc[
                max_idx, '成交量']

    # =判断该币种目前有没有本地文件
    if not os.path.exists(path):  # 如果没有本地文件
        # =记录各个时间点的持仓量
        symbol_info['持仓量'] = symbol_info['成交量'].cumsum()
        # =如果以当前价格衡量的持仓额小于5U，则认为该币种已经清仓
        symbol_info['是否清仓'] = np.where((symbol_info['持仓量'] * ticker_price) < 5, True, False)
        # =设置下清仓后的默认持仓量
        symbol_position = 0
        # ===如果该币种之前存在过清仓操作
        if True in symbol_info['是否清仓'].values:
            # =获取最近一次清仓操作的索引
            idx = symbol_info[symbol_info['是否清仓'] == True].index[-1]
            # =更新下清仓之后的持仓量剩余多少(可能卖不干净)
            symbol_position = symbol_info.loc[idx, '持仓量']
            # =截取清仓之后的数据
            symbol_info = symbol_info.iloc[idx + 1:].reset_index(drop=True)
        # =记录各个时间点的持仓额，这里需要考虑下之前卖不干净的历史持仓
        symbol_info['持仓额'] = symbol_info['成交额'].cumsum() + symbol_position * ticker_price
        symbol_info['持仓均价'] = symbol_info['持仓额'] / symbol_info['持仓量']  # 记录各个时间点的持仓均价
        # =删除是否清仓列
        symbol_info.drop('是否清仓', axis=1, inplace=True)

        # =如果币种文件不为空，则将每个币种的最近的一次开仓信息单独保存为一个csv文件
        if not symbol_info.empty:
            symbol_info.to_csv(path, encoding='gbk', index=False)
    else:
        # =如果某个币种有历史持仓数据，则先读取进来
        old_symbol_info = pd.read_csv(path, encoding='gbk')

        # =截取出来新数据(新数据即为历史持仓中没有考虑到的数据，中间有N个小时没有跑，这个时间点直接跑也不会出错)
        symbol_info = symbol_info[symbol_info['time'] > old_symbol_info['time'].iloc[-1]].reset_index(drop=True)
        # =如果拿到的数据都是老数据，已经全部处理过了，则不需要再次进行处理(测试的时候多次运行，不会出现bug)
        if symbol_info.empty:
            return

        # =整理数据
        symbol_info = symbol_info.sort_values('time', ascending=True).reset_index(drop=True)

        # =将新数据与老数据中的最新一条数据合并起来 并 整理
        symbol_info = pd.concat(
            [old_symbol_info[old_symbol_info['time'] == old_symbol_info['time'].max()], symbol_info],
            axis=0, copy=False)
        symbol_info = symbol_info.reset_index(drop=True)

        # =将 成交量、成交额、方向 修改为 持仓量、持仓额、1，用于之后计算持仓均价
        symbol_info.loc[0, '成交量'] = symbol_info.loc[0, '持仓量']
        symbol_info.loc[0, '成交额'] = symbol_info.loc[0, '持仓额']
        symbol_info.loc[0, '方向'] = 1

        # =计算持仓量、持仓额、持仓均价
        symbol_info['持仓量'] = symbol_info['成交量'].cumsum()
        # =如果根据当前的持仓量、当前的价格计算得到的持仓额小于5U，则将持仓信息中的该币种的文件删除
        symbol_info['是否清仓'] = np.where((symbol_info['持仓量'] * ticker_price) < 5, True, False)
        if True in symbol_info['是否清仓'].values:
            os.remove(path)
            return
        symbol_info['持仓额'] = symbol_info['成交额'].cumsum()
        symbol_info['持仓均价'] = symbol_info['持仓额'] / symbol_info['持仓量']

        # =删除第一条数据(第一条数据为之前合并的老数据)，只保留新数据
        symbol_info = symbol_info.drop(0, axis=0).reset_index(drop=True)
        # =删除是否清仓列
        symbol_info.drop('是否清仓', axis=1, inplace=True)

        # =保存数据
        symbol_info.to_csv(path, encoding='gbk', index=False, header=False, mode='a')


def get_orders_info(new_trades, spot_last_price, symbol):
    """
    根据订单数据获取订单统计信息
    :param new_trades: 最近一小时的订单数据
    :param spot_last_price: 最新各个现货币种的tick价格
    :param symbol: 币种名
    return:
        该币种最近小时的订单统计信息
                     time   symbol 方向  成交均价  平均滑点  成交量    成交额  手续费      滑点亏损  拆分次数      成交价列表     成交量列表         成交额列表  成交最高价与首次交易价相差(%)  成交最低价与首次交易价相差(%)  成交最高价与成交最低价相差(%)
    0 2023-10-30 17:00:00  GNOUSDT    1     104.1       0.0   1.068  111.1788    0.08  1.421085e-14         2  [104.1, 104.1]  [0.96, 0.108]  [99.936, 11.2428]                            0.0                            0.0                            0.0
    """
    # =如果使用BNB来抵扣手续费，则将使用的BNB根据BNB的当前价格转化为U
    if len(new_trades[new_trades['commissionAsset'] == 'BNB']) > 0:
        new_trades.loc[new_trades['commissionAsset'] == 'BNB', 'commission'] *= spot_last_price['BNBUSDT']
        new_trades.loc[new_trades['commissionAsset'] == 'BNB', 'commissionAsset'] = 'USDT'

    # =如果还有其他非USDT的币种来抵扣手续费，则将其也转化为U(比如BTTC币种，在购买BTTC时无法使用BNB进行抵扣，则也将其使用的手续费转化为对应的U)
    if len(new_trades[new_trades['commissionAsset'] != 'USDT']) > 0:
        new_trades.loc[new_trades['commissionAsset'] != 'USDT', 'commission'] *= spot_last_price[symbol]
        new_trades.loc[new_trades['commissionAsset'] != 'USDT', 'commissionAsset'] = 'USDT'

    # ===生成订单信息
    # =新建df统计每个币种的结果
    order_info = new_trades.loc[0, ['time', 'symbol', '方向']].to_frame().T

    if len(new_trades) == 1:  # 如果该币种在该小时内只有一条订单记录
        # =成交均价即为这个交易价格
        order_info['成交均价'] = new_trades['price'].iloc[0]
        # =如果以一笔订单成交则没有滑点
        order_info['平均滑点'] = 0.
    else:  # 如果该币种在该小时内有多条订单记录，则计算成交均价以及平均滑点
        # =成交均价 = 总成交额 / 总成交量
        order_info['成交均价'] = round(new_trades['quoteQty'].sum() / new_trades['qty'].sum(), 6)
        # =平均滑点 = 成交均价 / 第一笔订单的价格 - 1
        order_info['平均滑点'] = round(
            (new_trades['quoteQty'].sum() / new_trades['qty'].sum()) / new_trades['price'].iloc[0] - 1, 6)

    order_info['成交量'] = round(new_trades['qty'].sum(), 6)  # 记录成交量
    order_info['成交额'] = round(new_trades['quoteQty'].sum(), 6)  # 记录成交额
    order_info['手续费'] = round(new_trades['commission'].sum(), 2)  # 记录手续费(以U计价)
    # =计算滑点亏损时需要判断下方向
    if order_info.loc[0, '方向'] == 1:
        # 如果为买入，则 滑点亏损 = 实际发生的成交额 - 以第一笔交易价格计算得到的成交额(没有滑点时的成交额)
        # 比如：以第一笔价格计算得到的成交额为100，而实际发生的成交额为102，则买入该币种多支付了2块钱，即亏损了2块钱，
        # 如果滑点亏损为负，则代表该滑点让你少付了点钱，产生了盈利
        order_info['滑点亏损'] = new_trades['quoteQty'].sum() - new_trades['price'].iloc[0] * new_trades['qty'].sum()
    else:
        # 如果为卖出，则 滑点亏损 = 以第一笔交易价格计算得到的成交额(没有滑点时的成交额) - 实际发生的成交额
        order_info['滑点亏损'] = new_trades['price'].iloc[0] * new_trades['qty'].sum() - new_trades['quoteQty'].sum()
    order_info['拆分次数'] = len(new_trades)  # 记录拆分次数
    order_info['成交价列表'] = [new_trades['price'].to_list()]  # 记录拆分成交的各成交价格
    order_info['成交量列表'] = [new_trades['qty'].to_list()]  # 记录拆分成交的各成交量
    order_info['成交额列表'] = [new_trades['quoteQty'].to_list()]  # 记录拆分成交的各成交额
    # 记录成交最高价与第一笔交易价格的滑点
    order_info['成交最高价与首次交易价相差(%)'] = round(
        order_info['成交价列表'].map(lambda x: 0 if len(x) == 1 else (np.max(np.array(x)) / x[0] - 1)), 6)
    # 记录成交最低价与第一笔交易价格的滑点
    order_info['成交最低价与首次交易价相差(%)'] = round(
        order_info['成交价列表'].map(lambda x: 0 if len(x) == 1 else (np.min(np.array(x)) / x[0] - 1)), 6)
    # 记录成交最高价与成交最低价的滑点
    order_info['成交最高价与成交最低价相差(%)'] = round(
        order_info['成交价列表'].map(lambda x: 0 if len(x) == 1 else (np.max(np.array(x)) / np.min(np.array(x)) - 1)),
        6)

    return order_info


def get_all_order_info(account_config: AccountConfig, symbol_list, run_time, default_time, spot_last_price):
    """
    监测每小时的交易信息、生成各币种持仓信息
    :param account_config: 账户配置
    :param symbol_list: 现货币种列表
    :param run_time: 获取订单指定的运行时间
    :param default_time: 获取订单时默认时间，截取默认时间之后的所有历史订单
    :param spot_last_price: 现货各币种的ticker价格
    return:
                time    symbol 方向    成交均价  平均滑点        成交量      成交额  手续费      滑点亏损  拆分次数                                         成交价列表                                         成交量列表                                         成交额列表  成交最高价与首次交易价相差(%)  成交最低价与首次交易价相差(%)  成交最高价与成交最低价相差(%)
    0   2023-10-13 13:59   ASTUSDT    1    0.082400  0.000000  7.100000e+01    5.850400    0.00 -8.881784e-16         1                                           [0.0824]                                             [71.0]                                           [5.8504]                       0.000000                            0.0                       0.000000
    1   2023-10-13 13:59   XNOUSDT   -1    0.599000  0.000000  1.817900e+02  108.892210    0.08 -1.421085e-14         4                       [0.599, 0.599, 0.599, 0.599]                      [14.84, 17.44, 135.22, 14.29]             [8.88916, 10.44656, 80.99678, 8.55971]                       0.000000                            0.0                       0.000000
    2   2023-10-13 13:59  BTTCUSDT    1    0.000000  0.000000  3.057425e+08  113.124736    0.11  1.421085e-14         2                                 [3.7e-07, 3.7e-07]                          [270270270.0, 35472259.0]                          [99.9999999, 13.12473583]                       0.000000                            0.0                       0.000000
    """

    # =新建持仓信息文件夹
    dir_path = Path(data_path) / account_config.name / '持仓信息'
    dir_path.mkdir(parents=True, exist_ok=True)

    # =创建一个保存各个币种最近小时订单统计信息的列表
    order_info_list = []

    # ===循环每个币种获取订单统计信息、生成各币种持仓信息
    for symbol in tqdm(symbol_list):
        # =获取到该币种的所有历史订单数据，默认获取 default_time 之后的全部订单数据
        all_trades = get_orders(
            account_config, symbol, run_time + timedelta(minutes=30), default_time,
            hour_offset=account_config.hour_offset
        )
        # =判断该币种是否存在历史订单，如果不存在则说明没有交易过该币种，跳过
        if all_trades.empty:
            continue

        # =生成保存文件的路径
        path = dir_path / f'{symbol}.csv'
        # =获取该币种到最新的ticker价格
        ticker_price = spot_last_price[symbol]
        # =保存该币种的持仓信息数据
        save_position_info(all_trades, path, ticker_price)

        # =截取下来最近一小时的订单信息
        new_orders = all_trades[all_trades['time'] > (run_time - timedelta(minutes=30))].reset_index(drop=True)
        # =判断该币种最近一个交易周期是否交易过该币种，如果交易过，则记录下最近的订单数据，如果没有交易过，则跳过
        if new_orders.empty:
            continue
        # =根据该币种的订单数据生成订单统计信息
        order_info = get_orders_info(new_orders, spot_last_price, symbol)
        order_info_list.append(order_info)

    if len(order_info_list) == 0:
        return pd.DataFrame()

    # =将所有币种交易的订单信息合成 总的df
    all_order_info = pd.concat(order_info_list, ignore_index=True, copy=False)

    return all_order_info


def get_stats_info(buyer, seller, spot_equity):
    """
    获取某个小时所有币种的订单统计数据
    :param buyer: 买入订单数据
    :param seller: 卖出订单数据
    :param spot_equity: 现货账户净值
    :return:
        time  方向  币种数量  成交额  换手率  手续费  逐笔成交最大拆分次数 平均滑点  滑点亏损 最大不利滑点 最大有利滑点
        0  2023-10-13 13:59   1.0       9.0  482.87  15.44%    0.37                   9.0  0.0135%    0.1352      0.1577%         0.0%
        1  2023-10-13 13:59  -1.0       4.0  436.80  13.96%    0.32                   9.0  0.0025%   -0.0109         0.0%      0.1088%
    """

    # =新建df保存结果
    stats_info = pd.DataFrame()

    # =获取订单的统计信息
    # 买入
    if len(buyer) > 0:
        stats_info.loc[0, 'time'] = buyer['time'].mode()[0]  # 记录买入订单时间
        stats_info.loc[0, '方向'] = 1  # 记录买卖方向
        stats_info.loc[0, '币种数量'] = len(buyer)  # 记录买入币种的数量
        stats_info.loc[0, '成交额'] = round(buyer['成交额'].sum(), 2)  # 记录买入成交额
        stats_info.loc[0, '换手率'] = round(buyer['成交额'].sum() / spot_equity, 4)  # 记录买入换手率
        stats_info.loc[0, '手续费'] = round(buyer['手续费'].sum(), 2)  # 记录买入手续费
        stats_info.loc[0, '逐笔成交最大拆分次数'] = buyer['拆分次数'].max()  # 记录买入订单的最大拆分次数
        stats_info.loc[0, '平均滑点'] = round(buyer['平均滑点'].mean(), 6)  # 记录买入订单的平均滑点
        stats_info.loc[0, '滑点亏损'] = round(buyer['滑点亏损'].sum(), 4)  # 记录买入订单的滑点亏损
        stats_info.loc[0, '最大不利滑点'] = round(buyer['成交最高价与首次交易价相差(%)'].max(), 6)  # 记录买入订单的最大不利滑点
        stats_info.loc[0, '最大有利滑点'] = round(buyer['成交最低价与首次交易价相差(%)'].min(), 6)  # 记录买入订单的最大有利滑点
    else:
        stats_info.loc[0, 'time'] = None  # 记录买入订单时间
        stats_info.loc[0, '方向'] = 1  # 记录买卖方向
        stats_info.loc[0, '换手率'] = 0
        stats_info.loc[0, '平均滑点'] = 0
        stats_info.loc[0, '最大不利滑点'] = 0
        stats_info.loc[0, '最大有利滑点'] = 0

    # 卖出
    if len(seller) > 0:
        stats_info.loc[1, 'time'] = seller['time'].mode()[0]  # 记录卖出订单时间
        stats_info.loc[1, '方向'] = -1  # 记录买卖方向
        stats_info.loc[1, '币种数量'] = len(seller)  # 记录卖出币种的数量
        stats_info.loc[1, '成交额'] = round(seller['成交额'].sum(), 2)  # 记录卖出成交额
        stats_info.loc[1, '换手率'] = round(seller['成交额'].sum() / spot_equity, 4)  # 记录卖出换手率
        stats_info.loc[1, '手续费'] = round(seller['手续费'].sum(), 2)  # 记录卖出手续费
        stats_info.loc[1, '逐笔成交最大拆分次数'] = seller['拆分次数'].max()  # 记录卖出订单的最大拆分次数
        stats_info.loc[1, '平均滑点'] = round(seller['平均滑点'].mean(), 6)  # 记录卖出订单的平均滑点
        stats_info.loc[1, '滑点亏损'] = round(seller['滑点亏损'].sum(), 4)  # 记录卖出订单的滑点亏损
        stats_info.loc[1, '最大不利滑点'] = round(seller['成交最低价与首次交易价相差(%)'].min(), 6)  # 记录卖出订单的最大不利滑点
        stats_info.loc[1, '最大有利滑点'] = round(seller['成交最高价与首次交易价相差(%)'].max(), 6)  # 记录卖出订单的最大有利滑点
    else:
        stats_info.loc[1, 'time'] = None  # 记录卖出订单时间
        stats_info.loc[1, '方向'] = -1  # 记录买卖方向
        stats_info.loc[1, '换手率'] = 0
        stats_info.loc[1, '平均滑点'] = 0
        stats_info.loc[1, '最大不利滑点'] = 0
        stats_info.loc[1, '最大有利滑点'] = 0

    return stats_info


def get_order_msg(run_time, order_info, stats_info, buyer, seller):
    """
    发送订单信息
    :param run_time: 运行时间
    :param order_info: 详细订单数据
    :param stats_info: 订单统计数据
    :param buyer: 买入订单数据
    :param seller: 卖出订单数据
    :return:
        订单监测发送信息
            2023-10-18 14:00:00 现货交易
            成交额：851.52
            买入成交额：496.14
            卖出成交额：355.38
            手续费(U)：0.63
            买入手续费(U)：0.37
            卖出手续费(U)：0.26
            总换手率：0.57%
            offset换手率：0.57%
            滑点亏损：0.1735
            买入滑点亏损：0.0592
            卖出滑点亏损：0.1143
            滑点亏损比：0.0204%
    """

    # =判断当前小时是否买入了币种
    if len(buyer) > 0:
        buyer_volume = round(buyer["成交额"].sum(), 2)  # 买入成交额
        buyer_fee = round(buyer["手续费"].sum(), 2)  # 买入手续费
        buyer_slip_loss = round(buyer["滑点亏损"].sum(), 4)  # 买入滑点亏损
    else:  # 如果当前小时没有买入，设置为0
        buyer_volume = 0
        buyer_fee = 0
        buyer_slip_loss = 0

    # =判断当前小时是否卖出了币种
    if len(seller) > 0:
        seller_volume = round(seller["成交额"].sum(), 2)  # 卖出成交额
        seller_fee = round(seller["手续费"].sum(), 2)  # 卖出手续费
        seller_slip_loss = round(seller["滑点亏损"].sum(), 4)  # 卖出滑点亏损
    else:  # 如果当前小时没有卖出，设置为0
        seller_volume = 0
        seller_fee = 0
        seller_slip_loss = 0

    all_volume = round(order_info["成交额"].sum(), 2)  # 总成交额
    all_fee = round(order_info["手续费"].sum(), 2)  # 总手续费
    all_turnover_rate = round(stats_info["换手率"].mean() * 100, 2)  # 总换手率
    all_slip_loss = round(order_info["滑点亏损"].sum(), 4)  # 总滑点亏损
    all_slip_loss_ratio = abs(round(order_info["滑点亏损"].sum() / order_info["成交额"].sum() * 100, 4))  # 滑点亏损占比

    # =企业微信/钉钉中发送的信息
    order_msg = f'{run_time} 现货交易\n'  # 时间 交易类型
    order_msg += f'成交额：{all_volume}\n'  # 总成交额
    order_msg += f'买入成交额：{buyer_volume}\n'  # 买入成交额
    order_msg += f'卖出成交额：{seller_volume}\n'  # 卖出成交额
    order_msg += f'手续费(U)：{all_fee}\n'  # 总手续费
    order_msg += f'买入手续费(U)：{buyer_fee}\n'  # 买入手续费
    order_msg += f'卖出手续费(U)：{seller_fee}\n'  # 卖出手续费
    order_msg += f'总换手率：{all_turnover_rate}%\n'  # 总换手率
    order_msg += f'滑点亏损：{all_slip_loss}\n'  # 总滑点亏损
    order_msg += f'买入滑点亏损：{buyer_slip_loss}\n'  # 买入滑点亏损
    order_msg += f'卖出滑点亏损：{seller_slip_loss}\n'  # 卖出滑点亏损
    order_msg += f'滑点亏损比：{all_slip_loss_ratio}%\n'  # 滑点亏损比

    # =发送买入币种、卖出币种的信息
    bs_msg = ''
    if not buyer.empty:  # 如果存在买入币种信息，则将 买入的币种、买入的U 添加到字符串中
        df_buy = buyer.copy()
        df_buy = df_buy.sort_values('成交额', ascending=False).reset_index(drop=True)
        df_buy.set_index('symbol', inplace=True)
        bs_msg += f'买入现货币种：\n{df_buy[["成交额"]]}\n'
    if not seller.empty:  # 如果存在卖出币种信息，则将 卖出的币种、卖出的U 添加到字符串中
        df_seller = seller.copy()
        df_seller = df_seller.sort_values('成交额', ascending=False).reset_index(drop=True)
        df_seller.set_index('symbol', inplace=True)
        bs_msg += f'卖出现货币种：\n{df_seller[["成交额"]]}\n'

    return order_msg, bs_msg


def save_order_info(order_info, stats_info, run_time, account_name):
    """
    保存订单信息
    :param order_info: 详细订单数据
    :param stats_info: 订单统计数据
    :param run_time: 运行时间
    :param account_name: 账户名称
    :return:
    """

    # ===转换数据格式
    # =转换订单信息的数据格式
    order_info['平均滑点'] = order_info['平均滑点'].map(lambda x: str(round(x * 100, 6)) + '%')
    order_info['成交最高价与首次交易价相差(%)'] = order_info['成交最高价与首次交易价相差(%)'].map(
        lambda x: str(round(x * 100, 6)) + '%')
    order_info['成交最低价与首次交易价相差(%)'] = order_info['成交最低价与首次交易价相差(%)'].map(
        lambda x: str(round(x * 100, 6)) + '%')
    order_info['成交最高价与成交最低价相差(%)'] = order_info['成交最高价与成交最低价相差(%)'].map(
        lambda x: str(round(x * 100, 6)) + '%')

    # =转换统计信息的数据格式
    stats_info['换手率'] = stats_info['换手率'].map(lambda x: str(round(x * 100, 4)) + '%' if str(x) != 'nan' else x)
    stats_info['平均滑点'] = stats_info['平均滑点'].map(
        lambda x: str(round(x * 100, 6)) + '%' if str(x) != 'nan' else x)
    stats_info['最大不利滑点'] = stats_info['最大不利滑点'].map(
        lambda x: str(round(x * 100, 6)) + '%' if str(x) != 'nan' else x)
    stats_info['最大有利滑点'] = stats_info['最大有利滑点'].map(
        lambda x: str(round(x * 100, 6)) + '%' if str(x) != 'nan' else x)

    # =新建订单监测、详细信息文件夹
    dir_path = get_folder_path(data_path, account_name, '订单监测', '详细信息')

    # =保存订单详细信息
    file_path = get_file_path(dir_path, f'{run_time.strftime("%Y-%m-%d_%H")}_订单详细信息.csv')
    order_info.to_csv(file_path, encoding='gbk', index=False)
    # 删除多余的文件
    del_hist_files(dir_path, 999, file_suffix='.csv')

    # =保存订单统计信息
    dir_path = get_file_path(data_path, account_name, '订单监测', '订单统计信息.csv')
    if os.path.exists(dir_path):  # 如果文件存在，往原有的文件中添加新的结果
        stats_info.to_csv(dir_path, encoding='gbk', index=False, header=False, mode='a')
    else:  # 如果文不件存在，常规的to_csv操作
        stats_info.to_csv(dir_path, encoding='gbk', index=False)


def send_position_result(account_config: AccountConfig, spot_position, swap_position, spot_last_price):
    """
    发送现货、合约持仓信息
    :param account_config: 账户配置
    :param spot_position: 现货持仓
    :param swap_position: 合约持仓
    :param spot_last_price: 现货最新价格
    :return:
              side  change    pos_u   pnl_u  avg_price  cur_price
    symbol
    ARDRUSDT     1  59.24%   419.98  248.81     0.0515     0.0820
    BTSUSDT      1  36.90%   184.30   68.00     0.0071     0.0097
    GLMUSDT      1  26.67%   691.78  184.49     0.1466     0.1857
    """
    send_spot_df = calc_spot_position(spot_position, account_config.name, spot_last_price)
    send_swap_df = calc_swap_position(swap_position)

    utc_now_timestamp = int(datetime.utcnow().timestamp()) * 1000
    save_send_pos_info(utc_now_timestamp, send_spot_df, account_config.name, pos_type='spot')
    save_send_pos_info(utc_now_timestamp, send_swap_df, account_config.name, pos_type='swap')

    for data in [send_spot_df, send_swap_df]:
        if not data.empty:
            send_img_for_dataframe(data, account_config.wechat_webhook_url)


def set_annotate(ax, line_color, x, y):
    # 标注最新值
    last_date = x[-1]
    last_y = y[-1]
    ax.annotate(f'last:{last_y:.1f}%', xy=(last_date, last_y), xytext=(5, -5),
                textcoords='offset points', ha='left', va='top', color=line_color)
    # 标注最大值和最小值
    max_y, max_y_pos = y.max(), np.argmax(y)
    min_y, min_y_pos = y.min(), np.argmin(y)
    ax.annotate(f'max:{max_y:.1f}%', xy=(x[max_y_pos], max_y),
                xytext=(5, 5), textcoords='offset points',
                ha='left', va='bottom', color=line_color)
    ax.annotate(f'min:{min_y:.1f}%', xy=(x[min_y_pos], min_y),
                xytext=(5, -5), textcoords='offset points',
                ha='left', va='top', color=line_color)

    # 新增：计算并标注相对最大值的回撤比例
    drawdown_pct = ((1 + last_y / 100) / (1 + max_y / 100) - 1) * 100
    ax.annotate(f'({drawdown_pct:.1f}%)', xy=(last_date, last_y), xytext=(50, -5),
                textcoords='offset points', ha='left', va='top', color=line_color)


# 动态颜色生成函数
def get_color(index):
    """根据索引生成固定颜色，确保颜色差异度大"""
    # 预定义高对比度颜色列表，避开equity的橙色(#fd9300)，确保颜色差异明显
    high_contrast_colors = [
        '#1f77b4',  # 深蓝色
        '#d62728',  # 深红色
        '#2ca02c',  # 深绿色
        '#9467bd',  # 深紫色
        '#17becf',  # 深青色
        '#e377c2',  # 深粉色
        '#8c564b',  # 深棕色
        '#7f7f7f',  # 深灰色
        '#bcbd22',  # 深橄榄色
        '#ff1493',  # 深粉红色
        '#00ff7f',  # 春绿色
        '#4169e1',  # 皇家蓝
        '#32cd32',  # 柠檬绿
        '#9932cc',  # 深兰花紫
        '#00ced1',  # 深绿松石色
        '#dc143c',  # 深红色
        '#228b22',  # 森林绿
        '#4b0082',  # 靛青色
        '#008b8b',  # 深青色
        '#800080',  # 紫色
        '#006400',  # 深绿色
        '#8b008b',  # 深洋红色
        '#556b2f',  # 深橄榄绿
        '#483d8b',  # 深板岩蓝
    ]
    
    # 如果索引超出预定义颜色，使用HSV色彩空间生成
    if index < len(high_contrast_colors):
        return high_contrast_colors[index]
    else:
        # 使用HSV色彩空间生成颜色，确保色相差异大
        import matplotlib.colors as mcolors
        # 生成色相值，确保颜色间隔足够大
        hue = (index * 137.5) % 360  # 使用黄金角度比例137.5度确保颜色分布均匀
        saturation = 0.8 + (index % 3) * 0.1  # 饱和度在0.8-1.0间变化
        value = 0.7 + (index % 4) * 0.1       # 亮度在0.7-1.0间变化
        
        # 将HSV转换为RGB
        rgb = mcolors.hsv_to_rgb([hue/360, saturation, value])
        return rgb


def draw_equity_and_send_pic(me_conf, equity_df, transfer_df, title, account_config: AccountConfig, is_send=True):
    """
    画资金曲线并发送图片
    :param me_conf:     仓管配置
    :param equity_df:   资金曲线数据
    :param transfer_df: 划转数据
    :param title:       标题
    :param account_config:用户配置
    :param is_send:     是否发送，不发送就保存数据
    """
    # =合并转换划转记录
    equity_df['type'] = 'log'
    equity_df = pd.concat([equity_df, transfer_df], ignore_index=True, copy=False)
    equity_df.sort_values('time', inplace=True)
    equity_df.reset_index(inplace=True, drop=True)
    equity_df['time'] = equity_df['time'].dt.floor('H')
    # =计算净值
    equity_df = net_fund(equity_df)
    equity_df['net'] = (equity_df['净值'] - 1) * 100
    equity_df['max2here'] = equity_df['净值'].expanding().max()
    equity_df['dd2here'] = (equity_df['净值'] / equity_df['max2here'] - 1) * 100
    equity_df['long_ratio'] = equity_df['多头仓位'] / equity_df['账户总净值']
    equity_df['short_ratio'] = equity_df['空头仓位'].abs() / equity_df['账户总净值']
    equity_df['empty_ratio'] = (account_config.leverage - equity_df['long_ratio'] - equity_df['short_ratio']).clip(lower=0)
    columns_to_fill = ['long_ratio', 'short_ratio', 'empty_ratio', '多头选币', '空头选币', '多头单币最大持仓', '空头单币最大持仓']
    equity_df[columns_to_fill] = equity_df[columns_to_fill].fillna(method='ffill')
    equity_df['多头选币'] = equity_df['多头选币'].fillna(0)
    equity_df['空头选币'] = equity_df['空头选币'].fillna(0)
    equity_df['多头单币最大持仓'] = equity_df['多头单币最大持仓'].fillna(0)
    equity_df['空头单币最大持仓'] = equity_df['空头单币最大持仓'].fillna(0)

    time_diff = pd.to_timedelta(utc_offset + 1, unit='hours')
    if not is_send:
        equity_pkl_path = get_file_path(data_path, account_config.name, '账户信息', f'equity.pkl', as_path_type=True)
        equity_df.to_pickle(equity_pkl_path)

        sub_stg_eqs_path = get_file_path(data_path, account_config.name, '账户信息', f'sub_stg_eqs.pkl', as_path_type=True)
        sub_stg_eqs = {}
        for conf in me_conf.factory.config_list:
            sub_stg_file = conf.get_result_folder() / '资金曲线_再择时.csv'
            if not sub_stg_file.exists():
                sub_stg_file = conf.get_result_folder() / '资金曲线.csv'
            sub_stg_equity = pd.read_csv(sub_stg_file, parse_dates=['candle_begin_time'], index_col=0)
            sub_stg_equity['candle_begin_time'] = sub_stg_equity['candle_begin_time'].dt.tz_localize(None) + time_diff
            sub_stg_eqs[conf.name] = sub_stg_equity
        pd.to_pickle(sub_stg_eqs, sub_stg_eqs_path)
        return equity_df

    # =画图
    fig, (ax1, ax3, ax4, ax5, ax6, ax7) = plt.subplots(6, 1, figsize=(12, 16), gridspec_kw={'height_ratios': [3, 1, 1, 1, 1, 3]})
    # 标记买入和卖出点
    buy_signals = equity_df[(equity_df['type'] == 'transfer') & (equity_df['账户总净值'] > 0)]
    # 移除 bf 利息加仓部分: 加仓金额 < 当前资金 * 10% / 365
    buy_signals = buy_signals[buy_signals['账户总净值'] > buy_signals['当前总市值'] * 0.1 / 365]
    sell_signals = equity_df[(equity_df['type'] == 'transfer') & (equity_df['账户总净值'] < 0)]
    ax1.scatter(buy_signals['time'], buy_signals['net'], marker='+', color='black', label='add', s=100)
    ax1.scatter(sell_signals['time'], sell_signals['net'], marker='x', color='red', label='reduce', s=100)
    # 绘图
    # 定义基础颜色
    long_color = np.array([30, 177, 0])
    short_color = np.array([255, 99, 77])
    empty_color = np.array([0, 46, 77])
    equity_df['color'] = '#{:02x}{:02x}{:02x}'.format(*[253, 147, 0])

    # 动态绘制资金曲线
    for i in range(len(equity_df) - 1):
        start_time = equity_df.iloc[i]['time']
        end_time = equity_df.iloc[i + 1]['time']
        start_net = equity_df.iloc[i]['net']
        end_net = equity_df.iloc[i + 1]['net']
        color = equity_df.iloc[i]['color']
        ax1.plot([start_time, end_time], [start_net, end_net], color=color, zorder=3)
    set_annotate(ax1, equity_df['color'].iloc[-1], equity_df['time'].to_numpy() , equity_df['net'].to_numpy())

    # 主资金曲线的代理图例
    proxy_line_equity = mtlines.Line2D([0], [0], color='#fb9300', label='Equity', linewidth=2)
    legend_handles = [proxy_line_equity]  # 初始化图例句柄列表

    position_df = me_conf.factory.result_folder / '仓位比例.csv'
    if position_df.exists():
        position_df = pd.read_csv(position_df, index_col=0)
        position_df = position_df[pd.to_datetime(position_df.index).tz_localize(None) >= equity_df['time'].min() - time_diff]
    else:
        position_df = pd.DataFrame()

    for idx, conf in enumerate(me_conf.factory.config_list):
        sub_stg_file = conf.get_result_folder() / '资金曲线_再择时.csv'
        if not sub_stg_file.exists():
            sub_stg_file = conf.get_result_folder() / '资金曲线.csv'
        sub_stg_equity = pd.read_csv(sub_stg_file, parse_dates=['candle_begin_time'], index_col=0)
        sub_stg_equity['candle_begin_time'] = sub_stg_equity['candle_begin_time'].dt.tz_localize(None)
        sub_stg_equity = sub_stg_equity[sub_stg_equity['candle_begin_time'] >= equity_df['time'].min() - time_diff].reset_index(drop=True)
        if sub_stg_equity.empty:
            continue
        sub_stg_equity['净值'] = 100 * (sub_stg_equity['equity'] / sub_stg_equity.iloc[0]['equity'] - 1)

        # 绘制曲线并添加图例
        line = ax1.plot(
            pd.to_datetime(sub_stg_equity['candle_begin_time']) + time_diff,
            sub_stg_equity['净值'],
            color=get_color(idx),
            label=conf.name if position_df.empty else f'{conf.name}-{round(position_df.iloc[-1][idx] * 100, 4)}%',
            zorder=3,
            alpha=0.5
        )[0]

        set_annotate(ax1, line.get_c(), line.get_xdata(), line.get_ydata())

        # 记录图例句柄
        legend_handles.append(line)

    # 处理图例
    ax1.legend(
        handles=legend_handles,
        labels=[h.get_label() for h in legend_handles],
        loc='upper left',
        frameon=True
    )

    ax1.grid(True, which='both', linestyle='--', linewidth=0.5, zorder=1)
    ax1.set_title(f'{title} dd2here: {equity_df.iloc[-1]["dd2here"]:.2f}% eq_max: {equity_df["net"].max():.2f}%')
    ax1.yaxis.set_major_formatter(yticks)

    # 创建右侧轴
    # 右轴 回撤
    ax2 = ax1.twinx()
    ax2.fill_between(equity_df['time'], equity_df['dd2here'], 0, color='#d5d5d5', alpha=0.2, zorder=0)
    ax2.set_ylabel('dd2here (%)')
    ax2.yaxis.set_major_formatter(yticks)

    # 子图：多头和空头占比
    ax3.stackplot(equity_df['time'], equity_df['long_ratio'], equity_df['short_ratio'], equity_df['empty_ratio'],
                  labels=['Long', 'Short', 'empty'],
                  colors=[long_color / 255, short_color / 255, empty_color / 255], alpha=0.9)
    ax3.set_ylabel('Ratio')
    ax3.set_xlabel('Time')
    ax3.legend(loc='upper left')
    ax3.grid(True, linestyle='--', linewidth=0.5)

    # 新增子图：多头/空头选币数量（新的ax4）
    ax4.plot(equity_df['time'], equity_df['多头选币'],
             label='多头选币', color='green', linewidth=2, alpha=0.5)
    ax4.plot(equity_df['time'], equity_df['空头选币'],
             label='空头选币', color='red', linewidth=2, alpha=0.5)
    ax4.set_ylabel('数量')
    ax4.legend(loc='upper left')
    ax4.grid(True, linestyle='--', linewidth=0.5)
    ax4.set_xlabel('Time')

    # 新增子图：多头/空头选币数量（新的ax5）
    ax5.plot(equity_df['time'], equity_df['多头单币最大持仓'],
             label='多头最大持仓', color='green', linewidth=2, alpha=0.5)
    ax5.plot(equity_df['time'], equity_df['空头单币最大持仓'],
             label='空头最大持仓', color='red', linewidth=2, alpha=0.5)
    ax5.set_ylabel('单币持仓(MAX)')
    ax5.legend(loc='upper left')
    ax5.grid(True, linestyle='--', linewidth=0.5)
    ax5.set_xlabel('Time')

    # 新增子图：仓位分配图
    if not position_df.empty:
        # 将索引转换为 datetime 对象
        position_time = pd.to_datetime(position_df.index).tz_localize(None)
        for idx, conf in enumerate(me_conf.factory.config_list):
            ax6.plot(position_time, position_df[str(idx)],
                     label=conf.name, color=get_color(idx), linewidth=2, alpha=0.5)
    ax6.set_ylabel('策略仓位比例')
    ax6.legend(loc='upper left')
    ax6.grid(True, linestyle='--', linewidth=0.5)
    ax6.set_xlabel('Time')

    # 增加资金曲线副图
    ax7.scatter(buy_signals['time'], buy_signals['net'], marker='+', color='black', label='add', s=100)
    ax7.scatter(sell_signals['time'], sell_signals['net'], marker='x', color='red', label='reduce', s=100)
    ax7.plot(equity_df['time'].to_numpy() , equity_df['net'].to_numpy(), color=equity_df['color'].iloc[-1], zorder=3)
    set_annotate(ax7, equity_df['color'].iloc[-1], equity_df['time'].to_numpy() , equity_df['net'].to_numpy())

    # 处理图例
    ax7.legend(
        handles=[proxy_line_equity],
        labels=[proxy_line_equity.get_label()],
        loc='upper left',
        frameon=True
    )

    ax7.grid(True, which='both', linestyle='--', linewidth=0.5, zorder=1)
    ax7.set_title(f'{title} dd2here: {equity_df.iloc[-1]["dd2here"]:.2f}% eq_max: {equity_df["net"].max():.2f}%')
    ax7.yaxis.set_major_formatter(yticks)

    # 创建右侧轴
    # 右轴 回撤
    ax8 = ax7.twinx()
    ax8.fill_between(equity_df['time'], equity_df['dd2here'], 0, color='#d5d5d5', alpha=0.2, zorder=0)
    ax8.set_ylabel('dd2here (%)')
    ax8.yaxis.set_major_formatter(yticks)

    plt.tight_layout()

    # =定义导出图片位置
    pos_pic_path = get_file_path(data_path, 'pos.png')
    # =保存图片
    plt.savefig(pos_pic_path)
    # =发送图片
    send_wechat_work_img(pos_pic_path, account_config.wechat_webhook_url)


def save_and_send_equity_info(account_config: AccountConfig, me_conf, spot_position, swap_position, spot_equity, account_equity, spot_usdt,
                              seed_coin_spot_equity):
    """
    保存、发送账户信息
    :param account_config: 账户配置对象
    :param me_conf: 仓管配置对象
    :param spot_position: 现货持仓
    :param swap_position: 合约持仓
    :param spot_equity: 现货净值
    :param account_equity: 账户净值
    :param spot_usdt: 现货的u
    :param seed_coin_spot_equity: 种子现货价值
    :return:
    """
    # =创建需要存储equity的df
    new_equity_df = pd.DataFrame()
    # 记录时间
    new_equity_df.loc[0, 'time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # 记录账户总净值
    new_equity_df.loc[0, '账户总净值'] = round(account_equity, 2)
    # 记录多头现货
    new_equity_df.loc[0, '多头现货'] = round(spot_equity - spot_usdt - seed_coin_spot_equity, 2)

    # =追加信息到本地存储中
    swap_send_df = calc_swap_position(swap_position)
    if swap_send_df is None or swap_send_df.empty:
        new_equity_df.loc[0, '多头合约'] = 0  # 记录多头合约，合约为空时，无合约多头
        new_equity_df.loc[0, '多头仓位'] = round(spot_equity - spot_usdt - seed_coin_spot_equity, 2) # 记录多头仓位，合约为空时，多头仓位就只有现货仓位
        new_equity_df.loc[0, '空头仓位'] = 0  # 记录空头仓位，合约为空时，无空头仓位
    else:
        # 记录多头合约
        new_equity_df.loc[0, '多头合约'] = round(swap_send_df[swap_send_df['side'] == 1]['pos_u'].sum(), 2)
        # 记录多头仓位
        new_equity_df.loc[0, '多头仓位'] = round(spot_equity - spot_usdt - seed_coin_spot_equity + swap_send_df[swap_send_df['side'] == 1]['pos_u'].sum(), 2)
        # 记录空头仓位
        new_equity_df.loc[0, '空头仓位'] = round(swap_send_df[swap_send_df['side'] == -1]['pos_u'].sum(), 2)
    new_equity_df.loc[0, '多头选币'] = swap_position[swap_position['当前持仓量'] > 0].shape[0] + spot_position.shape[0]
    new_equity_df.loc[0, '空头选币'] = swap_position[swap_position['当前持仓量'] < 0].shape[0]
    new_equity_df.loc[0, '多头单币最大持仓'] = round(
        max(swap_position[swap_position['当前持仓量'] > 0]['仓位价值'].max() if not swap_position.empty else 0,
            spot_position['仓位价值'].max() if not spot_position.empty else 0) / account_equity, 2)
    new_equity_df.loc[0, '空头单币最大持仓'] = round(
        swap_position[swap_position['当前持仓量'] < 0]['仓位价值'].abs().max() / account_equity, 2) if not swap_position.empty else 0

    # =新建文件夹路径，保存数据
    equity_file_path = get_file_path(data_path, account_config.name, '账户信息', 'equity.csv')
    # =判断文件是否存在
    if os.path.exists(equity_file_path):  # 如果存在
        # 读取数据
        equity_df = pd.read_csv(equity_file_path, encoding='gbk', parse_dates=['time'])
        # 容错代码，转换时间周期
        equity_df['time'] = pd.to_datetime(equity_df['time'], format='mixed')
        # 保留近一个半小时的数据，增加一点提前下单造成的时间容错
        old_equity_df = equity_df[equity_df['time'] > datetime.now() - pd.Timedelta(hours=1, minutes=30)]
        # =数据整理
        old_equity_df = old_equity_df.sort_values('time', ascending=True).reset_index(drop=True)

        # =将保存的全部历史账户数据与最新账户数据合并
        equity_df = pd.concat([equity_df, new_equity_df], axis=0, copy=False)
        equity_df['time'] = pd.to_datetime(equity_df['time'])  # 修改时间格式
        equity_df = equity_df.sort_values('time').reset_index(drop=True)  # 整理数据

        # =记录一下账户总净值的最大值和最小值
        max_all_equity = round(equity_df['账户总净值'].max(), 2)
        min_all_equity = round(equity_df['账户总净值'].min(), 2)

        # ===输出近期走势图
        equity_df_temp = equity_df.reset_index(drop=True)
        _start_time = equity_df_temp.iloc[0]['time']
        # =获取划转记录
        transfer_df = account_config.bn.fetch_transfer_history()
        transfer_path = get_file_path(data_path, account_config.name, '账户信息', 'transfer.csv')
        transfer_df = get_and_save_local_transfer(transfer_df, transfer_path)
        # =构建上线后所有数据
        equity_df0 = equity_df_temp.copy()
        full_hist_df = draw_equity_and_send_pic(me_conf, equity_df0, transfer_df, 'equity-curve(All days)', account_config, is_send=False)
        # =构建近30天数据
        duration = '720h'
        equity_df1 = equity_df_temp[equity_df_temp['time'] >= equity_df_temp['time'].max() - pd.to_timedelta(duration)].reset_index(drop=True)
        # 绘图
        draw_equity_and_send_pic(me_conf, equity_df1, transfer_df, 'equity-curve(last 30 days)', account_config)

        # =构建近7天数据
        duration = '168h'
        equity_df2 = equity_df_temp[equity_df_temp['time'] >= equity_df_temp['time'].max() - pd.to_timedelta(duration)].reset_index(drop=True)
        # 绘图
        draw_equity_and_send_pic(me_conf, equity_df2, transfer_df, 'equity-curve(last 7 days)', account_config)
    else:  # 如果不存在，则创建一个新的df
        old_equity_df = pd.DataFrame()
        max_all_equity = np.nan  # 历史最高为nan
        min_all_equity = np.nan  # 历史最低为nan
        equity_df = new_equity_df
        full_hist_df = None  # 第一次运行，还没有历史数据，跳过净值法历史最高/回撤统计

    # =构建推送消息内容
    equity_msg = f'账户净值： {new_equity_df.loc[0, "账户总净值"]:.2f}\n'
    equity_msg += f'账户： {account_config.name}\n'
    equity_msg += f'账户类型：{account_config.account_type}\n'
    if old_equity_df.empty:  # 如果是第一次运行，将之前的信息赋值为空
        old_all_equity = np.nan
        # old_long_pos = np.nan
        # old_short_pos = np.nan
    else:  # 不是第一次运行，则将历史数据用来比较
        old_all_equity = old_equity_df.loc[0, "账户总净值"]
        # old_long_pos = old_equity_df.loc[0, "多头仓位"]
        # old_short_pos = old_equity_df.loc[0, "空头仓位"]

    equity_msg += f'最近1小时盈亏：{(new_equity_df.loc[0, "账户总净值"] - old_all_equity):.2f}\n'  # 记录近一小时盈亏
    # equity_msg += f'多头最近1小时盈亏：{(new_equity_df.loc[0, "多头仓位"] - old_long_pos):.2f}\n'  # 记录多头最近1小时盈亏
    # equity_msg += f'空头最近1小时盈亏：{(new_equity_df.loc[0, "账户总净值"] - old_all_equity) - (new_equity_df.loc[0, "多头仓位"] - old_long_pos):.2f}\n\n'  # 记录空头最近1小时盈亏

    equity_msg += f'历史最高账户总净值：{max_all_equity}\n'  # 记录历史最高账户净值
    equity_msg += f'历史最低账户总净值：{min_all_equity}\n'  # 记录历史最低账户净值

    if full_hist_df is not None and not full_hist_df.empty:
        # =净值法（份额法）口径的全历史最高净值、当前回撤、峰值以来最大回撤
        # full_hist_df 由 draw_equity_and_send_pic(..., is_send=False) 对全部历史（含转入转出）跑一遍 net_fund 得到，
        # 净值/dd2here 都是expanding口径：peak_idx 之后 max2here 恒等于全局峰值，所以这段区间内 dd2here 的最小值
        # 就是"峰值以来最大回撤"，dd2here 的最后一个值就是"当前较峰值回撤"
        peak_idx = full_hist_df['净值'].idxmax()
        peak_time = full_hist_df.loc[peak_idx, 'time']
        peak_net_value = full_hist_df.loc[peak_idx, '净值']
        current_dd = full_hist_df.iloc[-1]['dd2here']
        max_dd_since_peak = full_hist_df.loc[peak_idx:, 'dd2here'].min()

        equity_msg += f'净值法历史最高净值：{peak_net_value:.4f}（发生于 {peak_time:%Y-%m-%d %H:%M}）\n'
        equity_msg += f'当前较历史最高净值回撤：{current_dd:.2f}%\n'
        equity_msg += f'历史最高净值到当前区间内的最大回撤：{max_dd_since_peak:.2f}%\n'

    # 记录多头仓位、多头现货、多头合约、空头仓位
    equity_msg += f'现货USDT余额：{spot_usdt}\n'  # 记录历史USDT净值
    equity_msg += f'多头仓位：{new_equity_df.loc[0, "多头仓位"]:.2f}（spot {new_equity_df.loc[0, "多头现货"]:.2f}, swap {new_equity_df.loc[0, "多头合约"]:.2f}）\n'
    equity_msg += f'空头仓位：{new_equity_df.loc[0, "空头仓位"]:.2f}\n'

    # ===计算净杠杆：净敞口（多头仓位 + 空头仓位，空头仓位本身已是负数，两者相加即多空对冲后的方向性敞口）
    # 除以账户总净值（保证金的近似口径，全仓/统一账户下净值即保证金基础），跟 account_config.leverage 这个"设定杠杆"是两个概念——
    # 这里算的是多空对冲后实际还剩多少方向性风险，不是仓位总规模
    net_exposure = new_equity_df.loc[0, "多头仓位"] + new_equity_df.loc[0, "空头仓位"]
    net_leverage = net_exposure / account_equity if account_equity else 0
    equity_msg += f'净杠杆（净敞口/账户总净值）：{net_leverage:.2f}\n'  # 记录净杠杆

    # ===计算账户保证金率
    # total_equity = swap_equity + swap_position['持仓盈亏'].sum()  # 计算当前账户总净值（含未实现盈亏）
    # pos_equity = (swap_position['当前标记价格'] * swap_position['当前持仓量']).abs().sum()  # 计算合约账户持仓价值
    # margin_rate = total_equity / pos_equity  # 计算当前账户保证金率
    # equity_msg += f'合约保证金率：{margin_rate:.2f}\n'  # 记录合约保证金率

    # ===计算当前的杠杆倍数
    equity_msg += f'杠杆：{account_config.leverage:.2f}\n'  # 记录杠杆倍数
    equity_msg += f'账户的UniMMR：{account_config.bn.get_unimmr():.2f}\n'  # 记录Unimmr

    # 保存净值文件
    equity_df.to_csv(equity_file_path, encoding='gbk', index=False)

    return equity_msg


def get_and_save_local_transfer(transfer_df, transfer_path):
    if os.path.exists(transfer_path):
        exist_transfer_df = pd.read_csv(transfer_path, encoding='gbk', parse_dates=['time'])
        transfer_df = pd.concat([exist_transfer_df, transfer_df], axis=0, copy=False)
        transfer_df = transfer_df.drop_duplicates(keep='first').reset_index(drop=True)
        transfer_df.to_csv(transfer_path, encoding='gbk', index=False)
    elif not transfer_df.empty:
        transfer_df.to_csv(transfer_path, encoding='gbk', index=False)

    return transfer_df


def net_fund(df):
    first_log_index = (df['type'] == 'log').idxmax()
    df = df.loc[first_log_index:, :]
    df.reset_index(inplace=True, drop=True)

    df.loc[0, '净值'] = 1
    df.loc[0, '份额'] = df.iloc[0]['账户总净值'] / df.iloc[0]['净值']
    df.loc[0, '当前总市值'] = df.iloc[0]['账户总净值']
    for i in range(1, len(df)):
        if df.iloc[i]['type'] == 'log':
            df.loc[i, '当前总市值'] = df.iloc[i]['账户总净值']
            df.loc[i, '份额'] = df.iloc[i - 1]['份额']
            df.loc[i, '净值'] = df.iloc[i]['当前总市值'] / df.loc[i]['份额']
        if df.iloc[i]['type'] == 'transfer':
            reduce_cnt = df.iloc[i]['账户总净值'] / df.iloc[i - 1]['净值']
            df.loc[i, '份额'] = df.loc[i - 1]['份额'] + reduce_cnt
            df.loc[i, '当前总市值'] = df.iloc[i]['账户总净值'] + df.iloc[i - 1]['当前总市值']
            df.loc[i, '净值'] = df.iloc[i]['当前总市值'] / df.iloc[i]['份额']

    return df


def format_timedelta(td):
    days = td.days
    hours = td.components.hours
    if days > 0:
        return f"{days}D{hours}H"
    else:
        return f"{hours}H"


def format_timing_signal(signal):
    match signal:
        case 1:
            return 'LONG'
        case -1:
            return 'SHORT'
        case 0:
            return 'EMPTY'
        case _:
            print('no known signal : ', signal)
            return 'NO KNOWN'


def send_fixed_ratio_info(me_conf: MultiEquityBacktestConfig, account_info: AccountConfig):
    # 整理择时数据
    all_timing_signal_list = []
    for conf in me_conf.factory.config_list:
        timing_path = conf.get_result_folder() / '再择时动态杠杆.csv'
        if not timing_path.exists():
            continue
        timing_df = pd.read_csv(timing_path, encoding='utf-8-sig', parse_dates=['candle_begin_time'],
                                usecols=['candle_begin_time', '动态杠杆'])
        timing_df['candle_begin_time'] = timing_df['candle_begin_time'].dt.tz_localize(None)
        last_row = timing_df.iloc[-1]
        last_signal_time = timing_df[timing_df['动态杠杆'] != timing_df['动态杠杆'].shift()]['candle_begin_time'].iloc[
            -1]
        all_timing_signal_list.append({
            'candle_begin_time': last_row['candle_begin_time'],
            'strategy': f'{conf.timing.name}-{conf.timing.params}',
            'signal': last_row['动态杠杆'],
            'status': format_timing_signal(last_row['动态杠杆']),  # 当前小时
            'last_hour_status': format_timing_signal(timing_df.iloc[-2]['动态杠杆']),  # 上个小时
            'status_change': last_row['动态杠杆'] != timing_df.iloc[-2]['动态杠杆'],  # 当小时状态是否切换
            'hour': format_timedelta(last_row['candle_begin_time'] - last_signal_time),  # 持续时间
        })

    if all_timing_signal_list:
        all_timing_signal = pd.DataFrame(all_timing_signal_list)

        # 发送择时因子图
        send_img_for_dataframe(all_timing_signal[['candle_begin_time', 'strategy', 'signal']],
                               account_info.wechat_webhook_url)

        # 发送择时状态图
        send_img_for_dataframe(
            all_timing_signal[['strategy', 'status', 'last_hour_status', 'status_change', 'hour']],
            account_info.wechat_webhook_url)


# 动态比较所有列对
def get_max_columns(row, cols):
    max_cols = []
    max_val = row[cols[0]]
    for col in cols:
        if row[col] > max_val:
            max_val = row[col]
            max_cols = [col]
        elif row[col] == max_val:
            max_cols.append(col)
    return ", ".join(max_cols)


def send_rotation_info(me_conf: MultiEquityBacktestConfig, account_info: AccountConfig):
    # 整理轮动数据
    all_rotation_df_list = []
    all_rotation_signal_list = []
    idx_to_conf_dict = {}
    start_time = None

    for idx, conf in enumerate(me_conf.factory.config_list):
        file_path = conf.get_result_folder() / 'equity_df.pkl'
        if not file_path.exists():
            continue
        factor_df = pd.read_pickle(file_path)
        factor_df['candle_begin_time'] = factor_df['candle_begin_time'].dt.tz_localize(None)
        last_row = factor_df.iloc[-1]
        all_rotation_signal_list.append({
            'candle_begin_time': last_row['candle_begin_time'],
            'strategy': conf.name,
            'has_timing': None if conf.timing is None else conf.timing.name,
            **{col: last_row[col] for col in
               sorted(me_conf.strategy.factor_columns, key=lambda x: int(x.split('_')[1]))}
        })
        if start_time is None or factor_df.iloc[0]['candle_begin_time'] > start_time:
            start_time = factor_df.iloc[0]['candle_begin_time']
        all_rotation_df_list.append(factor_df)
        # idx_to_conf_dict[idx] = conf.strategy_type  # 暂时留着，不做清理
        idx_to_conf_dict[idx] = conf.name  # 状态图显示中文，需要安装字体

    # 需要对齐所有资金曲线数据的长度
    for idx, df in enumerate(all_rotation_df_list):
        all_rotation_df_list[idx] = df[df['candle_begin_time'] >= start_time]

    all_rotation_status_list = []
    if all_rotation_df_list:
        for factor_info in me_conf.strategy.params['factor_list']:
            if 'cal_one_ratio' not in me_conf.strategy.core_funcs:
                continue
            _rotation_df = me_conf.strategy.core_funcs['cal_one_ratio'](factor_info, all_rotation_df_list)
            _rotation_df.rename(columns=idx_to_conf_dict, inplace=True)
            # _rotation_df['status'] = _rotation_df.idxmax(axis=1)
            _rotation_df['status'] = _rotation_df.apply(get_max_columns, axis=1, cols=_rotation_df.columns.tolist())
            last_signal_time = _rotation_df[_rotation_df['status'] != _rotation_df['status'].shift()].index[-1]
            all_rotation_status_list.append({
                'factor': f'{factor_info[0]}_{factor_info[2]}',
                'status': _rotation_df.iloc[-1]['status'],
                'last_hour_status': _rotation_df.iloc[-2]['status'],
                'status_change': _rotation_df.iloc[-1]['status'] != _rotation_df.iloc[-2]['status'],
                'hour': format_timedelta(_rotation_df.index[-1] - last_signal_time),
            })

    if all_rotation_signal_list:
        # 发送轮动因子图
        send_img_for_dataframe(pd.DataFrame(all_rotation_signal_list), account_info.wechat_webhook_url)

    if all_rotation_status_list:
        # 发送轮动状态图
        send_img_for_dataframe(pd.DataFrame(all_rotation_status_list), account_info.wechat_webhook_url)


def send_pos_strategy_info(me_conf: MultiEquityBacktestConfig, account_info: AccountConfig):
    if me_conf.strategy.name not in ['FixedRatioStrategy', 'FixedRatioStrategyFill', 'FixedRatioStrategyFill_V2']:
        send_rotation_info(me_conf, account_info)

    send_fixed_ratio_info(me_conf, account_info)


def calculate_status(row):
    # 找出大于 0 的列
    positive_columns = row[row > 0].index.tolist()
    if len(positive_columns) == 1:
        return positive_columns[0]  # 返回列名
    else:
        return 'Mix'  # 多列大于 0 或没有列大于 0


def run():
    import sys
    if len(sys.argv) > 1:
        timestamp = sys.argv[1]
        account_param = sys.argv[2] if len(sys.argv) > 2 else None
        run_time = datetime.fromtimestamp(int(timestamp))
    else:
        run_time = None
        account_param = None
    print(run_time)
    # =====刷新一下与交易所的时差
    refresh_diff_time()
    # =设置一下默认时间，用于获取订单时截取订单数据
    default_time = '2023-09-28 00:00:00'

    # =加载现货交易对信息
    dummy_bn_cli = BinanceClient.get_dummy_client()
    market_info = dummy_bn_cli.get_market_info('spot')
    spot_symbol_list = market_info['symbol_list']

    for account_profile_file in load_multi_accounts():
        # 初始化多策略回测
        account_info, me_conf = init_system(account_profile_file.stem)
        account_info.update_account_info()
        account_name = account_info.name
        if account_param is not None and account_name != account_param:
            print(f'当前账号【{account_name}】，不是指定的账号 【{account_param}】，跳过')
            continue
        # ===获取账号的配置
        account_overview = account_info.bn.get_account_overview()
        account_equity = account_overview['account_equity']
        try:
            swap_position = account_overview['swap_assets']['swap_position_df']
            swap_equity = account_info.swap_equity
            if account_info.use_spot:
                seed_coin_spot_equity = account_overview['seed_coin_spot_equity']
                spot_position = account_overview['spot_assets']['spot_position_df']
                spot_equity = account_overview['spot_assets']['equity']
                spot_usdt = account_overview['spot_assets']['usdt']
            else:
                seed_coin_spot_equity = 0
                spot_position = pd.DataFrame()
                spot_equity = 0
                spot_usdt = 0
        except BaseException as e:
            print(e)
            print(traceback.format_exc())
            print(f'当前账号【{account_name}】，获取数据失败')
            continue

        # =====获取当前运行小时
        if run_time is None:
            run_time = datetime.now().replace(minute=account_info.hour_offset_minute, second=0, microsecond=0)
            # 仅仅在run time是none时，进行判断
            if not (datetime.now() - pd.to_timedelta('5m') <= run_time <= datetime.now() + pd.to_timedelta('5m')):
                print(f'当前账号【{account_name}】未到时间统计')
                continue
        # 以下代码可以测试的时候使用
        # run_time = datetime.strptime('2025-11-12 10:00:00', "%Y-%m-%d %H:%M:%S")
        if run_time.minute != account_info.hour_offset_minute:
            print(f'当前账号【{account_name}】未到时间统计, run_time: {run_time}, hour_offset: {account_info.hour_offset_minute}')
            continue

        # 获取仓位信息
        position_df = me_conf.factory.result_folder / '仓位比例.csv'
        if position_df.exists():
            position_df = pd.read_csv(position_df, index_col=0)
            position_df.index.name = 'time'
            last_pos = position_df.iloc[-1]
            content = ''
            for idx in last_pos.index.to_list():
                content += f"{strategy_pool[int(idx)].get('name', f'策略{idx}')}: {round(last_pos[idx] * 100, 4)}%\n"
            rotation_msg = f"【子策略状态】\n\n{content}\n"
            # send_wechat_work_msg(rotation_msg, account_info.wechat_webhook_url)  # 已禁用: 子策略状态消息

        # 发送仓位管理统计数据
        send_pos_strategy_info(me_conf, account_info)

        # ===生成账户净值信息
        equity_msg = save_and_send_equity_info(account_info, me_conf, spot_position, swap_position, spot_equity, account_equity, spot_usdt, seed_coin_spot_equity)
        # =发送账户净值信息
        send_wechat_work_msg(equity_msg, account_info.wechat_webhook_url)

        # =获取一下现货各个币种的最新价格
        spot_last_price = account_info.bn.get_spot_ticker_price_series()

        # =====每小时交易的订单监测、生成历史持仓信息(只保留当前持仓的文件，历史交易过的且已经清仓的不会保留文件)
        if account_info.use_spot:
            # ===获取订单的详细信息、以及生成各币种的持仓信息

            # ==new读取账户的换仓信息
            try:
                filename = run_time.strftime("%Y%m%d_%H") + ".csv"
                select_symbol_list_path = get_file_path(data_path, account_name, '账户换仓信息', filename)
                select_symbol = pd.read_csv(select_symbol_list_path, encoding='gbk')
                select_symbol_list = select_symbol.loc[select_symbol['symbol_type'] == 'spot', 'symbol'].tolist()
                all_spot_order_info = get_all_order_info(
                    account_info, sorted(select_symbol_list), run_time, default_time, spot_last_price)
            except Exception as e:
                ignore_error(e)
                print('读取账户换仓信息失败，准备获取全部订单信息')
                all_spot_order_info = get_all_order_info(
                    account_info, sorted(spot_symbol_list), run_time, default_time, spot_last_price)

            # 判断当前小时是否存在订单信息
            if all_spot_order_info.empty:
                print('该时间不存在订单，不发送订单监测信息...')
            else:
                # =整理订单数据
                all_spot_order_info = all_spot_order_info.sort_values(['方向', 'time', 'symbol']).reset_index(drop=True)
                # =将订单数据拆分为买入和卖出
                buyer = all_spot_order_info[all_spot_order_info['方向'] == 1].reset_index(drop=True)
                seller = all_spot_order_info[all_spot_order_info['方向'] == -1].reset_index(drop=True)

                # =生成该小时的统计数据，一共两行，一行为买入，一行为卖出
                stats_info = get_stats_info(buyer, seller, spot_equity)

                # =获取发送的订单监测信息
                order_msg, bs_msg = get_order_msg(run_time, all_spot_order_info, stats_info, buyer, seller)

                # =保存订单监测信息
                save_order_info(all_spot_order_info, stats_info, run_time, account_name)

                # =发送订单监测信息
                if order_msg:
                    send_wechat_work_msg(order_msg, account_info.wechat_webhook_url)
                # =发送买卖币种信息
                if bs_msg:
                    send_wechat_work_msg(bs_msg, account_info.wechat_webhook_url)

                # =清理数据
                del buyer, seller, stats_info, order_msg, bs_msg

        # ===发送各币种持仓信息
        # send_position_result(account_info, spot_position, swap_position, spot_last_price)  # 已禁用: 持仓快照图片

        # =====计算并发送持仓盈亏报告
        # try:  # 已禁用: 持仓盈亏报告
        #     calculate_and_send_pnl_report(account_info, spot_position, swap_position, spot_last_price)
        # except Exception as e:
        #     print(f"计算持仓盈亏失败: {traceback.format_exc()}")

        # 休息一会儿，避免超权重
        time.sleep(60)


if __name__ == '__main__':
    try:
        run()
    except Exception as err:
        msg = '保3下单统计脚本出错，出错原因: ' + str(err)
        print(msg)
        print(traceback.format_exc())
        send_wechat_work_msg(msg, error_webhook_url)
