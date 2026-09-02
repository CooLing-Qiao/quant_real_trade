"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import os
import traceback
from datetime import datetime, timedelta

import dataframe_image as dfi
import numpy as np
import pandas as pd

from core.model.account_config import AccountConfig
from core.utils.dingding import send_wechat_work_msg, send_wechat_work_img
from core.utils.path_kit import get_file_path, get_folder_path

data_path = get_folder_path('data', as_path_type=True)


def calc_spot_position(spot_position, account_name, spot_last_price):
    """
    发送现货、合约持仓信息
    :param spot_position: 现货持仓
    :param account_name: 账户名称
    :param spot_last_price: 现货最新价格
    :return:
              side  change    pos_u   pnl_u  avg_price  cur_price
    symbol
    ARDRU     1  59.24%   419.98  248.81     0.0515     0.0820
    BTSU      1  36.90%   184.30   68.00     0.0071     0.0097
    GLMU      1  26.67%   691.78  184.49     0.1466     0.1857
    """
    # 初始化两个持仓df
    spot_send_df = pd.DataFrame()
    # =如果现货存在持仓
    if not spot_position.empty:
        # 处理symbol列/索引的兼容性问题
        if 'symbol' in spot_position.columns:
            # 如果symbol是列，设置为索引
            spot_position.set_index('symbol', inplace=True)
        spot_position['当前价格'] = spot_last_price
        # =创建一个保存数据的列表
        position_info_list = []
        # =遍历每个持仓的币种
        for symbol in spot_position.index:
            # =生成路径
            path = get_file_path(data_path, account_name, '持仓信息', f'{symbol}.csv')
            # =判断是否存在持仓数据
            if os.path.exists(path):  # 如果该币种保存过文件，则读取历史持仓数据
                position_info = pd.read_csv(path, encoding='gbk', parse_dates=['time'])
                position_info = position_info[position_info['time'] == position_info['time'].max()]  # 只保留最新的一条数据
                position_info['方向'] = 1  # 方向为1
                # 取出部分列append到列表中
                position_info = position_info[['symbol', '方向', '持仓量', '持仓额', '持仓均价']]
            else:  # 如果没有没存数据，则赋值为nan
                position_info = pd.DataFrame(columns=['symbol', '方向', '持仓量', '持仓额', '持仓均价'], index=[0])
                position_info.loc[0, 'symbol'] = symbol
                position_info.loc[0, '方向'] = 1
                position_info.loc[0, '持仓量'] = spot_position.loc[symbol, '当前持仓量']
                position_info.loc[0, '持仓额'] = np.nan
                position_info.loc[0, '持仓均价'] = np.nan

            # =将读取到的币种持仓数据添加到列表中
            position_info_list.append(position_info)

        # =合并数据
        all_position_info = pd.concat(position_info_list, axis=0, copy=False)
        # =整理现货持仓数据
        spot_position = spot_position.reset_index()

        # =将现货持仓数据与读取到的数据merge一下
        spot_send_df = pd.merge(all_position_info, spot_position, on='symbol', how='right')
        spot_send_df['change'] = spot_send_df['当前价格'] / spot_send_df['持仓均价'] - 1  # 计算涨跌幅
        spot_send_df.loc[spot_send_df['持仓均价'] < 0, 'change'] = 1 - spot_send_df['当前价格'] / spot_send_df[
            '持仓均价']  # 如果持仓成本为负
        spot_send_df.sort_values('change', ascending=False, inplace=True)  # 以涨跌幅排序
        spot_send_df['pnl_u'] = spot_send_df['change'] * spot_send_df['持仓额']  # 计算现货的持仓盈亏
        spot_send_df.loc[spot_send_df['持仓均价'] < 0, 'pnl_u'] = spot_send_df['change'] * spot_send_df['持仓额'] * -1
        spot_send_df['change'] = spot_send_df['change'].transform(
            lambda x: f'{x * 100:.2f}%' if str(x) != 'nan' else x)  # 最后将数据转成百分比

        # =修改列名
        rename_cols = {'方向': 'side', '持仓额': 'pos_u', '持仓均价': 'avg_price', '当前价格': 'cur_price'}
        spot_send_df.rename(columns=rename_cols, inplace=True)

        # =修改格式并整理
        spot_send_df = spot_send_df[['symbol', 'side', 'change', 'pos_u', 'pnl_u', 'avg_price', 'cur_price']]
        spot_send_df['pos_u'] = spot_send_df['pos_u'].map(lambda x: round(x, 2))
        spot_send_df['pnl_u'] = spot_send_df['pnl_u'].map(lambda x: round(x, 2))
        spot_send_df['avg_price'] = spot_send_df['avg_price'].map(lambda x: round(x, 4))
        spot_send_df['cur_price'] = spot_send_df['cur_price'].map(lambda x: round(x, 4))
        spot_send_df.set_index('symbol', inplace=True)

    return spot_send_df


