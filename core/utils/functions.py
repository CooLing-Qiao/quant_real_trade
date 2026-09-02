"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""

import os
import time
import traceback
import warnings
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

from config import stable_symbol
from core.binance.base_client import BinanceClient
from core.model.account_config import AccountConfig
from core.model.backtest_config import BacktestConfig, MultiEquityBacktestConfig
from core.utils.datatools import load_bmac_data
from core.utils.dingding import send_wechat_work_msg
from core.utils.log_kit import logger
from core.utils.path_kit import get_file_path, get_folder_path

warnings.filterwarnings('ignore')

data_path = get_folder_path('data', as_path_type=True)
# =====策略相关函数
def del_insufficient_data(symbol_candle_data) -> Dict[str, pd.DataFrame]:
    """
    删除数据长度不足的币种信息

    :param symbol_candle_data:
    :return
    """
    # ===删除成交量为0的线数据、k线数不足的币种
    symbol_list = list(symbol_candle_data.keys())
    for symbol in symbol_list:
        # 删除空的数据
        if symbol_candle_data[symbol] is None or symbol_candle_data[symbol].empty:
            del symbol_candle_data[symbol]
            continue
        # 删除该币种成交量=0的k线
        # symbol_candle_data[symbol] = symbol_candle_data[symbol][symbol_candle_data[symbol]['volume'] > 0]

    return symbol_candle_data


def save_final_select_results(run_time, me_conf: MultiEquityBacktestConfig, account_name, max_file_limit=168):
    ratios = pd.read_csv(me_conf.factory.result_folder / '仓位比例.csv', index_col=0)
    df_list = []
    for idx, config in enumerate(me_conf.factory.config_list):
        _file_path = config.get_result_folder() / '最新持仓周期选币结果.pkl'
        if not _file_path.exists():
            continue
        df = pd.read_pickle(_file_path)
        if df.empty:
            continue
        df['target_alloc_ratio'] *= ratios[str(idx)].iloc[-1]
        df_list.append(df)

    if not df_list:
        return
    select_coin_df = pd.concat(df_list, ignore_index=True, copy=False)
    select_coin_df = select_coin_df[select_coin_df['target_alloc_ratio'] != 0]
    select_coin_df.sort_values(by=['candle_begin_time', 'strategy'], inplace=True)

    # 获取存储文件位置
    dir_path = Path(data_path) / account_name / 'select_coin'
    dir_path.mkdir(parents=True, exist_ok=True)

    # 生成文件名
    file_path = dir_path / f"{run_time.strftime('%Y-%m-%d_%H')}.pkl"
    select_coin_df.to_pickle(file_path)
    # 删除多余的文件
    del_hist_files(dir_path, max_file_limit, file_suffix='.pkl')


def save_position_results(position_results, run_time, account_name, max_file_limit=168):
    """
    保存目标仓位数据，最多保留999份文件
    :param position_results: 保存文件内容
    :param run_time: 当前运行时间
    :param account_name: 账户名称
    :param max_file_limit: 最大限制
    """
    # 获取存储文件位置
    dir_path = Path(data_path) / account_name / 'position_results'
    dir_path.mkdir(parents=True, exist_ok=True)

    # 生成文件名
    file_path = dir_path / f"{run_time.strftime('%Y-%m-%d_%H')}.pkl"
    # 保存文件
    position_results.to_pickle(file_path)
    # 删除多余的文件
    del_hist_files(dir_path, max_file_limit, file_suffix='.pkl')


