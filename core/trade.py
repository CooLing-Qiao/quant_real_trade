"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import random
import math
import numpy as np
import pandas as pd


def _round_by_type(value: float, precision: int, symbol_type: str) -> float:
    """
    根据交易类型进行精度处理
    :param value: 原始数量
    :param precision: 精度
    :param symbol_type: 交易类型 'spot'/'swap'
    """
    _m = 10 ** precision
    if symbol_type == 'swap':
        # 合约都向上取整
        return value / abs(value) * math.ceil(abs(value) * _m) / _m
    else:  # spot
        if value > 0:  # 现货买入向上取整
            return math.ceil(value * _m) / _m
        else:  # 现货卖出向下取整
            return -math.floor(abs(value) * _m) / _m


def _random_split(x, order_amount_limit):
    if abs(x) < order_amount_limit:
        return [x]

    result = []
    remaining = abs(x)

    while remaining > 0:
        random_amount = random.uniform(0.5 * order_amount_limit, 1.2 * order_amount_limit)
        amount = min(random_amount, remaining)
        result.append(amount if x > 0 else -amount)
        remaining -= amount

    return result


def generate_order_limits(orders_df, order_amount_limit):
    from core.binance.base_client import BinanceClient
    # 动态调整 order_amount_limit，确保满足每个币种的 min_notional 要求
    adjusted_order_limits = {}  # 存储每个币种调整后的limit
    for idx, row in orders_df.iterrows():
        symbol = row['symbol']
        symbol_type = row['symbol_type']
        market_info = BinanceClient.market_info[symbol_type]
        min_notional = market_info.get('min_notional', {})

        # 获取该币种的最小名义价值
        if symbol in min_notional:
            symbol_min_notional = min_notional[symbol]
            # 如果传入的 order_amount_limit 小于 min_notional，调整为 min_notional * 2.1
            # 因为随机拆单的范围是:0.5~1.2之间
            if order_amount_limit < symbol_min_notional * 2.1:
                adjusted_limit = symbol_min_notional * 2.1
                print(f"[拆单调整] {symbol}: order_amount_limit 从 {order_amount_limit} 调整为 {adjusted_limit} (min_notional={symbol_min_notional})")
            else:
                adjusted_limit = order_amount_limit
        else:
            # 如果没有 min_notional 信息，使用原始值
            adjusted_limit = order_amount_limit

        adjusted_order_limits[idx] = adjusted_limit

    return adjusted_order_limits


def split_order_twap(orders_df: pd.DataFrame, order_amount_limit):
    """
    对超额订单进行随机拆分,并进行调整,尽可能每批中子订单、每批订单让多空平衡
    :param orders_df: 原始下单信息
    :param order_amount_limit: 目标下单金额（非限额）
    """
    """
         @我已无心战斗 老板提供，随机拆单优化代码以及最后一笔碎单处理
    """
    from core.binance.base_client import BinanceClient

    # 保存原始下单量
    original_order_amount = orders_df['实际下单量'].copy()

    # 随机拆单
    adjusted_order_limits = generate_order_limits(orders_df, order_amount_limit)
    # 使用每个币种调整后的 limit 进行拆单
    orders_df['拆单金额'] = orders_df.apply(
        lambda row: _random_split(row['实际下单资金'], adjusted_order_limits[row.name]),
        axis=1
    )
    orders_df['拆单金额'] = orders_df['拆单金额'].apply(np.array)  # 将list转成numpy的array
    # 计算拆单金额对应多少的下单量
    orders_df['实际下单量'] = orders_df['实际下单量'] / orders_df['实际下单资金'] * orders_df['拆单金额']

    # 对每个币种进行精度调整
    for idx, row in orders_df.iterrows():
        symbol = row['symbol']
        symbol_type = row['symbol_type']
        market_info = BinanceClient.market_info[symbol_type]
        min_qty = market_info['min_qty']
        if symbol not in min_qty:
            print(f"symbol {symbol} not in min_qty")
            continue

        remaining_amount = original_order_amount[idx]  # 原始下单量
        split_amounts = orders_df.loc[idx, '实际下单量']  # 拆分后的数量列表
        precision = min_qty[symbol]

        # 计算向上取整后的总量会超出多少
        total_after_round = sum(_round_by_type(amt, precision, symbol_type) for amt in split_amounts[:-1])

        # 最后一笔用剩余量，确保总量不变
        if len(split_amounts) > 0:
            last_amount = remaining_amount - total_after_round
            # 如果最后一笔方向相反或太小，就合并到前一笔
            if (last_amount * split_amounts[0] < 0) or abs(last_amount) < 10**(-precision):
                split_amounts = split_amounts[:-1]
                if len(split_amounts) > 0:
                    split_amounts[-1] += last_amount
            else:
                split_amounts[-1] = last_amount

    # 继续原有的处理流程
    orders_df.reset_index(inplace=True)
    del orders_df['拆单金额']

    # 将拆单量进行数据进行展开
    orders_df = orders_df.explode('实际下单量')

    # 定义拆单list
    twap_orders_df_list = []
    # 分组
    group = orders_df.groupby(by='index')
    # 获取分组里面最大的长度
    max_group_len = group['index'].size().max()
    if max_group_len > 0:
        # 批量构建拆单数据
        for i in range(max_group_len):
            twap_orders_df_list.append(
                group.nth(i).sort_values('实际下单资金', ascending=False).reset_index(drop=True))

    return twap_orders_df_list


