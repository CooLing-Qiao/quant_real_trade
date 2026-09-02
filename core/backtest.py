"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import logging
import shutil
import time

import pandas as pd

from config import backtest_path, utc_offset
from core.equity import calc_equity
from core.model.backtest_config import BacktestConfig
from core.model.backtest_config import BacktestConfigFactory
from core.model.timing_signal import TimingSignal
from core.select_coin import calc_factors, select_coins, concat_select_results, transfer_spot_to_swap, \
    agg_multi_strategy_ratio, calc_cross_sections
from core.utils.commons import set_snapshot_time, get_snapshot_time
from core.utils.functions import save_performance_df_csv, load_realtime_data
from core.utils.log_kit import logger, divider
from core.utils.misc_kit import mem_save_exe


def step3_calc_factors(conf: BacktestConfig):
    """
    计算因子
    :param conf: 配置
    :return:
    """
    s_time = time.time()
    logger.info(f'因子计算...')
    calc_factors(conf)
    logger.ok(f'完成计算因子，花费时间：{time.time() - s_time:.2f}秒')


def step4_select_coins(conf: BacktestConfig):
    """
    选币
    :param conf: 配置
    :return:
    """
    s_time = time.time()
    logger.info(f'时序因子计算...')
    calc_factors(conf)
    logger.ok(f'完成计算时序因子，花费时间：{time.time() - s_time:.2f}秒')

    # 计算截面因子
    s_time = time.time()
    logger.info('截面因子计算...')
    calc_cross_sections(conf)
    logger.ok(f'完成计算截面因子，花费时间：{time.time() - s_time:.2f}秒')


def save_latest_period(conf, select_results, max_candle_time):
    # 选币结果的最新时间
    latest_result_list = []
    for stg in conf.strategy_list:
        latest_result_list.append(
            select_results[
                (select_results['strategy'] == stg.name) &
                (select_results['candle_begin_time'] > (max_candle_time - pd.to_timedelta(stg.hold_period)))
                ].copy())
    pd.concat(latest_result_list, ignore_index=True, copy=False).to_pickle(conf.get_result_folder() / '最新持仓周期选币结果.pkl')


def step5_aggregate_select_results(conf: BacktestConfig, max_candle_time, save_final_result=False):
    logger.info(f'整理{conf.name}选币结果...')
    # 整理选币结果
    concat_select_results(conf)  # 合并多个策略的选币结果
    select_results = transfer_spot_to_swap(conf)  # 把现货币对换成合约来节省手续费，如果是纯多或者合约的话，就直接跳过
    save_latest_period(conf, select_results, max_candle_time)
    logger.debug(f'💾 {conf.name}选币结果df大小：'
                   f'{select_results.memory_usage(deep=True).sum() / 1024 / 1024 / 1024:.4f} G')
    if save_final_result:
        # 存储最终的选币结果
        select_results.to_pickle(backtest_path / 'final_select_results.pkl')

    # 聚合大杂烩中多策略的权重，以及多offset选币的权重聚合
    s_time = time.time()
    logger.debug(f'🔃 开始{conf.name}权重聚合...')
    df_spot_ratio, df_swap_ratio = agg_multi_strategy_ratio(conf, select_results, max_candle_time)

    # 始终输出ratios
    df_spot_ratio.to_pickle(conf.get_result_folder() / 'df_spot_ratio.pkl')
    df_swap_ratio.to_pickle(conf.get_result_folder() / 'df_swap_ratio.pkl')

    logger.ok(f'完成{conf.name}权重聚合，花费时间： {time.time() - s_time:.3f}秒')
    return df_spot_ratio, df_swap_ratio


def step6_simulate_performance(conf: BacktestConfig, run_time):
    logger.info(f'{conf.name} 开始模拟交易...')
    account_df = calc_equity(conf, run_time, state_name='state0')

    save_performance_df_csv(conf, 资金曲线=account_df)

    has_timing_signal = isinstance(conf.timing, TimingSignal)

    if has_timing_signal:
        account_df = simu_timing(conf, run_time)

    return account_df


def simu_timing(conf: BacktestConfig, run_time):
    s_time = time.time()
    logger.info(f'{conf.get_fullname(as_folder_name=True)} 资金曲线择时，生成动态杠杆')
    state0_equity_path = conf.get_snapshot_folder() / 'account_state0.pkl'
    if not state0_equity_path.exists():
        logger.warning(f'{state0_equity_path} 不存在，请检查')
        logger.critical(f'{conf.name} 历史选币为空，可能是过滤条件过于严格，或者选中的币种被拉黑，请检查配置。程序退出...AA')
        exit()
    account_df = pd.read_pickle(state0_equity_path)

    # 检查哪个动态杠杆方法有实现，优先使用 get_dynamic_leverage_for_dataframe
    if conf.timing.impl_flags.get('dynamic_leverage_for_dataframe', False):
        leverages = conf.timing.get_dynamic_leverage_for_dataframe(account_df)
    elif conf.timing.impl_flags.get('dynamic_leverage', False):
        leverages = conf.timing.get_dynamic_leverage(account_df['equity'])
    else:
        raise NotImplementedError(f'择时信号 {conf.timing.name} 必须实现 dynamic_leverage 或 dynamic_leverage_for_dataframe 方法之一')
    logger.debug(f'⏰ 完成再择时杠杆计算，已花费时间{time.time() - s_time:.3f}秒')

    account_df = calc_equity(conf, run_time, leverage=leverages * conf.leverage, state_name='state1')
    save_performance_df_csv(conf, 资金曲线_再择时=account_df, 再择时动态杠杆=pd.DataFrame({
        'candle_begin_time': account_df['candle_begin_time'],
        '动态杠杆': leverages
    }))

    return account_df


