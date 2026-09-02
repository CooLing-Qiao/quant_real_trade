"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import warnings

import numpy as np
import pandas as pd

from config import runtime_folder
from core.model.account_config import AccountConfig
from core.utils.log_kit import logger

warnings.filterwarnings('ignore')
# pandas相关的显示设置，基础课程都有介绍
pd.set_option('display.max_rows', 1000)
pd.set_option('expand_frame_repr', False)  # 当列太多时不换行
pd.set_option('display.unicode.ambiguous_as_wide', True)  # 设置命令行输出时的列对齐功能
pd.set_option('display.unicode.east_asian_width', True)


def get_lot_sizes(min_qty_dict, symbols):
    """
    读取每个币种的最小下单量
    :param min_qty_dict: 每个币种的最小下单量
    :param symbols:  币种列表
    :return:
    """
    lot_sizes = 0.1 ** pd.Series(min_qty_dict)
    lot_sizes = lot_sizes.reindex(symbols)
    return lot_sizes


def get_pos_series(df_pos: pd.DataFrame):
    if df_pos.empty:
        return pd.Series()

    if 'symbol' in df_pos.columns:
        df_pos = df_pos.set_index('symbol')

    return df_pos['当前持仓量'].copy()


def calc_target_position(account_config: AccountConfig, is_debug=False):
    if is_debug is False:
        # 获取当前账户仓位
        account_config.update_account_info()

    current_spot_pos: pd.Series = get_pos_series(account_config.spot_position)
    current_swap_pos: pd.Series = get_pos_series(account_config.swap_position)

    # 取最新的权重为目标权重
    df_spot_ratio: pd.DataFrame = pd.read_pickle(runtime_folder / 'df_spot_ratio.pkl')
    df_swap_ratio: pd.DataFrame = pd.read_pickle(runtime_folder / 'df_swap_ratio.pkl')
    target_spot_ratio = df_spot_ratio.iloc[-1]
    target_swap_ratio = df_swap_ratio.iloc[-1]

    # 有效币种 = 当前可以交易的(目标持仓币种 + 当前持仓币种)
    spot_symbols = set(target_spot_ratio.index.to_list() + current_spot_pos.index.to_list())
    spot_symbols = spot_symbols.intersection(account_config.bn.market_info.get('spot', {}).get('symbol_list', []))
    spot_symbols = sorted(spot_symbols)

    swap_symbols = set(target_swap_ratio.index.to_list() + current_swap_pos.index.to_list())
    swap_symbols = swap_symbols.intersection(account_config.bn.market_info.get('swap', {}).get('symbol_list', []))
    swap_symbols = sorted(swap_symbols)

    # 获取有效币种最小下单量
    spot_lot_sizes = get_lot_sizes(account_config.bn.market_info.get('spot', {}).get('min_qty', {}), spot_symbols)
    swap_lot_sizes = get_lot_sizes(account_config.bn.market_info.get('swap', {}).get('min_qty', {}), swap_symbols)

    # 生成 rebalance 仓位映射算法
    pos_calc = account_config.rebalance_mode.create(spot_lot_sizes.to_numpy(), swap_lot_sizes.to_numpy())

    # 计算杠杆后权益
    leverage = account_config.leverage
    all_equity = (account_config.spot_equity + account_config.swap_equity) / 1.001  # 扣除掉部分手续费
    logger.info(f'杠杆前权益: {all_equity:.2f} USDT')
    # 没有合约 或 合约整体处理多头，根据资金配置，预留部分资金
    if spot_symbols and (not swap_symbols or target_swap_ratio.sum() >= 0):
        # 购买 bnb 抵扣手续费总资金比例的高于 0.1%，降低 5% 资金
        if account_config.buy_bnb_value / all_equity > 0.001:
            leverage = leverage * 0.95
        else:  # 购买 bnb 抵扣手续费总资金比例的低于 0.1%，降低 3% 资金
            leverage = leverage * 0.97
    equity_leveraged = all_equity * leverage
    logger.info(f'杠杆倍数: {leverage:.2f}')
    logger.info(f'杠杆后权益: {equity_leveraged:.2f} USDT')

    # 获取有效币种最新价格
    spot_price = account_config.bn.get_spot_ticker_price_series()
    swap_price = account_config.bn.get_swap_ticker_price_series()
    spot_price = spot_price.reindex(spot_symbols)
    swap_price = swap_price.reindex(swap_symbols)

    # 扩展当前仓位 Series index 为全部有效币种，无持仓的用 0 填充
    current_spot_pos = current_spot_pos.reindex(spot_symbols).fillna(0)
    current_swap_pos = current_swap_pos.reindex(swap_symbols).fillna(0)

    # 当前手数 = round(当前仓位 / 每手币数)
    current_spot_lots = (current_spot_pos / spot_lot_sizes).round().astype(np.int64)
    current_swap_lots = (current_swap_pos / swap_lot_sizes).round().astype(np.int64)

    # 扩展目标持仓权重 Series index 为全部有效币种，无权重的用 0 填充
    target_spot_ratio = target_spot_ratio.reindex(spot_symbols).fillna(0)
    target_swap_ratio = target_swap_ratio.reindex(swap_symbols).fillna(0)

    # 计算现货和合约目标手数
    target_lots_spot, target_lots_swap = pos_calc.calc_lots(equity_leveraged, spot_price.to_numpy(),
                                                            current_spot_lots.to_numpy(), target_spot_ratio.to_numpy(),
                                                            swap_price.to_numpy(), current_swap_lots.to_numpy(),
                                                            target_swap_ratio.to_numpy())

    # 生成目标仓位 DF
    cols = ['symbol', 'symbol_type', 'price', 'target_lots', 'lot_size']
    df_spot_pos = pd.DataFrame({
        'symbol': spot_symbols,
        'symbol_type': 'spot',
        'price': spot_price,
        'target_lots': target_lots_spot,
        'lot_size': spot_lot_sizes
    }) if spot_symbols else pd.DataFrame(columns=cols)

    df_swap_pos = pd.DataFrame({
        'symbol': swap_symbols,
        'symbol_type': 'swap',
        'price': swap_price,
        'target_lots': target_lots_swap,
        'lot_size': swap_lot_sizes
    }) if swap_symbols else pd.DataFrame(columns=cols)

    position_results = pd.concat([df_spot_pos, df_swap_pos], ignore_index=True, copy=False)
    position_results['目标持仓量'] = position_results['target_lots'] * position_results['lot_size']
    return position_results
