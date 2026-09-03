#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
丁针阶梯止盈（错时挂单版）

用法：python dingzhen.py <账户名> [--dry-run]
    <账户名> 对应 实盘/accounts/ 目录下的账户配置文件名（不含 .py 后缀）
    --dry-run 干跑模式：正常读持仓、读市场信息、等待调仓完成信号、计算阶梯，
              但不会真实下单/撤单，只打印"本来会挂出/撤掉什么"，用于上线前观察一个完整周期。

设计说明：
- 账户主循环（core/account_exec.py）在真正开始调仓前，会先把持仓读进内存做快照，之后子策略回测、
  算目标仓位、下单整个流程要跑数十秒到数分钟。如果丁针的止盈单在这段时间里成交，主程序算出来的
  目标仓位就是基于过期持仓的，下单量会算错。所以丁针不跟主循环抢挂单，而是"错时"运行：
    1. 在主程序预计启动前 CANCEL_LEAD_MINUTES 分钟，主动撤掉自己挂的所有止盈单，让出场子；
    2. 轮询主程序调仓完成的信号文件（data/<账户>/账户换仓信息/{YYYYMMDD}_{HH}.csv，
       这是 core/real_trading.py: run_by_account 的最后一步产物，出现即代表本轮调仓彻底结束）；
    3. 信号出现后，静置 SETTLE_SECONDS 秒，按这时的真实持仓重新计算并挂出整条阶梯止盈单；
       等不到信号（主程序崩溃/跑在debug模式不写该文件）就在 WAIT_TIMEOUT_MINUTES 分钟后兜底，
       按当前持仓直接挂单，并发企业微信告警。
- 阶梯止盈：止盈价 = 这一小时K线的真实开盘价 × (1 ± level)（多头+，空头-），**与持仓成本（均价）
  完全无关**——每小时都是独立的一次新赌注，不管这个仓位历史上盈亏多少。这个开盘价是直接问交易所
  要"这一小时正在走的K线"拿到的（get_current_hour_open），从整点那一刻起就固定不变，不是用标记价
  近似出来的，这样才能跟回测里"用每根K线自己的开盘价做基准，对比止盈拿到的价格与这根K线自己开盘
  到收盘的收益"这套逻辑完全对上。多头 level 取 LADDER_LEVELS_LONG，空头取 LADDER_LEVELS_SHORT
  （多空分开配置，是用这套回测方法挑出来的数据支持的档位，多空表现不对称，不能共用一套）。档位
  平分当前持仓量。
- 因为每小时都按当时的真实持仓重建，主策略自己加/减仓导致的持仓量变化会自动被下一次重建吸收，
  不需要额外跟踪、校准。
- 两次重建之间（挂单存续期）会持续做健康检查：每笔挂单按 orderId 核对交易所真实状态，
  FILLED 就发通知，被意外撤单（CANCELED 等终态）就原样挂回。

is_crash 期间合约多头单K止损（STOP_LOSS_ENABLED，默认关闭）：
- 只做多头止损，不做对称的空头止损（暴涨止损）——已经过代理估算验证是灾难性的，绝对不要加。
- is_crash 独立按持仓币自己的K线重新判定（过去 CRASH_WINDOW 小时内出现过单根跌幅低于
  CRASH_THRESHOLD 的暴跌），判定逻辑跟 factors/Acc_reverse_v3.py 里的同名概念一致，窗口目前
  跟该因子出厂默认（96小时）保持一致；本账户实际持有多头的子策略（Acc_reverse/Trix）都不
  引用 Acc_reverse_v3，没有现成的 is_crash 可以直接复用，只能独立按持仓币重新算一遍。
- 止损用交易所侧的条件单（STOP_MARKET + closePosition=true），不是本地轮询：普通限价单挂在
  不利方向会被立即撮合，做不了止损；本地轮询的延迟又恰好打在最该保护的场景（瀑布行情常常几分钟
  内走完）。2025-12-09 币安把条件单强制迁移到独立的 /fapi/v1/algoOrder 接口（不再支持走
  /fapi/v1/order 下单），对应封装见 core/binance/base_client.py 的 place_swap_algo_order /
  get_swap_algo_open_orders / cancel_swap_algo_order。
- 止损单不跟着止盈单一起在 CANCEL_LEAD_MINUTES 时提前撤（止损是保险，不该在调仓窗口裸奔），
  而是在 rebuild_ladders 之后、每小时统一"先撤旧的、再按新持仓判定挂新的"。