def calc_swap_position(swap_position):
    """
    发送现货、合约持仓信息
    :param swap_position: 合约持仓
    :return:
              side  change    pos_u   pnl_u  avg_price  cur_price
    symbol
    ARDRU     1  59.24%   419.98  248.81     0.0515     0.0820
    BTSU      1  36.90%   184.30   68.00     0.0071     0.0097
    GLMU      1  26.67%   691.78  184.49     0.1466     0.1857
    """
    swap_send_df = pd.DataFrame()
    # 如果存在合约持仓
    if not swap_position.empty:
        # =整理合约持仓数据
        swap_send_df = swap_position.copy()
        swap_send_df['side'] = swap_send_df['当前持仓量'].apply(
            lambda x: 1 if float(x) > 0 else (-1 if float(x) < 0 else 0))  # 取出方向
        swap_send_df['change'] = (swap_send_df['当前标记价格'] / swap_send_df['均价'] - 1) * swap_send_df[
            'side']  # 计算涨跌幅
        swap_send_df['pos_u'] = swap_send_df['当前持仓量'] * swap_send_df['当前标记价格']  # 计算持仓额
        swap_send_df.rename(columns={'均价': 'avg_price', '持仓盈亏': 'pnl_u', '当前标记价格': 'cur_price'},
                            inplace=True)  # 修改列名
        swap_send_df = swap_send_df[['side', 'change', 'pos_u', 'pnl_u', 'avg_price', 'cur_price']]
        swap_send_df.sort_values(['side', 'change'], ascending=[True, False], inplace=True)  # 以涨跌幅排序
        swap_send_df['change'] = swap_send_df['change'].transform(
            lambda x: f'{x * 100:.2f}%' if str(x) != 'nan' else x)  # 最后将数据转成百分比

        # =修改格式
        swap_send_df['pos_u'] = swap_send_df['pos_u'].map(lambda x: round(x, 2))
        swap_send_df['pnl_u'] = swap_send_df['pnl_u'].map(lambda x: round(x, 2))
        swap_send_df['avg_price'] = swap_send_df['avg_price'].map(lambda x: round(x, 4))
        swap_send_df['cur_price'] = swap_send_df['cur_price'].map(lambda x: round(x, 4))

    return swap_send_df


def save_send_pos_info(timestamp_key, pos_df, account_name, pos_type='spot'):
    pos_pkl_path = get_file_path(data_path, account_name, '账户信息', f'pos_{pos_type}.pkl', as_path_type=True)
    if pos_pkl_path.exists():
        pos_pkl = pd.read_pickle(pos_pkl_path)
    else:
        pos_pkl = {}
    
    # 添加新数据
    pos_pkl[timestamp_key] = pos_df
    
    # 清理7天前的数据（基于传入的timestamp_key）
    seven_days_ms = 7 * 24 * 60 * 60 * 1000  # 7天的毫秒数
    cutoff_timestamp = timestamp_key - seven_days_ms
    
    # 删除超过7天的数据
    keys_to_remove = [key for key in pos_pkl.keys() if key < cutoff_timestamp]
    for key in keys_to_remove:
        del pos_pkl[key]
    
    # 保存数据
    pd.to_pickle(pos_pkl, pos_pkl_path)
    
    # 输出清理信息
    if keys_to_remove:
        print(f"清理了{len(keys_to_remove)}条超过7天的{pos_type}持仓记录")