def del_hist_files(file_path, max_file_limit=999, file_suffix='.pkl'):
    """
    删除多余的文件，最限制max_file_limit
    :param file_path: 文件路径
    :param max_file_limit: 最大限制
    :param file_suffix: 文件后缀
    """
    # ===删除多余的flag文件
    files = [_ for _ in os.listdir(file_path) if _.endswith(file_suffix)]  # 获取file_path目录下所有以.pkl结尾的文件
    # 判断一下当前目录下文件是否过多
    if len(files) > max_file_limit:  # 文件数量超过最大文件数量限制，保留近999个文件，之前的文件全部删除
        logger.info(f'目前文件数量: {len(files)}, 文件超过最大限制: {max_file_limit}，准备删除文件')
        # 文件名称是时间命名的，所以这里倒序排序结果，距离今天时间越近的排在前面，距离距离今天时间越远的排在最后。例：[2023-04-02_08, 2023-04-02_07, 2023-04-02_06···]
        files = sorted(files, reverse=True)
        rm_files = files[max_file_limit:]  # 获取需要删除的文件列表

        # 遍历删除文件
        for _ in rm_files:
            os.remove(os.path.join(file_path, _))  # 删除文件
            logger.ok(f'删除文件完成:{os.path.join(file_path, _)}')


def create_finish_flag(flag_path, run_time, signal):
    """
    创建数据更新成功的标记文件
    如果标记文件过多，会删除7天之前的数据

    :param flag_path:标记文件存放的路径
    :param run_time: 当前的运行是时间
    :param signal: 信号
    """
    # ===判断数据是否完成
    if signal > 0:
        logger.warning(f'当前数据更新出现错误信号: {signal}，数据更新没有完成，当前小时不生成 flag 文件')
        return

    # ===生成flag文件
    # 指定生成文件名称
    index_config_path = os.path.join(flag_path,
                                     f"{run_time.strftime('%Y-%m-%d_%H_%M')}.flag")  # 例如文件名是：2023-04-02_08.flag
    # 更新信息成功，生成文件
    with open(index_config_path, 'w', encoding='utf-8') as f:
        f.write('更新完成')
        f.close()

    # ===删除多余的flag文件
    del_hist_files(flag_path, 7 * 24, file_suffix='.flag')


def update_accounts_info(accounts_config_dict: dict, is_only_spot_account=False, is_operate=True):
    """
    获取账户信息，并且自动筛选剔除掉无效账户
    :param accounts_config_dict:            config中配置的账户
    :param is_only_spot_account:    是否只保留现货交易的账户
    :param is_operate:              是否进行账户的调整操作
    :return:
    """
    # 从账户列表中拿到所有的账户名称
    # 注意点：直接使用account_list.keys()去遍历，del其中任一账户都会报错，使用list暂存一下不会出现问题
    account_name_list = list(accounts_config_dict.keys())
    usable_account_config_dict = {}

    # 遍历账号，获取每个账号下的净值和持仓
    for account_name in account_name_list:
        logger.info(f'# 查询 {account_name} 的净值和持仓...')
        try:
            # 更新当前账户净值和持仓
            account_config: AccountConfig = accounts_config_dict[account_name]
            response = account_config.update_account_info(is_only_spot_account, is_operate)

            if response is None:
                logger.warning(f'账户: {account_name}，资金不足，移除账户配置')

            if account_config.is_usable:
                usable_account_config_dict[account_name] = account_config

            # ===休息一下
            time.sleep(2)
        except KeyboardInterrupt:
            logger.critical('手动终止，退出')
            exit()
        except BaseException as e:
            msg = f'账户: {account_name}，更新账户信息出现问题，移除账户配置'
            logger.error(msg)
            logger.debug(e)
            logger.debug(traceback.format_exc())
            send_wechat_work_msg(msg, accounts_config_dict[account_name].wechat_webhook_url)

    logger.ok(f'所有账户更新完成，共计 {len(usable_account_config_dict.keys())} 个可用账户')
    return usable_account_config_dict


def refresh_diff_time():
    """刷新本地电脑与交易所的时差"""
    cli = BinanceClient.get_dummy_client()
    server_time = cli.exchange.fetch_time()  # 获取交易所时间
    diff_timestamp = int(time.time() * 1000) - server_time  # 计算时差
    BinanceClient.diff_timestamp = diff_timestamp  # 更新到全局变量中


