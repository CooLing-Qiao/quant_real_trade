#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
测试：主框架的撤单函数（core/real_trading.py 里调用的 acct_conf.bn.cancel_all_swap_orders()）
能不能撤掉丁针（dingzhen.py）挂出的止盈单。

背景：dingzhen.py 撤自己的止盈单用的是 acct_conf.bn.cancel_all_swap_orders(symbol_list=symbols)，
主框架 core/real_trading.py:76 撤单用的是 acct_conf.bn.cancel_all_swap_orders()（不传 symbol_list，
撤全部）—— 两边调用的是同一个方法。这个方法本质是按 symbol 调交易所的"撤销该symbol所有挂单"接口
（cancel_swap_open_orders_api），这个接口在交易所侧是不区分"谁下的单"的，同一个 symbol 下不管是
丁针下的还是主框架自己下的挂单，只要在这个 symbol 上，一次调用全部撤掉。

用法：python test_cancel_conflict.py <账户名>

流程（全程只操作一个非常小、价格远离盘口的测试单，不影响真实仓位）：
    1. 用跟丁针完全一样的方式挂一笔小额限价单（LIMIT/GTC），价格刻意设置在远离现价的地方，
       确保不会成交。
    2. 查询挂单确认它在交易所侧确实存在。
    3. 调用主框架实际在用的 acct_conf.bn.cancel_all_swap_orders()（不传 symbol_list，跟
       core/real_trading.py:76 完全一样的调用）。
    4. 再次查询挂单，确认这笔单是否被撤掉。
    5. 无论上一步是否成功，最后都用同一个撤单函数再兜底清一次场，确保不会有残留测试单挂在账户上
       （dingzhen 就算这中间被撤过一次也无所谓，它下一轮会按当时的真实持仓自动重建）。
"""

import sys
import time

sys.path.append('.')

from core.account_manager import init_system
from core.utils.log_kit import logger, divider


def get_open_swap_orders(acct_conf):
    bn = acct_conf.bn
    get_open_orders_func = getattr(bn.exchange, bn.constants.get('get_swap_open_orders_api'))
    from core.utils.commons import retry_wrapper
    orders = retry_wrapper(get_open_orders_func, params={'timestamp': ''}, func_name='测试脚本查询合约挂单')
    return orders or []


def main(account_name: str):
    acct_conf, _ = init_system(account_name)
    bn = acct_conf.bn
    divider(f'🧪 测试主框架撤单函数能否撤掉丁针风格的挂单 [{acct_conf.name}]', '+')

    symbol = 'BTCUSDT'
    market_info = bn.get_market_info('swap', require_update=True)
    price_precision = market_info['price_precision'].get(symbol, 1)
    qty_precision = market_info['min_qty'].get(symbol, 3)
    min_notional = market_info['min_notional'].get(symbol, 100)

    ticker = bn.fetch_swap_ticker_price(symbol)
    mark_price = float(ticker['price'])

    # 测试单价格设在现价的 40%，BUY 方向，正常行情下几秒内不可能跌这么多，不会成交
    test_price = round(mark_price * 0.4, price_precision)
    qty = round(max(min_notional / test_price * 1.05, 10 ** (-qty_precision)), qty_precision)

    logger.info(f'{symbol} 现价约 {mark_price}，测试单价格 {test_price}（远离现价，不会成交），数量 {qty}')

    # ---- 第1步：像丁针一样挂一笔限价单（LIMIT/GTC）----
    order_res = bn.place_swap_order(
        symbol=symbol,
        side='BUY',
        quantity=qty,
        price=test_price,
        type='LIMIT',
        timeInForce='GTC',
    )
    order_id = order_res.get('orderId') if order_res else None
    if not order_id:
        logger.error('❌ 测试单下单失败，无法继续测试')
        return
    order_id = int(order_id)
    logger.ok(f'测试单已挂出，orderId={order_id}')

    try:
        # ---- 第2步：确认挂单确实存在 ----
        time.sleep(1)
        open_orders = get_open_swap_orders(acct_conf)
        open_ids_before = {int(o['orderId']) for o in open_orders}
        if order_id not in open_ids_before:
            logger.error(f'❌ 挂单后查询不到该订单（orderId={order_id}），测试无法继续')
            return
        logger.ok(f'确认测试单在交易所挂单列表中：{order_id}')

        # ---- 第3步：调用主框架实际在用的撤单函数（不传symbol_list，跟 real_trading.py:76 一致）----
        divider('调用 acct_conf.bn.cancel_all_swap_orders()（主框架撤单调用）', '-')
        bn.cancel_all_swap_orders()

        # ---- 第4步：再次查询，确认是否被撤掉 ----
        time.sleep(1)
        open_orders_after = get_open_swap_orders(acct_conf)
        open_ids_after = {int(o['orderId']) for o in open_orders_after}

        if order_id in open_ids_after:
            logger.error(f'❌ 结论：主框架的 cancel_all_swap_orders() 没有撤掉这笔测试单（orderId={order_id} 仍存在）')
        else:
            logger.ok(f'✅ 结论：主框架的 cancel_all_swap_orders() 成功撤掉了这笔测试单（orderId={order_id}）')
            logger.ok('说明主框架撤单函数与丁针挂单是同一 symbol 维度的交易所接口，二者互相"看得见"，'
                       '主框架调仓时的撤单会连丁针挂的止盈单一起撤掉。')
    finally:
        # ---- 兜底清场：万一上面判断有误，确保不留残留测试单 ----
        bn.cancel_all_swap_orders(symbol_list=[symbol])
        divider('测试结束', '+')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        logger.error('用法：python test_cancel_conflict.py <账户名>')
        sys.exit(1)
    main(sys.argv[1])
