"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from core.utils.log_kit import logger


def load_coin_cap(file_path: str, candle_df: pd.DataFrame, save_cols: list, symbol: str) -> Optional[pd.DataFrame]:
    """
    加载coinmarketcap数据
    :param file_path: 文件路径
    :param candle_df: 原始k线数据
    :param save_cols: 需要保存的列
    :param symbol: 币种
    :return: 一个 df 数据
    """
    data_path = Path(file_path)
    # 合并选币名称并剔除空字符串
    symbols = (
        {symbol} if symbol else
        {s for s in (list(candle_df['symbol_spot'].unique()) + list(candle_df['symbol_swap'].unique())) if s}
    )
    # 兼容 回测 & 实盘。
    # 回测币种带 -USDT，实盘币种带 USDT
    # bmac 那边存储的数据是 pkl，所以这里拼接 .pkl 结尾
    all_file_paths = [data_path / f'{symbol}.pkl' for symbol in symbols]

    # 读取数据
    extra_df_list = []
    for file_path in all_file_paths:
        try:
            extra_df = pd.read_pickle(file_path)
            extra_df.drop_duplicates(subset=['candle_begin_time'], keep='last', inplace=True)
            extra_df.sort_values('candle_begin_time', inplace=True)  # 通过candle_begin_time排序
            extra_df_list.append(extra_df)
        except BaseException as e:
            continue

    if not extra_df_list:
        logger.warning(f'coin_cap 没有找到任何数据，symbol={symbols}')
        return None

    if len(candle_df) == 1:
        logger.warning(f'{candle_df["symbol"].iloc[0]}币种K线数据不足，跳过')
        return None

    # 预处理
    extra_df = pd.concat(extra_df_list, ignore_index=True, copy=False)
    time_diff = candle_df['candle_begin_time'].iloc[1] - candle_df['candle_begin_time'].iloc[0]
    hour_diff = time_diff.components.days * 24 + time_diff.components.hours
    hold_period_type = 'D' if hour_diff == 24 else 'H'

    if hold_period_type == 'H':
        extra_df['candle_begin_time'] += pd.Timedelta('23H')

    # 合并数据(这里是兼容分钟偏移)
    merge_df = pd.merge_asof(candle_df[['candle_begin_time', 'close']], extra_df, on='candle_begin_time', direction='backward')

    # 处理 cmc 数据中的 usd_price与close 数据的不一致
    # 例：
    # 1000SATS usd_price = 0.0000001， 但是close = 0.0001
    times = merge_df['close'] / merge_df['usd_price']
    times = times.map(lambda x: 10 ** np.log10(x).round(0))
    times = [times.mode().iloc[0] if times.notna().sum() > 0 else np.nan] * len(merge_df)

    # 筛选指定列
    columns = save_cols if save_cols else merge_df.columns
    for col in columns:
        if 'supply' in col:
            merge_df[col] = merge_df[col] / times
        merge_df[col].fillna(method='ffill', inplace=True)

    return merge_df[columns]


def load_coin_btc(file_path: str, candle_df: pd.DataFrame, save_cols: list, symbol: str) -> Optional[pd.DataFrame]:
    """
    加载BTC现货K线数据
    优先尝试回测模式（读取 BTC-USDT.csv），失败则尝试实盘模式（按分钟偏移读取 pkl）
    :param file_path: 数据目录路径
    :param candle_df: 原始k线数据
    :param save_cols: 需要保存的列（如 ['btc_close']）
    :param symbol: 币种（未使用，保留接口一致性）
    :return: 一个 df 数据
    """
    if candle_df is None or candle_df.empty:
        logger.warning('coin_BTC 未收到K线数据，跳过')
        return None

    if len(candle_df) == 1:
        logger.warning(f'{candle_df["symbol"].iloc[0]}币种K线数据不足，跳过')
        return None

    data_path = Path(file_path)
    extra_df = None

    # === 模式1：回测模式，直接读取 BTC-USDT.csv ===
    csv_path = data_path / 'BTC-USDT.csv'
    if csv_path.exists():
        try:
            # 读取 CSV 文件，跳过第一行（乱码行），第二行是列头，使用 GBK 编码
            extra_df = pd.read_csv(csv_path, skiprows=1, encoding='gbk', parse_dates=['candle_begin_time'])
            extra_df = extra_df[['candle_begin_time', 'close']].copy()
        except BaseException as e:
            logger.warning(f'coin_BTC 回测模式读取失败：{e}')
            extra_df = None

    # === 模式2：实盘模式，按分钟偏移读取 pkl ===
    if extra_df is None:
        minute_offset = int(candle_df['candle_begin_time'].iloc[-1].minute)
        hour_offset = f'{minute_offset}m'
        data_folder = data_path / hour_offset

        if data_folder.exists():
            for symbol_name in ('BTCUSDT', 'BTC-USDT'):
                candidate_path = data_folder / f'{symbol_name}.pkl'
                try:
                    extra_df = pd.read_pickle(candidate_path)
                    extra_df = extra_df[['candle_begin_time', 'close']].copy()
                    break
                except BaseException:
                    continue

    if extra_df is None or extra_df.empty:
        logger.warning(f'coin_BTC 没有找到任何数据，路径={data_path}')
        return None

    extra_df.drop_duplicates(subset=['candle_begin_time'], keep='last', inplace=True)
    extra_df.sort_values('candle_begin_time', inplace=True)

    # 移除时区信息（如果存在）
    if extra_df['candle_begin_time'].dt.tz is not None:
        extra_df['candle_begin_time'] = extra_df['candle_begin_time'].dt.tz_localize(None)

    candle_time_df = candle_df[['candle_begin_time']].copy()
    if candle_time_df['candle_begin_time'].dt.tz is not None:
        candle_time_df['candle_begin_time'] = candle_time_df['candle_begin_time'].dt.tz_localize(None)

    # 将 close 重命名为 btc_close
    extra_df.rename(columns={'close': 'btc_close'}, inplace=True)

    # 合并数据
    merge_df = pd.merge(
        candle_time_df.sort_values('candle_begin_time'),
        extra_df,
        on='candle_begin_time',
        how='left'
    )

    # 筛选指定列
    columns = save_cols if save_cols else merge_df.columns
    for col in columns:
        if col in merge_df.columns:
            merge_df[col].fillna(method='ffill', inplace=True)

    # 确保返回的列存在
    available_cols = [col for col in columns if col in merge_df.columns]
    if not available_cols:
        logger.warning(f'coin_BTC 没有可用的列：{columns}')
        return None

    return merge_df[available_cols]