def split_order_balanced(orders_df: pd.DataFrame, order_amount_limit: float):
    """
    对超额订单进行随机拆分，并通过多空配平构建批次

    配平规则：
    1. 每批次优先实现多空平衡（空头在前，多头在后）
    2. 配平误差容忍：90-110%范围内可接受
    3. 小额订单优先分配到第一批
    4. 配对订单用元组组织，单个订单也用元组包裹

    :param orders_df: 原始下单信息，包含列：symbol, symbol_type, 实际下单量, 实际下单资金
    :param order_amount_limit: 目标下单金额（非限额）
    :return: list[DataFrame]，每个DataFrame代表一批订单，包含配对组ID列
    """
    from core.binance.base_client import BinanceClient

    # ========== 阶段1：预处理 - 过滤小额币种 ==========

    # 过滤小于 min_notional 的币种
    filtered_indices = []
    for idx, row in orders_df.iterrows():
        symbol = row['symbol']
        symbol_type = row['symbol_type']
        market_info = BinanceClient.market_info[symbol_type]
        min_notional = market_info.get('min_notional', {})
        min_qty = market_info.get('min_qty', {})

        if symbol not in min_qty:
            print(f"[警告] symbol {symbol} 不在 min_qty 中，跳过精度调整")
            filtered_indices.append(idx)
            continue

        if symbol not in min_notional:
            print(f"[警告] symbol {symbol} 不在 min_notional 中，跳过")
            filtered_indices.append(idx)
            continue

        if abs(row['实际下单资金']) < min_notional[symbol] and row['交易模式'] != '清仓':
            print(f"[过滤] {symbol} 下单金额 {abs(row['实际下单资金']):.2f} < min_notional {min_notional[symbol]:.2f}")
            filtered_indices.append(idx)

    orders_df = orders_df.drop(filtered_indices).reset_index(drop=True)

    # 如果所有订单都被过滤，返回空列表
    if orders_df.empty:
        return []

    # 重建 original_order_amount 索引
    original_order_amount = orders_df['实际下单量'].copy()

    # ========== 阶段2：随机拆单 ==========
    # 随机拆单
    adjusted_order_limits = generate_order_limits(orders_df, order_amount_limit)
    # 使用每个币种调整后的 limit 进行拆单
    orders_df['拆单金额列表'] = orders_df.apply(
        lambda row: _random_split(row['实际下单资金'], adjusted_order_limits[row.name]),
        axis=1
    )

    # ========== 阶段3：构建拆单项池 ==========
    # 构建拆单项列表
    split_items = []
    for idx, row in orders_df.iterrows():
        for amount in row['拆单金额列表']:
            split_items.append({
                'symbol': row['symbol'],
                'symbol_type': row['symbol_type'],
                '拆单金额': amount,
                'original_index': idx,
            })

    # 分池
    short_pool = [item for item in split_items if item['拆单金额'] < 0]
    long_pool = [item for item in split_items if item['拆单金额'] > 0]

    # 按金额绝对值排序（大额优先）
    short_pool.sort(key=lambda x: abs(x['拆单金额']), reverse=True)
    long_pool.sort(key=lambda x: abs(x['拆单金额']), reverse=True)

    # ========== 阶段4：配平批次构建 ==========
    batches = []  # 每批是一个元组列表

    # 追踪每个拆单项是否已使用（使用索引追踪）
    short_used = [False] * len(short_pool)
    long_used = [False] * len(long_pool)

    # === 配对阶段 ===
    while True:
        current_batch = []
        used_symbols_in_batch = set()  # 当前批次已使用的币种

        # 阶段A：正常配对（90-110%误差）
        for short_idx, short_item in enumerate(short_pool):
            if short_used[short_idx]:
                continue
            if short_item['original_index'] in used_symbols_in_batch:
                continue

            short_amount = abs(short_item['拆单金额'])
            target_long_min = short_amount * 0.9
            target_long_max = short_amount * 1.1

            # 寻找未使用的多头币种配对
            matched_longs = []
            matched_indices = []
            accumulated_long = 0
            matched_symbols_in_pair = set()  # 追踪当前配对组内已匹配的币种

            for long_idx, long_item in enumerate(long_pool):
                if long_used[long_idx]:
                    continue
                if long_item['original_index'] in used_symbols_in_batch:
                    continue
                if long_item['original_index'] in matched_symbols_in_pair:
                    continue

                long_amount = abs(long_item['拆单金额'])

                # 检查是否可以加入配对
                if accumulated_long + long_amount <= target_long_max:
                    matched_longs.append(long_item)
                    matched_indices.append(long_idx)
                    accumulated_long += long_amount
                    matched_symbols_in_pair.add(long_item['original_index'])

                    # 检查是否在误差范围内
                    if target_long_min <= accumulated_long <= target_long_max:
                        break

            # 如果找到合适的配对
            if target_long_min <= accumulated_long <= target_long_max:
                # 标记为已使用
                short_used[short_idx] = True
                used_symbols_in_batch.add(short_item['original_index'])

                for idx in matched_indices:
                    long_used[idx] = True
                    used_symbols_in_batch.add(long_pool[idx]['original_index'])

                # 构建配对元组
                current_batch.append(tuple([short_item] + matched_longs))

        # 阶段B：兜底配对（不限误差，处理剩余未配对的）
        for short_idx, short_item in enumerate(short_pool):
            if short_used[short_idx]:
                continue
            if short_item['original_index'] in used_symbols_in_batch:
                continue

            short_amount = abs(short_item['拆单金额'])

            # 找绝对值误差最小的多头配对
            best_long_idx = None
            best_diff = float('inf')

            for long_idx, long_item in enumerate(long_pool):
                if long_used[long_idx]:
                    continue
                if long_item['original_index'] in used_symbols_in_batch:
                    continue

                long_amount = abs(long_item['拆单金额'])
                diff = abs(long_amount - short_amount)

                if diff < best_diff:
                    best_diff = diff
                    best_long_idx = long_idx

            # 如果找到最佳配对
            if best_long_idx is not None:
                # 标记为已使用
                short_used[short_idx] = True
                used_symbols_in_batch.add(short_item['original_index'])

                long_used[best_long_idx] = True
                used_symbols_in_batch.add(long_pool[best_long_idx]['original_index'])

                # 构建配对元组
                current_batch.append(tuple([short_item, long_pool[best_long_idx]]))

        # 如果当前批次有配对，保存并继续
        if current_batch:
            batches.append(current_batch)
        else:
            # 无法继续配对，退出配对循环
            break

    # === 单方向订单处理阶段 ===
    # 收集所有未使用的订单项
    remaining_items = []
    for idx, item in enumerate(short_pool):
        if not short_used[idx]:
            remaining_items.append(item)
    for idx, item in enumerate(long_pool):
        if not long_used[idx]:
            remaining_items.append(item)

    # 将单方向订单分批，保证每批中每个币种只出现一次
    while remaining_items:
        current_batch = []
        used_symbols_in_batch = set()

        # 从剩余订单中取，每个币种最多一个
        items_to_remove = []
        for item in remaining_items:
            if item['original_index'] not in used_symbols_in_batch:
                current_batch.append((item,))  # 单个订单也用元组包裹
                used_symbols_in_batch.add(item['original_index'])
                items_to_remove.append(item)

        # 移除已使用的订单
        for item in items_to_remove:
            remaining_items.remove(item)

        if current_batch:
            batches.append(current_batch)

    # ========== 阶段5：转换为 DataFrame 列表 ==========
    batch_df_list = []

    for batch in batches:
        batch_rows = []

        for pair_idx, pair_tuple in enumerate(batch):
            for item in pair_tuple:
                # 获取原始行信息
                orig_row = orders_df.iloc[item['original_index']]

                # 计算实际下单量 = 拆单金额 / 实际下单资金 * 实际下单量
                actual_amount = (item['拆单金额'] / orig_row['实际下单资金']
                               * original_order_amount[item['original_index']])

                row_data = {
                    'symbol': item['symbol'],
                    'symbol_type': item['symbol_type'],
                    '拆单金额': item['拆单金额'],
                    '实际下单量': actual_amount,
                    '配对组ID': pair_idx,
                    'original_index': item['original_index'],
                }

                # 复制其他列（如果有）
                for col in orig_row.index:
                    if col not in row_data and col not in ['拆单金额列表', '实际下单量', '实际下单资金']:
                        row_data[col] = orig_row[col]

                batch_rows.append(row_data)

        batch_df = pd.DataFrame(batch_rows)
        batch_df_list.append(batch_df)

    # ========== 阶段6：精度调整 ==========
    # 对每批进行精度调整
    for batch_df in batch_df_list:
        # 按 original_index 分组（同一个币种）
        for orig_idx in batch_df['original_index'].unique():
            mask = batch_df['original_index'] == orig_idx
            indices = batch_df[mask].index.tolist()

            if not indices:
                continue

            # 获取币种信息
            row = batch_df.loc[indices[0]]
            symbol = row['symbol']
            symbol_type = row['symbol_type']

            market_info = BinanceClient.market_info[symbol_type]
            min_qty = market_info['min_qty']
            precision = min_qty[symbol]

            # 对除最后一笔外的所有订单进行精度调整
            split_amounts = batch_df.loc[indices, '实际下单量'].values
            # 使用批次内总量，而非原始总量
            remaining_amount = sum(split_amounts)
            total_after_round = sum(
                _round_by_type(amt, precision, symbol_type)
                for amt in split_amounts[:-1]
            )

            # 更新前N-1笔
            for i in range(len(indices) - 1):
                batch_df.loc[indices[i], '实际下单量'] = _round_by_type(
                    split_amounts[i], precision, symbol_type
                )

            # 最后一笔用剩余量
            last_amount = remaining_amount - total_after_round

            # 检查方向是否一致且数量是否合理
            if (last_amount * split_amounts[0] < 0) or abs(last_amount) < 10**(-precision):
                # 方向相反或太小，合并到前一笔
                if len(indices) > 1:
                    batch_df.loc[indices[-2], '实际下单量'] += last_amount
                    batch_df.drop(indices[-1], inplace=True)
                else:
                    # 只有一笔，无法合并，保留但标记
                    batch_df.loc[indices[-1], '实际下单量'] = last_amount
            else:
                batch_df.loc[indices[-1], '实际下单量'] = last_amount

        # 重置索引
        batch_df.reset_index(drop=True, inplace=True)

    return batch_df_list
