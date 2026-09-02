"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import time
from datetime import datetime

import numpy as np
import pandas as pd

from config import bmac_exginfo_path, runtime_folder
from core.model.backtest_config import BacktestConfig
from core.simulator import Simulator
from core.utils.datatools import check_bmac_pivot_flag, read_bmac_market_pivot
from core.utils.log_kit import logger

pd.set_option('display.max_rows', 1000)
pd.set_option('expand_frame_repr', False)  # 当列太多时不换行


def merge_state(df, state: dict, pivot_dict: dict, df_exginfo: pd.DataFrame):
    columns = state['columns']
    sim: Simulator = state['sim']
    # 合并列并排序
    final_columns = sorted(set(columns) | set(df.columns))  # 在这里使用sorted确保列按照字母顺序排序

    # 为lots补齐新列对应的值
    original_map = {col: i for i, col in enumerate(columns)}
    sim.lots = np.array([sim.lots[original_map[c]] if c in original_map else 0 for c in final_columns])
    sim.target_lots = np.array([sim.target_lots[original_map[c]] if c in original_map else 0 for c in final_columns])

    # 对df进行reindex
    df = df.reindex(columns=final_columns, fill_value=0)

    # 对齐行情数据(如果出现下架币，会出现数据缺失的情况，这里进行对齐)
    # 填充缺失的行情数据
    df_exginfo = df_exginfo.reindex(index=final_columns, fill_value=0)
    # 填充缺失的币种列
    for key in pivot_dict.keys():
        pivot_dict[key] = pivot_dict[key].reindex(columns=final_columns, fill_value=0)

    return df, pivot_dict, df_exginfo, sim


def create_ratio_snapshot(conf: BacktestConfig, ratio_df: pd.DataFrame, symbol_type):
    ratio_snapshot = conf.get_snapshot_folder() / f'ratio_{symbol_type}.pkl'
    if ratio_snapshot.exists():
        # 缓存我们最终需要的完整columns
        final_columns = ratio_df.columns

        # 和本地数据拼接
        ratio_df_0 = pd.read_pickle(ratio_snapshot)
        ratio_df_1 = ratio_df[ratio_df.index > ratio_df_0.index.max()]
        ratio_df = pd.concat([ratio_df_0, ratio_df_1], sort=False, copy=False)

        # 当增量计算的时候，拼接会导致columns的位置错位，影响策略最终的仓位映射，必须reindex对齐
        ratio_df = ratio_df.reindex(columns=final_columns, fill_value=0)

        # logger.debug(f'[{symbol_type}]原长度：{len(ratio_df_0)}, 增长：{len(ratio_df_1)},合并后长度：{len(ratio_df)}')
        # logger.debug(
        #     f'[{symbol_type}]时间0：{ratio_df_0.index.max()}，时间1：{ratio_df_1.index.max()}，最新时间：{ratio_df.index.max()}')
    ratio_df.to_pickle(ratio_snapshot)
    return ratio_df