def save_position_snapshot(account_config: AccountConfig, run_time):
    """
    保存持仓快照（在下单前执行，确保能捕获到所有持仓币种）
    :param account_config: 账户配置
    :param run_time: 运行时间
    """
    spot_last_price = account_config.bn.get_spot_ticker_price_series()
    spot_position = account_config.spot_position
    swap_position = account_config.swap_position
    account_name = account_config.name
    
    print(f"保存持仓快照: 现货{len(spot_position) if not spot_position.empty else 0}个, 合约{len(swap_position) if not swap_position.empty else 0}个")

    # 处理现货持仓快照 - 即使为空也保存记录
    if not spot_position.empty:
        spot_snapshot = calc_spot_position(spot_position, account_name, spot_last_price)
        
        if not spot_snapshot.empty:
            # 添加时间列
            spot_snapshot_copy = spot_snapshot.reset_index()
            spot_snapshot_copy['timestamp'] = run_time
            
            # 保存到PKL文件
            spot_pkl_path = get_file_path(data_path, account_name, '账户信息', 'spot_position_history.pkl', as_path_type=True)
            if spot_pkl_path.exists():
                existing_data = pd.read_pickle(spot_pkl_path)
                updated_data = pd.concat([existing_data, spot_snapshot_copy], ignore_index=True)
            else:
                updated_data = spot_snapshot_copy
            
            # 清理7天前的数据
            cutoff_time = datetime.now() - timedelta(days=7)
            updated_data = updated_data[updated_data['timestamp'] >= cutoff_time]
            
            # 确保目录存在
            spot_pkl_path.parent.mkdir(parents=True, exist_ok=True)
            pd.to_pickle(updated_data, spot_pkl_path)
    else:
        # 即使没有现货持仓，也保存一个空记录
        empty_spot_record = pd.DataFrame({
            'symbol': ['EMPTY'],
            'side': [0],
            'change': ['0.00%'],
            'pos_u': [0.0],
            'pnl_u': [0.0],
            'avg_price': [0.0],
            'cur_price': [0.0],
            'timestamp': [run_time]
        })
        
        spot_pkl_path = get_file_path(data_path, account_name, '账户信息', 'spot_position_history.pkl', as_path_type=True)
        if spot_pkl_path.exists():
            existing_data = pd.read_pickle(spot_pkl_path)
            updated_data = pd.concat([existing_data, empty_spot_record], ignore_index=True)
        else:
            updated_data = empty_spot_record
        
        # 清理7天前的数据
        cutoff_time = datetime.now() - timedelta(days=7)
        updated_data = updated_data[updated_data['timestamp'] >= cutoff_time]
        
        # 确保目录存在
        spot_pkl_path.parent.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(updated_data, spot_pkl_path)

    # 处理合约持仓快照 - 即使为空也保存记录
    if not swap_position.empty:
        swap_snapshot = calc_swap_position(swap_position)
        
        if not swap_snapshot.empty:
            # 添加时间列
            swap_snapshot_copy = swap_snapshot.reset_index()
            swap_snapshot_copy['timestamp'] = run_time
            
            # 保存到PKL文件
            swap_pkl_path = get_file_path(data_path, account_name, '账户信息', 'swap_position_history.pkl', as_path_type=True)
            if swap_pkl_path.exists():
                existing_data = pd.read_pickle(swap_pkl_path)
                updated_data = pd.concat([existing_data, swap_snapshot_copy], ignore_index=True)
            else:
                updated_data = swap_snapshot_copy
            
            # 清理7天前的数据
            cutoff_time = datetime.now() - timedelta(days=7)
            updated_data = updated_data[updated_data['timestamp'] >= cutoff_time]
                
            # 确保目录存在
            swap_pkl_path.parent.mkdir(parents=True, exist_ok=True)
            pd.to_pickle(updated_data, swap_pkl_path)
    else:
        # 即使没有合约持仓，也保存一个空记录
        empty_swap_record = pd.DataFrame({
            'symbol': ['EMPTY'],
            'side': [0],
            'change': ['0.00%'],
            'pos_u': [0.0],
            'pnl_u': [0.0],
            'avg_price': [0.0],
            'cur_price': [0.0],
            'timestamp': [run_time]
        })
        
        swap_pkl_path = get_file_path(data_path, account_name, '账户信息', 'swap_position_history.pkl', as_path_type=True)
        if swap_pkl_path.exists():
            existing_data = pd.read_pickle(swap_pkl_path)
            updated_data = pd.concat([existing_data, empty_swap_record], ignore_index=True)
        else:
            updated_data = empty_swap_record
        
        # 清理7天前的数据
        cutoff_time = datetime.now() - timedelta(days=7)
        updated_data = updated_data[updated_data['timestamp'] >= cutoff_time]
            
        # 确保目录存在
        swap_pkl_path.parent.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(updated_data, swap_pkl_path)