def save_symbol_order(symbol_order, run_time, account_name):
    # 创建存储账户换仓信息文件的目录[为了计算账户小时成交量信息生成的]
    dir_path = Path(data_path)
    dir_path = dir_path / account_name / '账户换仓信息'
    dir_path.mkdir(exist_ok=True)

    filename = run_time.strftime("%Y%m%d_%H") + ".csv"
    select_symbol_list_path = dir_path / filename
    select_symbol_list = symbol_order[['symbol', 'symbol_type']].copy()
    select_symbol_list['time'] = run_time

    select_symbol_list.to_csv(select_symbol_list_path)
    del_hist_files(dir_path, 999, file_suffix='.csv')


def ignore_error(anything):
    return anything


def load_min_qty(file_path: Path) -> (int, Dict[str, int]):
    # 读取min_qty文件并转为dict格式
    min_qty_df = pd.read_csv(file_path, encoding='utf-8-sig')
    min_qty_df['最小下单量'] = -np.log10(min_qty_df['最小下单量']).round().astype(int)
    default_min_qty = min_qty_df['最小下单量'].max()
    min_qty_df.set_index('币种', inplace=True)
    min_qty_dict = min_qty_df['最小下单量'].to_dict()

    return default_min_qty, min_qty_dict


def is_trade_symbol(symbol, black_list, white_list) -> bool:
    """
    过滤掉不能用于交易的币种，比如稳定币、非USDT交易对，以及一些杠杆币
    :param symbol: 交易对
    :param black_list: 黑名单
    :param white_list: 白名单
    :return: 是否可以进入交易，True可以参与选币，False不参与
    """
    symbol = symbol.upper().replace('-USDT', 'USDT')
    if white_list:
        if symbol in white_list:
            return True
        else:
            return False

    # 稳定币和黑名单币不参与
    if not symbol or not symbol.endswith('USDT') or symbol in black_list:
        return False

    # 筛选杠杆币
    base_symbol = symbol[:-4]
    if (
            base_symbol.endswith(("UP", "DOWN", "BEAR", "BULL"))
            and base_symbol != "JUP"
            and base_symbol != "SYRUP"
            or base_symbol in stable_symbol
    ):
        return False
    else:
        return True


def load_realtime_data(conf: BacktestConfig, run_time):
    all_candle_df_list = []
    all_symbol_list = set()
    # chunk 机制先硬编码处理一下
    if not {'swap', 'mix'}.isdisjoint(conf.select_scope_set) or not {'swap'}.isdisjoint(conf.order_first_set):
        symbol_swap_candle_data = load_bmac_data('swap', run_time, conf)
        symbol_swap_candle_data = {
            k: v for k, v in symbol_swap_candle_data.items()
            if is_trade_symbol(k, conf.black_list, conf.white_list)
        }
        all_candle_df_list = all_candle_df_list + list(del_insufficient_data(symbol_swap_candle_data).values())
        all_symbol_list = all_symbol_list | set(symbol_swap_candle_data.keys())
        del symbol_swap_candle_data

    if not {'spot', 'mix'}.isdisjoint(conf.select_scope_set):
        symbol_spot_candle_data = load_bmac_data('spot', run_time, conf)
        symbol_spot_candle_data = {
            k: v for k, v in symbol_spot_candle_data.items()
            if is_trade_symbol(k, conf.black_list, conf.white_list)
        }
        all_candle_df_list = all_candle_df_list + list(del_insufficient_data(symbol_spot_candle_data).values())
        all_symbol_list = all_symbol_list | set(symbol_spot_candle_data.keys())
        del symbol_spot_candle_data

    pkl_path = get_file_path('data', 'cache', 'all_candle_df_list.pkl')
    pd.to_pickle(all_candle_df_list, pkl_path)

    del all_candle_df_list

    return tuple(all_symbol_list)


def save_performance_df_csv(conf: BacktestConfig, **kwargs):
    for name, df in kwargs.items():
        file_path = conf.get_result_folder() / f'{name}.csv'
        df.to_csv(file_path, encoding='utf-8-sig')