def calc_equity(conf: BacktestConfig, run_time, leverage: float | pd.Series = None, state_name: str = None, account_name: str = ''):
    """
    计算回测结果的函数
    :param conf: Account 配置
    :param run_time: 运行时间
    :param leverage: 杠杆
    :param state_name: 模拟交易的状态
    :param account_name: 用户名
    """
    # ====================================================================================================
    # 1. 读入下单资金占比数据
    # ====================================================================================================
    if account_name:
        df_spot_ratio = pd.read_pickle(runtime_folder / 'df_spot_ratio.pkl')
        df_swap_ratio = pd.read_pickle(runtime_folder / 'df_swap_ratio.pkl')
    else:
        df_spot_ratio = pd.read_pickle(conf.get_result_folder() / 'df_spot_ratio.pkl')
        df_swap_ratio = pd.read_pickle(conf.get_result_folder() / 'df_swap_ratio.pkl')

        df_spot_ratio.index = df_spot_ratio.index.tz_localize('UTC')
        df_swap_ratio.index = df_swap_ratio.index.tz_localize('UTC')

    # ====================================================================================================
    # 2. 等待实盘数据就绪，并读入数据
    # ====================================================================================================
    # is_pivot_ready = check_bmac_pivot_flag(run_time)
    # if not is_pivot_ready:
    #     logger.warning(f'Pivot data 未就绪')
    #     return

    hour_offset = f'{run_time.minute}m'
    pivot_dict_spot = read_bmac_market_pivot(hour_offset, 'spot')
    pivot_dict_swap = read_bmac_market_pivot(hour_offset, 'swap')
    # pivot_dict_spot = pd.read_pickle(bmac_data_path / hour_offset / 'market_pivot_spot.pkl')
    # pivot_dict_swap = pd.read_pickle(bmac_data_path / hour_offset / 'market_pivot_swap.pkl')

    df_exginfo_spot = pd.read_pickle(bmac_exginfo_path / 'exginfo_spot.pkl').set_index('symbol')
    df_exginfo_swap = pd.read_pickle(bmac_exginfo_path / 'exginfo_swap.pkl').set_index('symbol')

    # ====================================================================================================
    # 3. 根据缓存的状态，恢复模拟交易的状态
    # ====================================================================================================
    if state_name:
        state_path = conf.get_snapshot_folder() / f'lots_{state_name}.pkl'
        has_snapshot = state_path.exists()
    else:
        state_path = None
        has_snapshot = False

    if has_snapshot:
        state = pd.read_pickle(state_path)
        # 合并新老选币
        df_spot_ratio, pivot_dict_spot, df_exginfo_spot, sim_spot = merge_state(df_spot_ratio, state['spot'],
                                                                                pivot_dict_spot, df_exginfo_spot)
        df_swap_ratio, pivot_dict_swap, df_exginfo_swap, sim_swap = merge_state(df_swap_ratio, state['swap'],
                                                                                pivot_dict_swap, df_exginfo_swap)

        # 按照上次模拟时间裁切
        simulate_to = state['simulate_to']
        df_spot_ratio = df_spot_ratio[df_spot_ratio.index > simulate_to]
        df_swap_ratio = df_swap_ratio[df_swap_ratio.index > simulate_to]
    else:
        state = {}
        sim_spot = None
        sim_swap = None

    if len(df_spot_ratio) != len(df_swap_ratio) or np.any(df_swap_ratio.index != df_spot_ratio.index):
        logger.warning(f'数据长度不一致，现货数据长度：{len(df_spot_ratio)}, 永续合约数据长度：{len(df_swap_ratio)}')
        return

    # 获取现货和永续合约的币种，并且排序
    spot_symbols = sorted(set(df_spot_ratio.columns).intersection(df_exginfo_spot.index))
    swap_symbols = sorted(set(df_swap_ratio.columns).intersection(df_exginfo_swap.index))

    df_exginfo_spot = df_exginfo_spot.loc[spot_symbols]
    df_exginfo_swap = df_exginfo_swap.loc[swap_symbols]

    df_spot_ratio = df_spot_ratio[spot_symbols]
    df_swap_ratio = df_swap_ratio[swap_symbols]

    # 读入最小下单量数据
    spot_lot_sizes = df_exginfo_spot['lot_size'].astype(float)
    swap_lot_sizes = df_exginfo_swap['lot_size'].astype(float)

    if not has_snapshot:
        start_lots_spot = np.zeros(len(df_spot_ratio.columns))
        start_lots_swap = np.zeros(len(df_swap_ratio.columns))
        sim_spot = Simulator(conf.initial_usdt, spot_lot_sizes, conf.spot_c_rate, start_lots_spot,
                             conf.spot_min_order_limit)
        sim_swap = Simulator(0, swap_lot_sizes, conf.swap_c_rate, start_lots_swap, conf.swap_min_order_limit)
    else:
        sim_spot.lot_sizes = spot_lot_sizes
        sim_swap.lot_sizes = swap_lot_sizes
        last_prices_spot = pivot_dict_spot['close'].loc[state['simulate_to'], spot_symbols].to_numpy()
        last_prices_swap = pivot_dict_swap['close'].loc[state['simulate_to'], swap_symbols].to_numpy()
        sim_spot.last_prices = last_prices_spot
        sim_swap.last_prices = last_prices_swap

    # 开始时间列
    candle_begin_times = df_spot_ratio.index.to_series().reset_index(drop=True)

    # 裁切现货数据，保证open，close，vwap1m，对应的df中，现货币种、时间长度一致
    pivot_dict_spot = align_pivot_dimensions(pivot_dict_spot, spot_symbols, candle_begin_times)

    # 裁切合约数据，保证open，close，vwap1m，funding_fee对应的df中，合约币种、时间长度一致
    pivot_dict_swap = align_pivot_dimensions(pivot_dict_swap, swap_symbols, candle_begin_times)

    pos_calc = conf.rebalance_mode.create(spot_lot_sizes.to_numpy(), swap_lot_sizes.to_numpy())

    # 确定rebalance接入的时间点
    if conf.is_day_period:
        require_rebalance = np.where(candle_begin_times.dt.hour == 23, 1, 0).astype(np.int8)
    else:
        require_rebalance = np.ones(len(candle_begin_times), dtype=np.int8)

    if leverage is None:
        leverage = conf.leverage

    if isinstance(leverage, pd.Series):
        leverages = leverage.to_numpy(dtype=np.float64)
    else:
        leverages = np.full(len(df_spot_ratio), leverage, dtype=np.float64)

    # 从后往前截取一下数据，保证杠杆的数据与 ratio 的数据长度是一致的
    leverages = leverages[-len(df_spot_ratio):]
    # ====================================================================================================
    # 2. 开始模拟交易
    # 开始策马奔腾啦 🐎
    # ====================================================================================================
    s_time = time.perf_counter()
    logger.debug(f'▶️ 模拟交易开始{datetime.now()}...')
    equities, turnovers, fees, funding_fees, margin_rates, long_pos_values, short_pos_values, sim_spot, sim_swap = start_simulation(
        leverages=leverages,  # 杠杆
        # 现货最小下单量
        # 永续合约最小下单量
        # 现货杠杆率
        # 永续合约杠杆率
        # 现货最小下单金额
        # 永续合约最小下单金额
        min_margin_rate=conf.margin_rate,  # 最低保证金比例
        # 选股结果计算聚合得到的每个周期目标资金占比
        spot_ratio=df_spot_ratio[spot_symbols].to_numpy(),  # 现货目标资金占比
        swap_ratio=df_swap_ratio[swap_symbols].to_numpy(),  # 永续合约目标资金占比
        # 现货行情数据
        spot_open_p=pivot_dict_spot['open'].to_numpy(),  # 现货开盘价
        spot_close_p=pivot_dict_spot['close'].to_numpy(),  # 现货收盘价
        spot_vwap1m_p=pivot_dict_spot['open'].to_numpy(),  # 现货开盘一分钟均价
        # 永续合约行情数据
        swap_open_p=pivot_dict_swap['open'].to_numpy(),  # 永续合约开盘价
        swap_close_p=pivot_dict_swap['close'].to_numpy(),  # 永续合约收盘价
        swap_vwap1m_p=pivot_dict_swap['open'].to_numpy(),  # 永续合约开盘一分钟均价
        funding_rates=pivot_dict_swap['funding_rate'].to_numpy(),  # 永续合约资金费率
        pos_calc=pos_calc,  # 仓位计算
        require_rebalance=require_rebalance,  # 是否需要rebalance
        sim_spot=sim_spot,  # 现货模拟器
        sim_swap=sim_swap,  # 永续合约模拟器
    )
    logger.ok(f'完成模拟交易，花费时间: {time.perf_counter() - s_time:.3f}秒')

    if state_name == 'state0':
        # 保存模拟交易的状态，只有在原始模拟时候才有效
        df_spot_ratio = create_ratio_snapshot(conf, df_spot_ratio, 'spot')
        df_swap_ratio = create_ratio_snapshot(conf, df_swap_ratio, 'swap')

    # ====================================================================================================
    # 3. 回测结果汇总，并输出相关文件
    # ====================================================================================================
    account_df = pd.DataFrame({
        'candle_begin_time': candle_begin_times,
        'equity': equities,
        'turnover': turnovers,
        'fee': fees,
        'funding_fee': funding_fees,
        'marginRatio': margin_rates,
        'long_pos_value': long_pos_values,
        'short_pos_value': short_pos_values
    })
    account_snapshot = conf.get_snapshot_folder() / f'account_{state_name}.pkl'
    if account_snapshot.exists():
        account_df_0 = pd.read_pickle(account_snapshot)
        account_df_1 = account_df[account_df['candle_begin_time'] > account_df_0['candle_begin_time'].max()]
        account_df = pd.concat([account_df_0, account_df_1], sort=False, copy=False).reset_index(drop=True)

    account_df['净值'] = account_df['equity'] / conf.initial_usdt
    account_df['涨跌幅'] = account_df['净值'].pct_change()
    account_df.loc[account_df['marginRatio'] < conf.margin_rate, '是否爆仓'] = 1
    account_df['是否爆仓'].fillna(method='ffill', inplace=True)
    account_df['是否爆仓'].fillna(value=0, inplace=True)
    account_df['long_short_ratio'] = account_df['long_pos_value'] / (account_df['short_pos_value'] + 1e-8)
    account_df['leverage_ratio'] = (account_df['long_pos_value'] + account_df['short_pos_value']) / account_df['equity']
    account_df.set_index('candle_begin_time', inplace=True)
    account_df['symbol_long_num'] = df_spot_ratio[df_spot_ratio > 0].count(axis=1) + df_swap_ratio[df_swap_ratio > 0].count(axis=1)
    account_df['symbol_short_num'] = df_spot_ratio[df_spot_ratio < 0].count(axis=1) + df_swap_ratio[df_swap_ratio < 0].count(axis=1)
    account_df.reset_index(inplace=True)

    if df_swap_ratio.empty and df_spot_ratio.empty:
        # 没有模拟的情况
        simulate_to = None
    else:
        simulate_to = df_spot_ratio.index.max() if df_swap_ratio.empty else df_swap_ratio.index.max()

    if (state_name is not None) and (simulate_to is not None):
        account_df.to_pickle(conf.get_snapshot_folder() / f'account_{state_name}.pkl')
        state = {
            'spot': {
                'columns': df_spot_ratio.columns,
                'sim': sim_spot,
            },
            'swap': {
                'columns': df_swap_ratio.columns,
                'sim': sim_swap,
            },
            'simulate_to': simulate_to
        }
        pd.to_pickle(state, state_path)
    return account_df