def get_historical_position(account_name, hours_ago, pos_type='spot'):
    """
    获取历史持仓数据
    :param account_name: 账户名
    :param hours_ago: 多少小时前
    :param pos_type: 持仓类型 'spot' 或 'swap'
    :return: 历史持仓DataFrame
    """
    pos_pkl_path = get_file_path(data_path, account_name, '账户信息', f'{pos_type}_position_history.pkl', as_path_type=True)
    
    if not pos_pkl_path.exists():
        return pd.DataFrame()

    pos_data = pd.read_pickle(pos_pkl_path)
    
    if pos_data.empty:
        return pd.DataFrame()

    # 计算目标时间
    target_time = datetime.now() - timedelta(hours=hours_ago)
    
    # 确保timestamp列是datetime类型
    pos_data['timestamp'] = pd.to_datetime(pos_data['timestamp'])
    
    # 查找最接近的历史数据
    available_times = pos_data['timestamp'].unique()
    
    if len(available_times) == 0:
        return pd.DataFrame()

    # 找到最接近且不超过目标时间的时间戳
    valid_times = available_times[available_times <= target_time]
    
    if len(valid_times) == 0:
        # 如果没有找到不超过目标时间的时间戳，返回最早的时间戳
        closest_time = available_times.min()
    else:
        closest_time = valid_times.max()

    # 返回对应时间的数据
    result_data = pos_data[pos_data['timestamp'] == closest_time].copy()
    
    # 删除timestamp列，保持symbol作为普通列（不设为索引）
    result_data = result_data.drop('timestamp', axis=1).reset_index(drop=True)
    
    # 如果历史数据只包含EMPTY记录，返回空DataFrame
    if not result_data.empty and result_data['symbol'].iloc[0] == 'EMPTY':
        return pd.DataFrame()
    
    return result_data


