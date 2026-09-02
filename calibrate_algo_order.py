#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性校准脚本：验证 core/binance/base_client.py 新增的合约条件单封装
（place_swap_algo_order / get_swap_algo_open_orders / cancel_swap_algo_order）
能否正确对接币安 2025-12-09 强制迁移后的新版 Algo Order 接口（/fapi/v1/algoOrder）。

这是 is_crash 期间单K止损功能上线前必须先跑一次的手动校准（详见
~/.claude/plans/iscrash-ai-is-crash-mellow-gosling.md 里"接口参数校准"那一步）——
新接口用的 workingType/positionSide/closePosition 等参数名和取值都是照官方文档写的，
没有在真实账户上跑过，需要用一次真实调用来确认参数名对不对、返回字段叫什么。

用法：
    python calibrate_algo_order.py <账户名> [symbol]
        <账户名>  对应 实盘/accounts/ 目录下的账户配置文件名（不含 .py 后缀）
        [symbol]  可选，指定要测试的合约symbol（如 BTCUSDT）。不传则自动挑当前账户
                  持仓量最大的一个多头币种。

安全说明：
    - 会在选定symbol上挂一张真实的 STOP_MARKET 条件单，stopPrice 设在当前价的 50% 以下，
      正常行情下不可能触发，脚本跑完会立刻撤掉它，不会有资金风险。
    - 执行前会要求手动确认（输入 yes），不会静默下单。
    - 全程只做"挂单 -> 查询 -> 撤单"验证，不改变任何实际持仓。