- 只对普通账户（fapi）生效，统一账户（papi）未接入。
"""

import json
import math
import sys
import time
import traceback
from datetime import datetime, timedelta

import pandas as pd

from config import error_webhook_url
from core.account_manager import init_system
from core.utils.commons import retry_wrapper
from core.utils.dingding import send_wechat_work_msg
from core.utils.log_kit import logger, divider
from core.utils.path_kit import get_file_path

# ====================================================================================================
# ** 配置 **
# ====================================================================================================
LADDER_LEVELS_LONG = [0.45, 0.60, 0.75]   # 多头止盈档位（盈利比例）
LADDER_LEVELS_SHORT = [0.25, 0.35, 0.45]  # 空头止盈档位（盈利比例）

CANCEL_LEAD_MINUTES = 5    # 主程序预计下单前多少分钟撤单清场
WAIT_TIMEOUT_MINUTES = 20  # 等不到调仓完成信号，多久后兜底直接重建
SETTLE_SECONDS = 20        # 看到完成信号后静置多久再读持仓（让最后的成交/结算落定）
POLL_SECONDS = 15          # 清场前/等待信号期间的健康检查 & 轮询间隔

TERMINAL_ORDER_STATUS = ('CANCELED', 'EXPIRED', 'REJECTED', 'EXPIRED_IN_MATCH')  # 视为"被撤单"的订单终态

# ++++ is_crash 期间合约多头单K止损 ++++
# 背景见 docs（研究结论）：is_crash 期间的多头，若单根K线跌幅过深，加一道交易所侧的条件止损，
# 把左尾损失截断在阈值附近。只做多头止损——对称的空头止损（暴涨止损）已经过代理估算验证是灾难性的，
# 绝对不要加。2026-09-02 已用 calibrate_algo_order.py 在真实账户上校准过下单/查询/撤单三个接口
# （见该脚本注释里链的 plan 文档），全部通过，正式开启。
STOP_LOSS_ENABLED = True    # 总开关
CRASH_WINDOW = 96           # is_crash 判定窗口（小时），跟 Acc_reverse_v3 因子出厂默认一致
CRASH_THRESHOLD = -0.10     # 窗口内只要有一根K线跌幅低于此阈值，即判定为 is_crash
STOP_DROP = 0.25            # 止损线：相对上一根已收盘K线收盘价的跌幅
STOP_WORKING_TYPE = 'CONTRACT_PRICE'  # 条件单触发价格基准，跟回测口径（用K线价格）对齐，不用标记价

DRY_RUN = False  # 干跑模式开关，由 --dry-run 命令行参数设置，见文件末尾


# ====================================================================================================
# ** 状态持久化 **
# 注意：不能放在 data/runtime/ 下——real_trading.py 每轮调仓开头会把整个 data/runtime/ 删掉重建。
# ====================================================================================================
def _state_file_path(account_name: str):
    return get_file_path('data', 'dingzhen', f'{account_name}_state.json', as_path_type=True)


def load_state(account_name: str) -> dict:
    path = _state_file_path(account_name)
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            logger.warning(f'丁针状态文件损坏，将重建：{path}')
    return {}


def save_state(account_name: str, state: dict):
    path = _state_file_path(account_name)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ====================================================================================================
# ** 交易所查询 / 下单 **
# ====================================================================================================
def get_open_swap_orders(acct_conf) -> pd.DataFrame:
    """获取账户当前全部合约挂单（复用 cancel_all_swap_orders 用的同一套查询接口）"""
    bn = acct_conf.bn
    get_open_orders_func = getattr(bn.exchange, bn.constants.get('get_swap_open_orders_api'))
    orders = retry_wrapper(get_open_orders_func, params={'timestamp': ''}, func_name='丁针查询合约挂单')
    columns = ['symbol', 'orderId', 'side', 'price', 'origQty', 'reduceOnly']
    if not orders:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(orders)
    df['orderId'] = df['orderId'].astype(int)
    df['price'] = pd.to_numeric(df['price'])
    df['origQty'] = pd.to_numeric(df['origQty'])
    return df


def _query_order_api_name(acct_conf) -> str:
    """按账户类型（普通账户/统一账户）推断单笔订单查询接口名，与 constants 里 open_orders 接口保持同一套命名规则"""
    open_orders_api = acct_conf.bn.constants.get('get_swap_open_orders_api', '')
    if open_orders_api.startswith('papi'):
        return 'papi_get_um_order'
    return 'fapiprivate_get_order'


def query_order_status(acct_conf, symbol: str, order_id: int) -> dict | None:
    """按 orderId 向交易所查询某笔订单的真实状态，查询失败返回 None（本轮先跳过，下一轮再确认）"""
    bn = acct_conf.bn
    query_func = getattr(bn.exchange, _query_order_api_name(acct_conf))
    try:
        return retry_wrapper(query_func, params={'symbol': symbol, 'orderId': order_id, 'timestamp': ''},
                              func_name='丁针查询订单状态', retry_times=2, sleep_seconds=2)
    except Exception as e:
        logger.error(f'{symbol} 查询订单 {order_id} 状态失败：{e}')
        return None


def get_current_hour_open(acct_conf, symbol: str) -> float | None:
    """
    直接问交易所要"这一小时正在走的K线"的开盘价——从整点那一刻起就固定不变，是止盈档位真正应该
    锚定的基准。core/binance/base_client.py 里现成的 get_candle_df 是给策略因子计算用的，专门把
    还没走完的这根K线过滤掉了（避免用一根还在变化的K线算因子），所以这里不能复用，单独直接请求。
    这是公共行情接口，普通账户/统一账户共用同一个方法名，不需要按账户类型区分。
    """
    bn = acct_conf.bn
    try:
        kline = retry_wrapper(bn.exchange.fapipublic_get_klines,
                               params={'symbol': symbol, 'interval': '1h', 'limit': 1},
                               func_name='丁针获取当前小时开盘价', retry_times=2, sleep_seconds=2)
    except Exception as e:
        logger.error(f'{symbol} 获取当前小时开盘价失败：{e}')
        return None
    if not kline:
        return None
    return float(kline[-1][1])  # K线字段顺序：0=开盘时间 1=open 2=high 3=low 4=close ...


def get_recent_closed_closes(acct_conf, symbol: str, limit: int) -> list:
    """
    获取该币最近 limit 根【已收盘】1小时K线的收盘价，按时间升序排列。
    跟 get_current_hour_open 用同一个公开行情接口（fapipublic_get_klines），多请求2根做缓冲，
    并且无条件丢弃最后一根——交易所返回的最后一根可能是正在走的当前小时K线，is_crash 的判定口径
    （factors/Acc_reverse_v3.py）只用"已经走完"的K线，混进还在变化的最新数据会让判定在盘中反复
    开关，产生毛刺。
    """
    bn = acct_conf.bn
    try:
        klines = retry_wrapper(bn.exchange.fapipublic_get_klines,
                               params={'symbol': symbol, 'interval': '1h', 'limit': limit + 2},
                               func_name='止损获取历史K线', retry_times=2, sleep_seconds=2)
    except Exception as e:
        logger.error(f'{symbol} 获取历史K线失败，本周期跳过止损判定：{e}')
        return []
    if not klines or len(klines) < 2:
        return []
    closed = klines[:-1]  # 丢弃最后一根（可能未收盘）
    return [float(k[4]) for k in closed[-limit:]]  # K线字段顺序：4=close


def is_crash_symbol(closes: list) -> bool:
    """
    过去 CRASH_WINDOW 根已收盘K线内，是否出现过单根跌幅低于 CRASH_THRESHOLD 的暴跌。
    判定逻辑跟 factors/Acc_reverse_v3.py 里的 is_crash 完全一致
    （min_change = close.pct_change().rolling(window, min_periods=1).min(); is_crash = min_change < 阈值），
    只是这里独立维护、窗口用 CRASH_WINDOW=144小时（而不是该因子出厂默认的96小时）——本账户实际
    持有多头的子策略是 Acc_reverse/Trix，都不引用 Acc_reverse_v3，没有现成的 is_crash 可以直接复用，
    只能按持仓币自己的K线重新算一遍。
    :param closes: 按时间升序排列的已收盘K线收盘价
    """
    if len(closes) < 2:
        return False
    pct_change = pd.Series(closes, dtype='float64').pct_change()
    return bool((pct_change < CRASH_THRESHOLD).any())


def build_stop_plan(prev_close: float, position_amt: float, price_precision: int) -> dict | None:
    """
    只对净持仓为多头的币生成止损计划：SELL 平多，stopPrice = 上一根已收盘K线收盘价 * (1 - STOP_DROP)。
    净持仓为空头/无持仓时返回 None——空头止损（暴涨止损）已经过代理估算验证是灾难性的，不做。
    """
    if position_amt <= 0:
        return None
    stop_price = round_price(prev_close * (1 - STOP_DROP), price_precision)
    return {'side': 'SELL', 'stop_price': stop_price}


def round_price(price: float, precision: int) -> float:
    return round(price, precision)


def round_qty_down(qty: float, precision: int) -> float:
    """数量向下取整到交易所允许的精度，避免因为四舍五入超过实际可平仓位"""
    _m = 10 ** precision
    return math.floor(qty * _m) / _m


def place_one_order(acct_conf, symbol: str, side: str, qty: float, price: float) -> dict | None:
    if DRY_RUN:
        logger.info(f'[干跑] 将挂单：{symbol} {side} 数量 {qty} 价格 {price}（LIMIT/GTC/reduceOnly）—— 未真实下单')
        return {'order_id': -int(time.time() * 1000), 'qty': qty, 'price': price, 'side': side, 'status': 'simulated'}

    res = acct_conf.bn.place_swap_order(
        symbol=symbol,
        side=side,
        quantity=qty,
        price=price,
        type='LIMIT',
        timeInForce='GTC',
        reduceOnly=str(True),
    )
    if not res or not res.get('orderId'):
        return None
    return {'order_id': int(res['orderId']), 'qty': qty, 'price': price, 'side': side, 'status': 'open'}


def place_rung_order(acct_conf, symbol: str, side: str, qty: float, price: float, level: float) -> dict | None:
    """挂出一档阶梯止盈单。GTC 限价单是被动挂单，不存在市价单那种"冲击市场"的问题，
    所以不按 max_one_order_amount 拆单——拆单只会白白引入额外的精度取整误差。"""
    order = place_one_order(acct_conf, symbol, side, qty, price)
    if order is None:
        logger.error(f'{symbol} 第{level * 100:.0f}%档止盈单下单失败：数量 {qty}，价格 {price}')
        return None
    order['level'] = level
    return order


def place_stop_order(acct_conf, symbol: str, side: str, stop_price: float) -> dict | None:
    """挂一张止损条件单（STOP_MARKET/closePosition=true）。跟丁针止盈单的 place_one_order 保持同一个
    DRY_RUN 处理模式：干跑下只打印、返回一个假 algo_id，不发真实请求。"""
    if DRY_RUN:
        logger.info(f'[干跑] 将挂止损单：{symbol} {side} stopPrice={stop_price}（STOP_MARKET/closePosition）'
                    f'—— 未真实下单')
        return {'algo_id': -int(time.time() * 1000), 'stop_price': stop_price, 'side': side, 'status': 'simulated'}

    res = acct_conf.bn.place_swap_algo_order(symbol, side, stop_price, close_position=True,
                                             working_type=STOP_WORKING_TYPE)
    algo_id = res.get('algoId') or res.get('clientAlgoId')
    if not algo_id:
        return None
    return {'algo_id': algo_id, 'stop_price': stop_price, 'side': side, 'status': 'open'}


# ====================================================================================================
# ** 阶梯计算 **
# ====================================================================================================
def build_ladder_plan(hour_open_price: float, position_amt: float,
                       price_precision: int, qty_precision: int) -> list:
    """
    根据这一小时K线的真实开盘价、当前持仓量，算出这一小时要挂的止盈档位。
    跟持仓成本（均价）完全无关——每小时都是独立的一次新赌注。
    多头（position_amt > 0）：止盈价 = hour_open_price * (1 + level)，卖出平多
    空头（position_amt < 0）：止盈价 = hour_open_price * (1 - level)，买入平空
    档位平分当前持仓量。
    """
    is_long = position_amt > 0
    total_qty = abs(position_amt)
    levels = LADDER_LEVELS_LONG if is_long else LADDER_LEVELS_SHORT

    qty_each = round_qty_down(total_qty / len(levels), qty_precision)
    if qty_each <= 0:
        return []

    side = 'SELL' if is_long else 'BUY'
    plan = []
    for level in levels:
        price = hour_open_price * (1 + level) if is_long else hour_open_price * (1 - level)
        price = round_price(price, price_precision)
        plan.append({'level': level, 'price': price, 'qty': qty_each, 'side': side})
    return plan


# ====================================================================================================
# ** 订单状态核对（挂单是否还在 / 成交 / 被撤）**
# ====================================================================================================
def _resolve_order(acct_conf, symbol: str, order: dict, open_order_ids: set) -> str:
    """返回 'open' / 'filled' / 'canceled' / 'unknown'，不做任何下单/撤单动作"""
    if order['order_id'] in open_order_ids:
        return 'open'
    order_info = query_order_status(acct_conf, symbol, order['order_id'])
    status = (order_info or {}).get('status')
    if status == 'FILLED':
        return 'filled'
    if status in TERMINAL_ORDER_STATUS:
        return 'canceled'
    return 'unknown'


def notify_fill(acct_conf, symbol: str, order: dict):
    msg = (f"【丁针止盈】{symbol} 第{order['level'] * 100:.0f}%档止盈单已成交，"
           f"数量 {order['qty']}，价格 {order['price']}")
    logger.ok(msg)
    send_wechat_work_msg(msg, acct_conf.wechat_webhook_url)


def health_check(acct_conf, state: dict):
    """挂单存续期间的巡检：确认成交就通知，被意外撤单就原样挂回（前提是仓位还在同一方向）。
    这里加一次持仓校验是安全网：止损触发平仓后，本函数末尾的 health_check_stop_orders 会主动
    撤掉同symbol残留的止盈单，但那是在本轮ladder检查【之后】才执行的（写在下面调用顺序里），
    所以刚触发的那一轮巡检，ladder订单在这里可能还没被撤、状态仍是"open"；不管是这种"还没
    轮到"的情况，还是订单确实已经被撤销，只要一看到"canceled"就补一次方向校验——仓位已经
    不对就不再挂回，否则原样挂回一张空仓的reduceOnly单只会被交易所拒绝，还会触发
    place_swap_order 内部的异常告警，纯噪音"""
    if DRY_RUN or not state:
        return
    open_orders_df = get_open_swap_orders(acct_conf)
    open_order_ids = set(open_orders_df['orderId']) if not open_orders_df.empty else set()
    position_df = None  # 惰性获取：只有真的遇到"被撤单"才查一次，避免每轮巡检都多打一次接口

    for symbol in list(state.keys()):
        sym_state = state[symbol]
        still_open = []
        for order in sym_state.get('orders', []):
            result = _resolve_order(acct_conf, symbol, order, open_order_ids)
            if result == 'open' or result == 'unknown':
                still_open.append(order)
            elif result == 'filled':
                notify_fill(acct_conf, symbol, order)
            elif result == 'canceled':
                if position_df is None:
                    position_df = acct_conf.bn.get_swap_position_df()
                position_amt = float(position_df.loc[symbol, '当前持仓量']) if symbol in position_df.index else 0.0
                # 止盈单方向和持仓方向相反：SELL 平多要求仓位还是多头，BUY 平空要求仓位还是空头
                still_same_direction = (order['side'] == 'SELL' and position_amt > 0) or \
                                       (order['side'] == 'BUY' and position_amt < 0)
                if not still_same_direction:
                    logger.info(f'{symbol} 第{order["level"] * 100:.0f}%档止盈单被撤且仓位方向已不同'
                               f'（大概率是止损触发平仓），不再挂回')
                    continue
                logger.warning(f'{symbol} 第{order["level"] * 100:.0f}%档止盈单被意外撤单，重新挂回')
                new_order = place_one_order(acct_conf, symbol, order['side'], order['qty'], order['price'])
                if new_order is not None:
                    new_order['level'] = order['level']
                    still_open.append(new_order)
                else:
                    logger.error(f'{symbol} 第{order["level"] * 100:.0f}%档止盈单重新挂回失败，'
                                 f'该档本轮将不再持有，等下一次重建')

        if still_open:
            sym_state['orders'] = still_open
        else:
            sym_state.pop('orders', None)
        if not sym_state.get('orders') and not sym_state.get('stop'):
            state.pop(symbol, None)

    if STOP_LOSS_ENABLED:
        health_check_stop_orders(acct_conf, state)


def _clear_ladder_keys(state: dict):
    """只清掉每个symbol的止盈'orders'子键，保留'stop'子键（止损条件单的状态跟踪）。
    不能用 state.clear() 整体清空——那会把止损单的 algoId 记录也一起冲掉，导致
    clear_stop_orders 只能靠兜底扫描去撤单，失去按 algoId 精确撤单的能力。"""
    for symbol in list(state.keys()):
        state[symbol].pop('orders', None)
        if not state[symbol]:
            state.pop(symbol, None)


def clear_ladders(acct_conf, state: dict):
    """撤掉丁针挂过的所有止盈单，为主程序调仓让路。已确认成交的先发通知，再清空止盈相关状态。
    止损条件单不在这里处理——它是交易所侧的保险，不跟着止盈单一起提前撤，撤销/重建的时机见
    run_cycle 里 clear_stop_orders 的调用位置和注释。"""
    if not state:
        return

    if DRY_RUN:
        logger.info(f'[干跑] 将撤单清场：{list(state.keys())} —— 未真实撤单')
        _clear_ladder_keys(state)
        return

    open_orders_df = get_open_swap_orders(acct_conf)
    open_order_ids = set(open_orders_df['orderId']) if not open_orders_df.empty else set()

    for symbol, sym_state in state.items():
        for order in sym_state.get('orders', []):
            if _resolve_order(acct_conf, symbol, order, open_order_ids) == 'filled':
                notify_fill(acct_conf, symbol, order)

    symbols = [s for s, v in state.items() if v.get('orders')]
    if symbols:
        acct_conf.bn.cancel_all_swap_orders(symbol_list=symbols)
        logger.info(f'丁针已撤单清场：{symbols}')
    _clear_ladder_keys(state)


def rebuild_ladders(acct_conf, state: dict):
    """按当前真实持仓重新计算并挂出整条阶梯止盈单"""
    position_df = acct_conf.bn.get_swap_position_df()
    if position_df.empty:
        logger.info('丁针重建：当前无合约持仓，无需挂止盈单')
        return

    market_info = acct_conf.bn.get_market_info('swap', require_update=True)

    for symbol, row in position_df.iterrows():
        try:
            position_amt = float(row['当前持仓量'])
            price_precision = market_info['price_precision'].get(symbol, 4)
            qty_precision = market_info['min_qty'].get(symbol, 4)
            min_notional = market_info['min_notional'].get(symbol, 5)

            hour_open_price = get_current_hour_open(acct_conf, symbol)
            if hour_open_price is None:
                logger.error(f'{symbol} 拿不到本小时开盘价，本周期跳过挂单')
                continue

            plan = build_ladder_plan(hour_open_price, position_amt, price_precision, qty_precision)
            if not plan:
                logger.info(f'{symbol} 仓位过小，本周期不挂止盈单')
                continue

            orders = []
            for rung in plan:
                if rung['qty'] * rung['price'] < min_notional:
                    logger.warning(f'{symbol} 第{rung["level"] * 100:.0f}%档止盈单金额低于最小下单额，跳过')
                    continue
                order = place_rung_order(acct_conf, symbol, rung['side'], rung['qty'], rung['price'], rung['level'])
                if order is not None:
                    orders.append(order)

            if orders:
                # 用 setdefault 合并写入而不是整体覆盖 state[symbol]——这一步执行时同一symbol
                # 可能已经有本轮 rebuild_stop_orders 尚未写入、但上一轮遗留的 'stop' 子键
                # （理论上 clear_ladders 只清 'orders'，'stop' 应该还在），整体覆盖会把它冲掉。
                state.setdefault(symbol, {})['orders'] = orders
                verb = '（干跑）计算出' if DRY_RUN else '已挂出'
                logger.ok(f'{symbol} {verb} {len(orders)} 笔阶梯止盈单，本小时开盘价 {hour_open_price}')
        except Exception as e:
            logger.error(f'{symbol} 重建阶梯止盈单出错：{e}')
            logger.debug(traceback.format_exc())


# ====================================================================================================
# ** 与主程序错时调度 **
# ====================================================================================================
def next_main_run_time(hour_offset_minute: int, after: datetime = None) -> datetime:
    """计算下一个『主程序应该完成下单』的整点+offset时刻"""
    after = after or datetime.now()
    candidate = after.replace(minute=hour_offset_minute, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(hours=1)
    return candidate


def completion_file_path(acct_conf, run_time: datetime):
    """主程序本轮调仓完成的信号文件：core/real_trading.py: run_by_account 最后一步的落盘产物"""
    filename = run_time.strftime('%Y%m%d_%H') + '.csv'
    return get_file_path('data', acct_conf.name, '账户换仓信息', filename, as_path_type=True, auto_create=False)


def wait_for_completion(acct_conf, run_time: datetime, deadline: datetime) -> bool:
    """轮询调仓完成信号。调用方在此之前已经 clear_ladders 清空了 state 里的止盈'orders'子键
    （止损条件单仍然照常挂在交易所，state 里对应的'stop'子键也还在），这段等待期间没有止盈挂单
    需要巡检，所以这里只是单纯等待，不调用 health_check。"""
    path = completion_file_path(acct_conf, run_time)
    while datetime.now() < deadline:
        if path.exists():
            return True
        time.sleep(POLL_SECONDS)
    return False


# ====================================================================================================
# ** is_crash 期间合约多头单K止损 **
# ====================================================================================================
def clear_stop_orders(acct_conf, state: dict):
    """撤掉上一小时挂的全部止损条件单。先按 state 里记录的 algoId 精确撤，再用交易所侧的条件单
    列表兜底核对——万一 state 没保存成功（比如上次进程在写文件前崩溃），孤儿止损单会一直挂在账户上，
    它的 closePosition=true 是认 symbol 不认方向的，如果这个 symbol 下一小时反手做空，孤儿的多头止损单
    会在错误的方向上触发平仓，所以这里的兜底扫描不是可选项。
    这个函数只清账户里现存的条件单，不判断是否应该重新挂——重新挂的判断在 rebuild_stop_orders。"""
    if DRY_RUN:
        symbols = [s for s, v in state.items() if v.get('stop')]
        if symbols:
            logger.info(f'[干跑] 将撤销止损单：{symbols} —— 未真实撤单')
        for sym_state in state.values():
            sym_state.pop('stop', None)
        return

    for symbol in list(state.keys()):
        sym_state = state[symbol]
        stop = sym_state.pop('stop', None)
        if stop and stop.get('algo_id'):
            acct_conf.bn.cancel_swap_algo_order(symbol, algo_id=stop['algo_id'])
        if not sym_state:
            state.pop(symbol, None)

    # 兜底：扫描账户里所有还挂着的条件单，防止 state 丢失导致孤儿单遗留到下一小时
    leftover = acct_conf.bn.get_swap_algo_open_orders()
    for order in leftover:
        symbol, algo_id = order.get('symbol'), order.get('algoId')
        if symbol and algo_id:
            logger.warning(f'{symbol} 发现孤儿止损条件单 {algo_id}（state 中未记录），一并撤销')
            acct_conf.bn.cancel_swap_algo_order(symbol, algo_id=algo_id)


def rebuild_stop_orders(acct_conf, state: dict):
    """按当前真实持仓，对净持仓为多头且命中 is_crash 的币重新挂止损条件单。
    必须在 clear_stop_orders 之后调用——顺序颠倒会导致新一轮的止损单被自己紧接着撤掉。"""
    position_df = acct_conf.bn.get_swap_position_df()
    if position_df.empty:
        logger.info('止损重建：当前无合约持仓，跳过')
        return

    market_info = acct_conf.bn.get_market_info('swap', require_update=True)

    for symbol, row in position_df.iterrows():
        try:
            position_amt = float(row['当前持仓量'])
            if position_amt <= 0:
                continue  # 只做多头止损，空头/无持仓不处理

            closes = get_recent_closed_closes(acct_conf, symbol, CRASH_WINDOW)
            if not closes or not is_crash_symbol(closes):
                continue

            price_precision = market_info['price_precision'].get(symbol, 4)
            plan = build_stop_plan(closes[-1], position_amt, price_precision)
            if plan is None:
                continue

            order = place_stop_order(acct_conf, symbol, plan['side'], plan['stop_price'])
            if order is None:
                logger.error(f'{symbol} 止损条件单下单失败，本周期该币无止损保护')
                continue

            state.setdefault(symbol, {})['stop'] = order
            verb = '（干跑）计算出' if DRY_RUN else '已挂出'
            logger.ok(f'{symbol} is_crash 命中，{verb}止损单，stopPrice={plan["stop_price"]}')
        except Exception as e:
            logger.error(f'{symbol} 重建止损单出错：{e}')
            logger.debug(traceback.format_exc())


def health_check_stop_orders(acct_conf, state: dict):
    """止损条件单巡检：跟丁针止盈单不同，止损单不需要"被撤就挂回"（重挂的判断依赖当时的持仓和
    is_crash 状态，只在整点 rebuild_stop_orders 里做一次即可，没必要在巡检里高频重算）。
    这里做两件事：1）尽早发现某张止损单"消失了"，区分是正常触发（仓位已平）还是异常丢失
    （仓位还在但保护没了），分别发不同的告警内容；2）如果是正常触发，主动撤掉这个symbol上
    残留的止盈限价单（丁针的ladder orders）——不假设币安会在仓位归零时自动撤掉同symbol的
    reduceOnly止盈单（这个行为没有实测验证过，不能把安全性建立在一个没验证的假设上）。
    即使交易所真的会自动撤，这里的主动撤单也只是幂等地清空一个已经不存在的挂单，无副作用；
    如果交易所不会自动撤，不主动清理的话，这些残留的reduceOnly止盈单要等到下一次整点
    clear_ladders才会被清掉，最多裸奔近1小时——虽然reduceOnly本身保证它们不会在没有持仓时
    被动成交、扩大不该有的仓位，但没必要留着这个不确定性。"""
    symbols_with_stop = [s for s, v in state.items() if v.get('stop', {}).get('algo_id')]
    if not symbols_with_stop:
        return

    open_algo_orders = acct_conf.bn.get_swap_algo_open_orders()
    open_algo_ids = {str(o.get('algoId')) for o in open_algo_orders}

    for symbol in symbols_with_stop:
        stop = state[symbol]['stop']
        if str(stop['algo_id']) in open_algo_ids:
            continue

        position_df = acct_conf.bn.get_swap_position_df()
        position_amt = float(position_df.loc[symbol, '当前持仓量']) if symbol in position_df.index else 0.0
        if position_amt <= 0:
            msg = f'【止损触发】{symbol} 止损条件单已消失，持仓已平（stopPrice={stop["stop_price"]}）'
            logger.ok(msg)
            if state[symbol].get('orders'):
                logger.info(f'{symbol} 止损已把仓位平掉，主动撤销该symbol残留的止盈限价单，不等下一整点')
                acct_conf.bn.cancel_all_swap_orders(symbol_list=[symbol])
                state[symbol].pop('orders', None)
        else:
            msg = (f'【止损异常】{symbol} 止损条件单消失但仓位仍在（{position_amt}），'
                  f'可能被误撤，等待下一次整点重建恢复保护')
            logger.warning(msg)
        send_wechat_work_msg(msg, acct_conf.wechat_webhook_url)

        state[symbol].pop('stop', None)
        if not state[symbol].get('orders') and not state[symbol].get('stop'):
            state.pop(symbol, None)


def run_cycle(acct_conf, state: dict, run_time: datetime):
    cancel_at = run_time - timedelta(minutes=CANCEL_LEAD_MINUTES)
    deadline = run_time + timedelta(minutes=WAIT_TIMEOUT_MINUTES)

    # ---- 挂单存续期：巡检直到临近主程序下单时刻 ----
    while datetime.now() < cancel_at:
        health_check(acct_conf, state)
        time.sleep(POLL_SECONDS)

    # ---- 清场，给主程序让路 ----
    logger.info(f'距离 {run_time:%Y-%m-%d %H:%M} 主程序调仓还剩 {CANCEL_LEAD_MINUTES} 分钟，丁针清场')
    clear_ladders(acct_conf, state)
    # 注意：止损条件单不在这里清场。止盈单让路是为了不占用主程序调仓时的下单额度；
    # 止损单是保险，提前撤掉会让调仓这几分钟的窗口裸奔在没有保护的状态下，所以保留到调仓完成、
    # 新持仓确定之后，再跟 rebuild_stop_orders 一起统一撤旧挂新（见下方）。

    # ---- 等待调仓完成信号 ----
    got_signal = wait_for_completion(acct_conf, run_time, deadline)
    if got_signal:
        logger.info(f'检测到 {run_time:%Y-%m-%d %H:%M} 调仓完成信号，静置 {SETTLE_SECONDS}s 后重建阶梯止盈单')
        time.sleep(SETTLE_SECONDS)
    else:
        msg = f'丁针等待 {run_time:%Y-%m-%d %H:%M} 调仓完成信号超时（{WAIT_TIMEOUT_MINUTES}分钟），按当前持仓直接重建止盈单'
        logger.warning(msg)
        if not DRY_RUN:
            send_wechat_work_msg(msg, acct_conf.wechat_webhook_url)

    # ---- 按当前真实持仓重建整条阶梯 ----
    rebuild_ladders(acct_conf, state)

    # ---- 止损条件单：先撤旧的（此时新仓位已经确定），再按新持仓判定 is_crash 挂新的 ----
    # 顺序不可颠倒：先撤后挂，否则旧的 closePosition=true 条件单可能作用在调仓后的新仓位/新方向上。
    if STOP_LOSS_ENABLED:
        clear_stop_orders(acct_conf, state)
        rebuild_stop_orders(acct_conf, state)

    if DRY_RUN:
        logger.info(f'[干跑] 本周期计算结果（不落盘）：{json.dumps(state, ensure_ascii=False, indent=2)}')
    else:
        save_state(acct_conf.name, state)


def main(account_name: str):
    acct_conf, _ = init_system(account_name)
    tag = '（干跑/DRY-RUN，不会真实下单撤单）' if DRY_RUN else ''
    divider(f'🎯 丁针阶梯止盈启动（错时挂单版）{tag} [{acct_conf.name}]', '+')

    state = {} if DRY_RUN else load_state(acct_conf.name)
    run_time = next_main_run_time(acct_conf.hour_offset_minute)
    logger.info(f'下一个调仓时刻：{run_time}')

    while True:
        run_cycle(acct_conf, state, run_time)
        run_time = next_main_run_time(acct_conf.hour_offset_minute, after=run_time)
        logger.info(f'下一个调仓时刻：{run_time}')


if __name__ == '__main__':
    args = sys.argv[1:]
    DRY_RUN = '--dry-run' in args
    args = [a for a in args if a != '--dry-run']

    if not args:
        logger.error('❌ 请传入账户名，例如：python dingzhen.py 0m超混 [--dry-run]')
        sys.exit(1)

    account_name = args[0]

    while True:
        try:
            main(account_name)
        except Exception as err:
            msg = '丁针系统出错，10s之后重新运行，出错原因: ' + str(err)
            logger.error(msg)
            logger.debug(traceback.format_exc())
            send_wechat_work_msg(msg, error_webhook_url)
            time.sleep(10)