def calc_position_pnl(current_pos, historical_pos, spot_last_price, pos_type='spot', total_equity=None):
    """
    计算持仓盈亏
    :param current_pos: 当前持仓
    :param historical_pos: 历史持仓
    :param spot_last_price: 现货最新价格
    :param pos_type: 持仓类型，'spot' 或 'swap'
    :param total_equity: 账户总净值，用于计算盈亏占总资金的百分比
    :return: 盈亏DataFrame
    """
    if current_pos.empty or historical_pos.empty:
        return pd.DataFrame()

    # 合并当前持仓和历史持仓
    current_pos_copy = current_pos.copy() if not current_pos.empty else pd.DataFrame()
    historical_pos_copy = historical_pos.copy() if not historical_pos.empty else pd.DataFrame()

    # 确保symbol列可用 - 智能处理不同数据格式
    if not current_pos_copy.empty:
        if 'symbol' not in current_pos_copy.columns:
            current_pos_copy = current_pos_copy.reset_index()
    
    if not historical_pos_copy.empty:
        if 'symbol' not in historical_pos_copy.columns:
            historical_pos_copy = historical_pos_copy.reset_index()
    
    # 如果历史数据仍然没有symbol列，跳过盈亏计算
    if not historical_pos_copy.empty and 'symbol' not in historical_pos_copy.columns:
        return pd.DataFrame()

    # 获取所有涉及的币种
    current_symbols = set(current_pos_copy['symbol'].tolist()) if not current_pos_copy.empty and 'symbol' in current_pos_copy.columns else set()
    historical_symbols = set(historical_pos_copy['symbol'].tolist()) if not historical_pos_copy.empty and 'symbol' in historical_pos_copy.columns else set()
    all_symbols = current_symbols.union(historical_symbols)

    if not all_symbols:
        return pd.DataFrame()

    # 构建盈亏DataFrame
    pnl_list = []

    for symbol in all_symbols:
        pnl_info = {'symbol': symbol}

        # 获取当前持仓信息
        if not current_pos_copy.empty and 'symbol' in current_pos_copy.columns:
            current_row = current_pos_copy[current_pos_copy['symbol'] == symbol]
            if not current_row.empty:
                current_row = current_row.iloc[0]
                current_pos_u = current_row['pos_u']
                current_price = current_row['cur_price']
                current_side = current_row['side']
            else:
                current_pos_u = 0
                current_price = spot_last_price.get(symbol, 0)
                current_side = 0
        else:
            # 当前持仓为空或缺少symbol列
            current_pos_u = 0
            current_price = spot_last_price.get(symbol, 0)
            current_side = 0
        
        # 特殊处理：如果当前价格为0（通常是因为平仓），尝试从市场数据获取
        if current_price == 0 and symbol in spot_last_price:
            current_price = spot_last_price[symbol]

        # 获取历史持仓信息
        historical_exists = False
        if not historical_pos_copy.empty and 'symbol' in historical_pos_copy.columns:
            historical_row = historical_pos_copy[historical_pos_copy['symbol'] == symbol]
            if not historical_row.empty:
                historical_row = historical_row.iloc[0]
                historical_pos_u = historical_row['pos_u']
                historical_price = historical_row['cur_price']
                historical_side = historical_row['side']
                historical_exists = True
            else:
                historical_pos_u = 0
                historical_price = 0
                historical_side = 0
        else:
            # 历史数据为空或缺少symbol列
            historical_pos_u = 0
            historical_price = 0
            historical_side = 0

        # 计算盈亏
        if historical_pos_u == 0 and current_pos_u == 0:
            continue  # 如果历史和当前都没有持仓，跳过

        # 价格涨跌幅 - 只有当历史数据存在且历史价格和当前价格都不为0时才计算
        if historical_exists and historical_price != 0 and current_price != 0:
            # 重新计算原始价格变化（不依赖历史数据中可能已调整的change字段）
            raw_price_change = (current_price / historical_price - 1)
            
            # 根据持仓方向调整价格变化显示（从持仓者角度看盈亏）
            if historical_side == 1:  # 多头持仓：价格上涨为正，下跌为负
                price_change = raw_price_change
            elif historical_side == -1:  # 空头持仓：价格下跌为正（盈利），上涨为负（亏损）
                price_change = -raw_price_change  # 空头价格上涨显示为负数（亏损）
            else:
                price_change = raw_price_change  # side为0的情况，保持原始价格变化
            
        else:
            raw_price_change = None
            price_change = None  # 无法计算价格变化

        # 总盈亏（实际的盈亏金额）
        # 修复：正确计算空头盈亏 = (开仓价格 - 当前价格) × 持仓数量
        if historical_side == -1:  # 空头持仓
            if historical_pos_u != 0:
                # 从历史数据获取开仓成本
                avg_price = 0
                if historical_exists and not historical_pos_copy.empty:
                    hist_row = historical_pos_copy[historical_pos_copy['symbol'] == symbol]
                    if not hist_row.empty and 'avg_price' in hist_row.columns:
                        avg_price = hist_row.iloc[0]['avg_price']
                
                if avg_price != 0:
                    historical_position_amount = abs(historical_pos_u) / avg_price
                    
                    if current_pos_u != 0 and current_price != 0:
                        # 仍有持仓：计算mark-to-market盈亏
                        current_position_amount = abs(current_pos_u) / current_price
                        
                        # 平仓部分的盈亏
                        closed_amount = historical_position_amount - current_position_amount
                        closed_pnl = (avg_price - current_price) * closed_amount if closed_amount > 0 else 0
                        
                        # 剩余持仓的mark-to-market盈亏
                        remaining_pnl = (avg_price - current_price) * current_position_amount
                        
                        total_pnl = closed_pnl + remaining_pnl
                    else:
                        # 完全平仓：使用历史时点的市场价格作为平仓价格
                        close_price = historical_price if historical_price != 0 else avg_price
                        total_pnl = (avg_price - close_price) * historical_position_amount
                        
                else:
                    # 无法获取开仓成本，使用旧逻辑
                    total_pnl = abs(historical_pos_u) - abs(current_pos_u)
            else:
                total_pnl = 0
        else:
            # 多头持仓或无方向：正常计算
            total_pnl = current_pos_u - historical_pos_u
        
        # 持仓盈亏计算（假设持仓量不变，仅价格变化带来的盈亏）
        if historical_pos_u != 0 and raw_price_change is not None:
            # 使用原始价格变化计算价格影响的盈亏金额
            if historical_side == 1:  # 多头：价格上涨盈利，下跌亏损
                position_pnl = abs(historical_pos_u) * raw_price_change
            elif historical_side == -1:  # 空头：价格下跌盈利，上涨亏损
                position_pnl = abs(historical_pos_u) * raw_price_change * -1
            else:
                position_pnl = 0
        else:
            position_pnl = 0

        # 交易盈亏（扣除价格变化影响后，纯粹由交易操作带来的盈亏）
        trade_pnl = total_pnl - position_pnl

        pnl_info.update({
            f'historical_pos_u': round(historical_pos_u, 2),
            f'current_pos_u': round(current_pos_u, 2),
            f'historical_price': round(historical_price, 4) if historical_price != 0 else 0,
            f'current_price': round(current_price, 4),
            f'price_change': round((total_pnl / total_equity) * 100, 2) if total_equity and total_equity != 0 else None,  # 盈亏占总资金的百分比
            f'position_pnl': round(position_pnl, 2),
            f'trade_pnl': round(trade_pnl, 2),
            f'total_pnl': round(total_pnl, 2),
            f'side': current_side if current_pos_u != 0 else historical_side,
            f'type': pos_type  # 添加持仓类型
        })

        pnl_list.append(pnl_info)

    if not pnl_list:
        return pd.DataFrame()

    pnl_df = pd.DataFrame(pnl_list)
    pnl_df = pnl_df.sort_values('total_pnl', ascending=False)

    return pnl_df