# ====================================================================================================
# ** 回测主程序 **
# 1. 准备工作
# 2. 读取数据
# 3. 计算因子
# 4. 选币
# 5. 整理选币数据
# 6. 添加下一个每一个周期需要卖出的币的信息
# 7. 计算资金曲线
# ====================================================================================================
def run_backtest_multi(factory: BacktestConfigFactory, run_time):
    # ====================================================================================================
    # 1. 准备工作
    # ====================================================================================================
    divider('准备启动', sep='-')
    iter_results_folder = factory.result_folder

    # 删除缓存
    shutil.rmtree(iter_results_folder, ignore_errors=True)
    iter_results_folder.mkdir(parents=True, exist_ok=True)

    conf_list = factory.config_list
    for index, conf in enumerate(conf_list):
        logger.debug(f'ℹ️ 策略{index + 1}｜共{len(conf_list)}个')
        logger.debug(f'{conf.get_fullname()}')
        conf.save()
    logger.ok('策略池中需要回测的策略数：{}'.format(len(conf_list)))

    # 记录一下时间戳
    r_time = time.time()

    # ====================================================================================================
    # 2. 读取回测所需数据，并做简单的预处理
    # ====================================================================================================
    divider('读取数据', sep='-')
    s_time = time.time()
    conf_all = factory.generate_all_factor_config()

    # 读取数据
    # 针对现货策略和非现货策略读取的逻辑完全不同。
    # - 如果是纯合约模式，只需要读入 swap 数据并且合并即可
    # - 如果是现货模式，需要读入 spot 和 swap 数据并且合并，然后添加 tag
    mem_save_exe(load_realtime_data, conf_all, run_time)  # 串行方式，完全等价
    logger.ok(f'完成读取数据中心数据，花费时间：{time.time() - s_time:.3f}秒')

    # ====================================================================================================
    # 3. 计算因子
    # ====================================================================================================
    divider('时序因子计算', sep='-')
    s_time = time.time()
    mem_save_exe(calc_factors, conf_all)  # 执行时序因子计算
    logger.ok(f'完成计算时序因子，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒')

    # 计算截面因子
    s_time = time.time()
    divider(f'截面因子计算', sep='-')
    mem_save_exe(calc_cross_sections, conf_all)  # 执行截面因子计算
    logger.ok(f'完成计算截面因子，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒')


    # ====================================================================================================
    # 4. 选币
    # - 注意：选完之后，每一个策略的选币结果会被保存到硬盘
    # ====================================================================================================
    divider('选币', sep='-')
    s_time = time.time()
    logger.debug(f'注意：这个过程时间久，和包含的策略及子策略数量、选币数量有关...')
    # ** 正常回测**
    for conf in factory.config_list:
        logger.info(f'{conf.name}的{len(conf.strategy_list)}个子策略选币')
        mem_save_exe(select_coins, conf, multi_process=bool(get_snapshot_time(conf_all.name)))

    logger.ok(f'完成选币，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒')

    # ====================================================================================================
    # 5. 针对选币结果进行聚合
    # ====================================================================================================
    divider('合并选币的资金权重', sep='-')
    logger.setLevel(logging.DEBUG)
    logger.debug(f'注意：主要和选币数量有关...')
    s_time = time.time()
    max_candle_time = run_time - pd.to_timedelta('1D' if conf_all.is_day_period else '1H') - pd.Timedelta(hours=utc_offset)
    # 串行
    for conf in conf_list:
        conf.has_snapshot = bool(get_snapshot_time(conf_all.name))
        logger.debug(f"[{conf.name}] 聚合{len(conf.strategy_list)}个子策略")
        mem_save_exe(step5_aggregate_select_results, conf, max_candle_time, save_final_result=True)
    logger.ok(f'聚合策略资金权重完成，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒')

    # ====================================================================================================
    # 6. 回测模拟
    # ====================================================================================================
    logger.setLevel(logging.DEBUG)
    divider('回测模拟', sep='-')
    logger.info(f'时间会比较久，主要和选币数量有关...')
    s_time = time.time()

    equity_list = []
    for conf in conf_list:
        logger.debug(f"🔃 聚合{conf.name}的{len(conf.strategy_list)}个子策略，并计算资金曲线...")
        equity_list.append(step6_simulate_performance(conf, run_time))

    snapshot_time = max([equity['candle_begin_time'].max() for equity in equity_list])

    set_snapshot_time(conf_all.name, snapshot_time)

    logger.ok(f'完成策略池所有策略模拟，花费时间：{time.time() - s_time:.3f}秒，累计时间：{(time.time() - r_time):.3f}秒，最后资金曲线时间：{snapshot_time}')

    return equity_list