跑完后请把完整输出发回来，用于确认/修正 base_client.py 里的参数名（如 type vs orderType、
返回的 algoId 字段名等），再决定要不要打开 dingzhen.py 里的 STOP_LOSS_ENABLED。
"""
import sys

from core.account_manager import init_system
from core.utils.log_kit import logger, divider


def pick_test_symbol(bn) -> tuple:
    """自动挑一个当前有多头持仓的symbol用于测试，返回 (symbol, position_amt, mark_price)"""
    position_df = bn.get_swap_position_df()
    long_positions = position_df[position_df['当前持仓量'] > 0]
    if long_positions.empty:
        raise RuntimeError('当前账户没有任何多头合约持仓，无法自动挑选测试symbol，请手动传入 symbol 参数')
    # 按持仓名义价值降序，选价值最大的（避免选到快要被平掉的碎仓）
    row = long_positions.sort_values('仓位价值', ascending=False).iloc[0]
    symbol = long_positions.sort_values('仓位价值', ascending=False).index[0]
    return symbol, float(row['当前持仓量']), float(row['当前标记价格'])


def main(account_name: str, symbol: str = None):
    divider(f'🧪 Algo Order 接口校准 [{account_name}]', '+')
    acct_conf, _ = init_system(account_name)
    bn = acct_conf.bn

    if symbol:
        position_df = bn.get_swap_position_df()
        if symbol not in position_df.index or float(position_df.loc[symbol, '当前持仓量']) <= 0:
            logger.error(f'{symbol} 当前不是多头持仓，换一个symbol或不传参数自动挑选')
            return
        mark_price = float(position_df.loc[symbol, '当前标记价格'])
    else:
        symbol, position_amt, mark_price = pick_test_symbol(bn)
        logger.info(f'自动选中测试symbol：{symbol}（当前多头持仓 {position_amt}，标记价 {mark_price}）')

    market_info = bn.get_market_info('swap', require_update=True)
    price_precision = market_info['price_precision'].get(symbol, 4)
    stop_price = round(mark_price * 0.5, price_precision)  # 当前价 50% 以下，正常行情不会触发

    print(f'\n即将在 {symbol} 上挂一张测试用 STOP_MARKET 条件单：')
    print(f'  side=SELL, stopPrice={stop_price}（当前标记价 {mark_price} 的 50%），closePosition=true')
    print(f'  验证完成后会立刻撤销，不会真正平仓。')
    confirm = input('\n确认执行？输入 yes 继续，其他任意键取消：').strip().lower()
    if confirm != 'yes':
        logger.info('已取消，未做任何操作')
        return

    results = {}

    # ---- 1. 下单 ----
    divider('1. 下单 place_swap_algo_order', '-')
    order_res = bn.place_swap_algo_order(symbol, 'SELL', stop_price, close_position=True,
                                         working_type='CONTRACT_PRICE')
    print(f'原始返回：{order_res}')
    algo_id = order_res.get('algoId') or order_res.get('clientAlgoId')
    results['下单成功，返回 algoId'] = bool(algo_id)
    if not algo_id:
        logger.error('下单没有返回 algoId/clientAlgoId，后续步骤无法继续，请检查上面的原始返回排查参数问题')
        print_summary(results)
        return
    logger.ok(f'下单成功，algoId={algo_id}')

    # 从这里开始任何一步都可能抛异常（比如 cancel_all_swap_orders 内部 retry_wrapper 耗尽重试后
    # 直接 raise）。上一次校准就是卡在中间某一步导致脚本整体退出，测试单一直挂在账户上，
    # 靠人工手动撤掉的——所以这里全程包一层 try/finally，不管中途哪一步炸了，
    # finally 都会补一次撤单，不依赖流程走到第4步才清理。
    cleaned_up = False
    try:
        # ---- 2. 查询 ----
        divider('2. 查询 get_swap_algo_open_orders', '-')
        open_orders = bn.get_swap_algo_open_orders(symbol)
        print(f'原始返回：{open_orders}')
        found = any(str(o.get('algoId')) == str(algo_id) for o in open_orders)
        results['查询能看到刚下的条件单'] = found
        if found:
            logger.ok('查询到刚下的条件单')
        else:
            logger.warning('没有在挂单列表里查到刚下的条件单，字段名可能对不上（比如不是 algoId），看上面原始返回')

        # ---- 3. 验证 cancel_all_swap_orders 撤不掉它（隔离性） ----
        divider('3. 验证普通撤单接口不会误撤条件单', '-')
        bn.cancel_all_swap_orders(symbol_list=[symbol])
        open_orders_after = bn.get_swap_algo_open_orders(symbol)
        still_there = any(str(o.get('algoId')) == str(algo_id) for o in open_orders_after)
        results['cancel_all_swap_orders 未误撤条件单'] = still_there
        if still_there:
            logger.ok('cancel_all_swap_orders 确认撤不掉条件单，两套挂单命名空间是隔离的')
        else:
            logger.error('condition单被 cancel_all_swap_orders 误撤了！这会导致主循环调仓时把止损单一起撤掉，'
                         '需要重新设计撤单隔离方案，不能直接用现在这套')

        # ---- 4. 撤销 ----
        divider('4. 撤销 cancel_swap_algo_order', '-')
        cancel_res = bn.cancel_swap_algo_order(symbol, algo_id=algo_id)
        print(f'原始返回：{cancel_res}')
        open_orders_final = bn.get_swap_algo_open_orders(symbol)
        gone = not any(str(o.get('algoId')) == str(algo_id) for o in open_orders_final)
        results['撤销后确认已清除'] = gone
        cleaned_up = gone
        if gone:
            logger.ok('撤销成功，已确认清除')
        else:
            logger.error('撤销后仍能查到该条件单，请手动登录交易所APP/网页确认并手动撤销！')
    finally:
        if not cleaned_up:
            logger.warning(f'安全网触发：补一次撤单，避免中途异常导致孤儿单遗留（algoId={algo_id}）')
            try:
                bn.cancel_swap_algo_order(symbol, algo_id=algo_id)
                logger.ok('安全网撤单已发出（不保证一定成功，建议手动去交易所确认一遍）')
            except Exception as cleanup_err:
                logger.error(f'安全网撤单也失败了，请务必手动登录交易所检查并撤销 algoId={algo_id}：{cleanup_err}')

    print_summary(results)


def print_summary(results: dict):
    divider('📋 校准结果汇总', '=')
    for desc, ok in results.items():
        print(f'  {"✅" if ok else "❌"} {desc}')
    if all(results.values()):
        logger.ok('全部通过，接口参数校准无误，可以考虑打开 dingzhen.py 里的 STOP_LOSS_ENABLED')
    else:
        logger.error('存在未通过项，请把完整输出发回去分析，不要直接打开 STOP_LOSS_ENABLED')


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        logger.error('❌ 请传入账户名，例如：python calibrate_algo_order.py 0m超混 [symbol]')
        sys.exit(1)
    main(args[0], args[1] if len(args) > 1 else None)