def calculate_and_send_pnl_report(account_config: AccountConfig, spot_position, swap_position, spot_last_price):
    """
    计算并发送持仓盈亏报告
    :param account_config: 账户配置
    :param spot_position: 现货持仓
    :param swap_position: 合约持仓
    :param spot_last_price: 现货最新价格
    """
    account_name = account_config.name
    
    # 获取账户总净值
    total_equity = account_config.swap_equity + account_config.spot_equity

    # 获取当前持仓
    current_spot_pos = calc_spot_position(spot_position, account_name, spot_last_price)
    current_swap_pos = calc_swap_position(swap_position)

    # 计算1小时盈亏
    hist_spot_1h = get_historical_position(account_name, 1, 'spot')
    hist_swap_1h = get_historical_position(account_name, 1, 'swap')

    spot_pnl_1h = calc_position_pnl(current_spot_pos, hist_spot_1h, spot_last_price, 'spot', total_equity)
    swap_pnl_1h = calc_position_pnl(current_swap_pos, hist_swap_1h, spot_last_price, 'swap', total_equity)

    # 计算24小时盈亏
    hist_spot_24h = get_historical_position(account_name, 24, 'spot')
    hist_swap_24h = get_historical_position(account_name, 24, 'swap')

    spot_pnl_24h = calc_position_pnl(current_spot_pos, hist_spot_24h, spot_last_price, 'spot', total_equity)
    swap_pnl_24h = calc_position_pnl(current_swap_pos, hist_swap_24h, spot_last_price, 'swap', total_equity)

    # 合并现货和合约盈亏数据
    all_pnl_1h = pd.concat([spot_pnl_1h, swap_pnl_1h], ignore_index=True) if not spot_pnl_1h.empty or not swap_pnl_1h.empty else pd.DataFrame()
    all_pnl_24h = pd.concat([spot_pnl_24h, swap_pnl_24h], ignore_index=True) if not spot_pnl_24h.empty or not swap_pnl_24h.empty else pd.DataFrame()

    # 生成报告
    pnl_msg = f"【持仓盈亏报告】\n"
    pnl_msg += f"账户: {account_name}\n"
    pnl_msg += f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    pnl_msg += f"PS: 对于开/平仓的币种统计可能存在一些误差，后续会优化\n\n"

    # 生成1小时TOP5盈亏信息
    if not all_pnl_1h.empty:
        # 过滤出有意义的数据
        significant_pnl_1h = all_pnl_1h[all_pnl_1h['total_pnl'].abs() > 0.01].copy()
        if not significant_pnl_1h.empty:
            # 按总盈亏排序
            significant_pnl_1h = significant_pnl_1h.sort_values('total_pnl', ascending=False)
            
            # 获取盈利TOP5和亏损TOP5
            profit_top5_1h = significant_pnl_1h[significant_pnl_1h['total_pnl'] > 0].head(5)
            loss_top5_1h = significant_pnl_1h[significant_pnl_1h['total_pnl'] < 0].tail(5)
            
            total_1h = significant_pnl_1h['total_pnl'].sum()
            pnl_msg += f"【1小时盈亏 总计: {total_1h:.2f} U】\n"
            
            # 盈利TOP5
            if not profit_top5_1h.empty:
                pnl_msg += f"📈 盈利TOP5:\n"
                for _, row in profit_top5_1h.iterrows():
                    # 完善的nan检查：检查None、nan和其他无效值
                    if pd.isna(row['price_change']) or row['price_change'] is None:
                        price_change_str = "平仓"
                    else:
                        price_change_str = f"{row['price_change']:.2f}%"
                    type_name = "现货" if row['type'] == 'spot' else "合约"
                    pnl_msg += f"  {row['symbol']}({type_name}): +{row['total_pnl']:.2f} U ({price_change_str})\n"
                pnl_msg += "\n"
            
            # 亏损TOP5
            if not loss_top5_1h.empty:
                pnl_msg += f"📉 亏损TOP5:\n"
                for _, row in loss_top5_1h.iterrows():
                    # 完善的nan检查：检查None、nan和其他无效值
                    if pd.isna(row['price_change']) or row['price_change'] is None:
                        price_change_str = "平仓"
                    else:
                        price_change_str = f"{row['price_change']:.2f}%"
                    type_name = "现货" if row['type'] == 'spot' else "合约"
                    pnl_msg += f"  {row['symbol']}({type_name}): {row['total_pnl']:.2f} U ({price_change_str})\n"
                pnl_msg += "\n"

    # 生成24小时TOP5盈亏信息
    if not all_pnl_24h.empty:
        # 过滤出有意义的数据
        significant_pnl_24h = all_pnl_24h[all_pnl_24h['total_pnl'].abs() > 0.01].copy()
        if not significant_pnl_24h.empty:
            # 按总盈亏排序
            significant_pnl_24h = significant_pnl_24h.sort_values('total_pnl', ascending=False)
            
            # 获取盈利TOP5和亏损TOP5
            profit_top5_24h = significant_pnl_24h[significant_pnl_24h['total_pnl'] > 0].head(5)
            loss_top5_24h = significant_pnl_24h[significant_pnl_24h['total_pnl'] < 0].tail(5)
            
            total_24h = significant_pnl_24h['total_pnl'].sum()
            pnl_msg += f"【24小时盈亏 总计: {total_24h:.2f} U】\n"
            
            # 盈利TOP5
            if not profit_top5_24h.empty:
                pnl_msg += f"📈 盈利TOP5:\n"
                for _, row in profit_top5_24h.iterrows():
                    # 完善的nan检查：检查None、nan和其他无效值
                    if pd.isna(row['price_change']) or row['price_change'] is None:
                        price_change_str = "平仓"
                    else:
                        price_change_str = f"{row['price_change']:.2f}%"
                    type_name = "现货" if row['type'] == 'spot' else "合约"
                    pnl_msg += f"  {row['symbol']}({type_name}): +{row['total_pnl']:.2f} U ({price_change_str})\n"
                pnl_msg += "\n"
            
            # 亏损TOP5
            if not loss_top5_24h.empty:
                pnl_msg += f"📉 亏损TOP5:\n"
                for _, row in loss_top5_24h.iterrows():
                    # 完善的nan检查：检查None、nan和其他无效值
                    if pd.isna(row['price_change']) or row['price_change'] is None:
                        price_change_str = "平仓"
                    else:
                        price_change_str = f"{row['price_change']:.2f}%"
                    type_name = "现货" if row['type'] == 'spot' else "合约"
                    pnl_msg += f"  {row['symbol']}({type_name}): {row['total_pnl']:.2f} U ({price_change_str})\n"
                pnl_msg += "\n"

    # 发送报告信息
    if pnl_msg:
        send_wechat_work_msg(pnl_msg, account_config.wechat_webhook_url)

    # 保存详细的盈亏数据到pkl文件
    try:
        # 生成毫秒级时间戳作为key
        timestamp_key = int(datetime.utcnow().timestamp()) * 1000
        
        # 准备保存的数据字典
        pnl_record = {}
        
        # 处理1小时和24小时盈亏数据
        for pnl_data, period in [(all_pnl_1h, '1小时'), (all_pnl_24h, '24小时')]:
            period_key = '1h' if period == '1小时' else '24h'
            
            if not pnl_data.empty:
                # 过滤出有意义的数据（总盈亏不为0）
                significant_pnl = pnl_data[pnl_data['total_pnl'].abs() > 0.01]
                if not significant_pnl.empty:
                    # 只保留重要列
                    display_cols = ['symbol', 'price_change', 'position_pnl', 'trade_pnl', 'total_pnl', 'type']
                    display_data = significant_pnl[display_cols].copy()
                    display_data = display_data.sort_values('total_pnl', ascending=False)
                    
                    # 转换为字典格式保存
                    pnl_record[period_key] = display_data.to_dict('records')
                else:
                    pnl_record[period_key] = []
            else:
                pnl_record[period_key] = []
        
        # 读取现有的pkl文件
        pnl_pkl_path = get_file_path(data_path, account_name, '账户信息', 'pnl_history.pkl', as_path_type=True)
        
        if pnl_pkl_path.exists():
            existing_data = pd.read_pickle(pnl_pkl_path)
        else:
            existing_data = {}
        
        # 添加新数据
        existing_data[timestamp_key] = pnl_record
        
        # 清理7天前的数据（基于传入的timestamp_key）
        seven_days_ms = 7 * 24 * 60 * 60 * 1000  # 7天的毫秒数
        cutoff_timestamp = timestamp_key - seven_days_ms
        
        # 删除超过7天的数据
        keys_to_remove = [key for key in existing_data.keys() if key < cutoff_timestamp]
        for key in keys_to_remove:
            del existing_data[key]
        
        # 确保目录存在
        pnl_pkl_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 保存到pkl文件
        pd.to_pickle(existing_data, pnl_pkl_path)
        
        print(f"保存盈亏详细数据到: {pnl_pkl_path}")
        print(f"当前保存记录数: {len(existing_data)}")
        if keys_to_remove:
            print(f"清理了{len(keys_to_remove)}条超过7天的旧记录")
            
    except Exception as e:
        print(f"保存盈亏详细数据失败: {e}")
        print(traceback.format_exc())


def send_img_for_dataframe(dataframe, wechat_webhook_url):
    try:
        # =定义导出图片位置
        pos_pic_path = get_file_path(data_path, 'pos.png')
        # =导出图片
        dfi.export(dataframe, pos_pic_path, table_conversion='matplotlib', max_cols=-1, max_rows=-1)
        # =发送图片
        send_wechat_work_img(pos_pic_path, wechat_webhook_url)
    except BaseException as e:
        print(traceback.format_exc())
        print('转换图片出现错误', e)