def align_pivot_dimensions(market_pivot_dict, symbols, candle_begin_times):
    """
    对不同维度的数据进行对齐
    :param market_pivot_dict: 原始数据，是一个dict哦
    :param symbols: 币种（列）
    :param candle_begin_times: 时间（行）
    :return:
    """
    return {k: df.loc[candle_begin_times, symbols] for k, df in market_pivot_dict.items()}


# @nb.jit(nopython=True, boundscheck=True)
def start_simulation(leverages, min_margin_rate, spot_ratio, swap_ratio,
                     spot_open_p, spot_close_p, spot_vwap1m_p, swap_open_p, swap_close_p, swap_vwap1m_p,
                     funding_rates, pos_calc, require_rebalance,
                     sim_spot, sim_swap):
    """
    模拟交易
    :param leverages: 杠杆
    :param min_margin_rate: 维持保证金率
    :param spot_ratio: spot 的仓位透视表 (numpy 矩阵)
    :param swap_ratio: swap 的仓位透视表 (numpy 矩阵)
    :param spot_open_p: spot 的开仓价格透视表 (numpy 矩阵)
    :param spot_close_p: spot 的平仓价格透视表 (numpy 矩阵)
    :param spot_vwap1m_p: spot 的 vwap1m 价格透视表 (numpy 矩阵)
    :param swap_open_p: swap 的开仓价格透视表 (numpy 矩阵)
    :param swap_close_p: swap 的平仓价格透视表 (numpy 矩阵)
    :param swap_vwap1m_p: swap 的 vwap1m 价格透视表 (numpy 矩阵)
    :param funding_rates: swap 的 funding rate 透视表 (numpy 矩阵)
    :param pos_calc: 仓位计算
    :param require_rebalance: 是否需要调仓
    :param sim_spot: 现货模拟数据
    :param sim_swap: 合约模拟数据
    :return:
    """
    # ====================================================================================================
    # 1. 初始化回测空间
    # 设置几个固定长度的数组变量，并且重置为0，到时候每一个周期的数据，都按照index的顺序，依次填充进去
    # ====================================================================================================
    n_bars = spot_ratio.shape[0]
    n_syms_spot = spot_ratio.shape[1]
    # n_syms_swap = swap_ratio.shape[1]

    # start_lots_spot = np.zeros(n_syms_spot, dtype=np.int64)
    # start_lots_swap = np.zeros(n_syms_swap, dtype=np.int64)
    # 现货不设置资金费
    funding_rates_spot = np.zeros(n_syms_spot, dtype=np.float64)

    turnovers = np.zeros(n_bars, dtype=np.float64)
    fees = np.zeros(n_bars, dtype=np.float64)
    equities = np.zeros(n_bars, dtype=np.float64)  # equity after execution
    funding_fees = np.zeros(n_bars, dtype=np.float64)
    margin_rates = np.zeros(n_bars, dtype=np.float64)
    long_pos_values = np.zeros(n_bars, dtype=np.float64)
    short_pos_values = np.zeros(n_bars, dtype=np.float64)

    # ====================================================================================================
    # 2. 初始化模拟对象
    # ====================================================================================================

    # ====================================================================================================
    # 3. 开始回测
    # 每次循环包含以下四个步骤：
    # 1. 模拟开盘on_open
    # 2. 模拟执行on_execution
    # 3. 模拟平仓on_close
    # 4. 设置目标仓位set_target_lots
    # 如下依次执行
    # t1: on_open -> on_execution -> on_close -> set_target_lots
    # t2: on_open -> on_execution -> on_close -> set_target_lots
    # t3: on_open -> on_execution -> on_close -> set_target_lots
    # ...
    # tN: on_open -> on_execution -> on_close -> set_target_lots
    # 并且在每一个t时刻，都会记录账户的截面数据，包括equity，funding_fee，margin_rate，等等
    # ====================================================================================================
    #
    for i in range(n_bars):
        """1. 模拟开盘on_open"""
        # 根据开盘价格，计算账户权益，当前持仓的名义价值，以及资金费
        equity_spot, _, pos_value_spot = sim_spot.on_open(spot_open_p[i], funding_rates_spot, spot_open_p[i])
        equity_swap, funding_fee, pos_value_swap = sim_swap.on_open(swap_open_p[i], funding_rates[i], swap_open_p[i])

        # 当前持仓的名义价值
        position_val = np.sum(np.abs(pos_value_spot)) + np.sum(np.abs(pos_value_swap))
        if position_val < 1e-8:
            # 没有持仓
            margin_rate = 10000.0
        else:
            margin_rate = (equity_spot + equity_swap) / float(position_val)

        # 当前保证金率小于维持保证金率，爆仓 💀
        if margin_rate < min_margin_rate:
            margin_rates[i] = margin_rate
            break

        """2. 模拟开仓on_execution"""
        # 根据开仓价格，计算账户权益，换手，手续费
        equity_spot, turnover_spot, fee_spot = sim_spot.on_execution(spot_vwap1m_p[i])
        equity_swap, turnover_swap, fee_swap = sim_swap.on_execution(swap_vwap1m_p[i])

        """3. 模拟K线结束on_close"""
        # 根据收盘价格，计算账户权益
        equity_spot_close, pos_value_spot_close = sim_spot.on_close(spot_close_p[i])
        equity_swap_close, pos_value_swap_close = sim_swap.on_close(swap_close_p[i])

        long_pos_value = (np.sum(pos_value_spot_close[pos_value_spot_close > 0]) +
                          np.sum(pos_value_swap_close[pos_value_swap_close > 0]))

        short_pos_value = -(np.sum(pos_value_spot_close[pos_value_spot_close < 0]) +
                            np.sum(pos_value_swap_close[pos_value_swap_close < 0]))

        # 把中间结果更新到之前初始化的空间
        funding_fees[i] = funding_fee
        equities[i] = equity_spot_close + equity_swap_close
        turnovers[i] = turnover_spot + turnover_swap
        fees[i] = fee_spot + fee_swap
        margin_rates[i] = margin_rate
        long_pos_values[i] = long_pos_value
        short_pos_values[i] = short_pos_value

        # 考虑杠杆
        equity_leveraged = (equity_spot_close + equity_swap_close) * leverages[i]

        """4. 计算目标持仓"""
        # 并不是所有的时间点都需要计算目标持仓，比如D持仓下，只需要在23点更新0点的目标持仓
        if require_rebalance[i] == 1:
            target_lots_spot, target_lots_swap = pos_calc.calc_lots(equity_leveraged, spot_close_p[i], sim_spot.lots,
                                                                    spot_ratio[i], swap_close_p[i], sim_swap.lots,
                                                                    swap_ratio[i])
            # 更新目标持仓
            sim_spot.set_target_lots(target_lots_spot)
            sim_swap.set_target_lots(target_lots_swap)

    return equities, turnovers, fees, funding_fees, margin_rates, long_pos_values, short_pos_values, sim_spot, sim_swap
