"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import time
import traceback
from datetime import datetime

import pandas as pd

from config import utc_offset
from core.binance.base_client import BinanceClient
from core.utils.commons import retry_wrapper, apply_precision
from core.utils.log_kit import logger


class StandardClient(BinanceClient):
    constants = dict(
        spot_account_type='SPOT',
        reset_page_leverage_api='fapiprivate_post_leverage',
        get_leverage_bracket_api='fapiprivate_get_leveragebracket',
        get_swap_position_api='fapiprivatev2_get_positionrisk',
        get_spot_open_orders_api='private_get_openorders',
        cancel_spot_open_orders_api='private_delete_openorders',
        get_swap_open_orders_api='fapiprivate_get_openorders',
        cancel_swap_open_orders_api='fapiprivate_delete_allopenorders',
        get_spot_my_trades_api='private_get_mytrades',
    )

    def __init__(self, **config):
        super().__init__(**config)
        self.is_pure_long: bool = config.get('is_pure_long', False)

    def transfer_u_from_spot_to_swap(self, amount, asset='USDT'):
        logger.info(f'从现货划转U到U本位合约: {amount} {asset}')
        if amount <= 0:
            logger.warning(f'当前划转金额【{amount}】小于0，本次不进行划转。(不是错误不要担心)')
            return

        if self.is_pure_long and asset == 'USDT':
            logger.warning('纯多策略不划转钱到合约')
            return

        params = {
            'type': 'MAIN_UMFUTURE',
            'asset': asset,
            'amount': str(amount),
            'timestamp': '',
        }
        transfer_info = retry_wrapper(self.exchange.sapi_post_asset_transfer, params=params,
                                      func_name='BN现货账户转U去U本位合约账户')
        logger.info(f'BN 资金转移 {asset} 现货账户 => U本位合约账户 接口返回: {transfer_info}')
        logger.ok(f'BN 资金转移 {asset} 现货账户 => U本位合约账户 资金量: {amount}')

    def transfer_u_from_swap_to_spot(self, amount, asset='USDT'):
        """
        BN U本位合约账户 转U 到 现货账户
        :param amount:      划转资金数量
        :param asset:       划转资产
        """
        logger.info(f'从U本位合约划转U到现货: {amount} {asset}')
        if amount <= 0:
            logger.warning(f'当前划转金额【{amount}】小于0，本次不进行划转。(不是错误不要担心)')
            return

        params = {
            'type': 'UMFUTURE_MAIN',
            'asset': asset,
            'amount': str(amount),
            'timestamp': '',
        }
        transfer_info = retry_wrapper(
            self.exchange.sapi_post_asset_transfer, params=params,
            func_name='U本位合约账户转U去现货账户'
        )
        logger.info(f'BN 资金转移 {asset} U本位合约账户 => 现货账户 接口返回: {transfer_info}')
        logger.ok(f'BN 资金转移 {asset} U本位合约账户 => 现货账户 资金量: {amount}')

    def _set_position_side(self, dual_side_position=False):
        """
        检查是否是单向持仓模式
        """
        params = {'dualSidePosition': 'true' if dual_side_position else 'false', 'timestamp': ''}
        retry_wrapper(self.exchange.fapiprivate_post_positionside_dual, params=params,
                      func_name='fapiprivate_post_positionside_dual', if_exit=False)
        logger.info('修改持仓模式为单向持仓')

    def set_single_side_position(self):

        # 查询持仓模式
        res = retry_wrapper(
            self.exchange.fapiprivate_get_positionside_dual, params={'timestamp': ''}, func_name='设置单向持仓',
            if_exit=False
        )

        is_duel_side_position = bool(res['dualSidePosition'])

        # 判断是否是单向持仓模式
        if is_duel_side_position:  # 若当前持仓模式不是单向持仓模式，则调用接口修改持仓模式为单向持仓模式
            self._set_position_side(dual_side_position=False)

    def set_duel_side_position(self):
        # 查询持仓模式
        res = retry_wrapper(
            self.exchange.fapiprivate_get_positionside_dual, params={'timestamp': ''}, func_name='设置单向持仓',
            if_exit=False
        )

        is_duel_side_position = bool(res['dualSidePosition'])

        # 判断是否是单向持仓模式
        if not is_duel_side_position:  # 若当前持仓模式不是单向持仓模式，则调用接口修改持仓模式为单向持仓模式
            self._set_position_side(dual_side_position=True)

    def set_multi_assets_margin(self):
        """
        检查是否开启了联合保证金模式
        """
        # 查询保证金模式
        res = retry_wrapper(self.exchange.fapiprivate_get_multiassetsmargin, params={'timestamp': ''},
                            func_name='fapiprivate_get_multiassetsmargin', if_exit=False)
        # 判断是否开启了联合保证金模式
        if not bool(res['multiAssetsMargin']):  # 若联合保证金模式没有开启，则调用接口开启一下联合保证金模式
            params = {'multiAssetsMargin': 'true', 'timestamp': ''}
            retry_wrapper(self.exchange.fapiprivate_post_multiassetsmargin, params=params,
                          func_name='fapiprivate_post_multiassetsmargin', if_exit=False)
            logger.ok('开启联合保证金模式')

    def get_account_overview(self):
        spot_ticker_data = self.fetch_spot_ticker_price()
        spot_ticker = {_['symbol']: float(_['price']) for _ in spot_ticker_data}

        swap_account = self.get_swap_account()
        equity = pd.DataFrame(swap_account['assets'])
        equity['walletBalance'] = equity['walletBalance'].astype(float)

        # 稳定币（USDT/USDC 等 base_coins）按原始余额 1:1 计入，不存在折价问题
        swap_usdt_balance = 0
        for base_coin in self.base_coins:
            if base_coin in equity['asset'].to_list():
                base_coin_amount = equity.loc[equity['asset'] == base_coin, 'walletBalance'].iloc[0]
                swap_usdt_balance += base_coin_amount
                self.base_coin_amount[base_coin] = base_coin_amount or 0

        # 联合保证金模式下（startup.py 每次启动都会调用 set_multi_assets_margin() 开启），
        # 币安算 totalWalletBalance 时会对 BTC/ETH/BNB 等非稳定币资产按折算率（约 90%~95%，稳定币之外都有折价）打折。
        # 这里不用 totalWalletBalance，而是对每个 marginAvailable=True 的非稳定币资产，
        # 用原始持仓数量（walletBalance，币本位）× 实时合约价格，按 100% 真实市值计入，不采用币安的折算率
        swap_ticker_data = self.fetch_swap_ticker_price()
        swap_ticker = {_['symbol']: float(_['price']) for _ in swap_ticker_data}
        other_assets = equity[
            (~equity['asset'].isin(self.base_coins)) &
            (equity['walletBalance'] != 0) &
            (equity['marginAvailable'] == True)  # noqa: E712
        ]
        for _, row in other_assets.iterrows():
            price = swap_ticker.get(f"{row['asset']}USDT", 0)
            if price == 0:
                logger.warning(f"未找到 {row['asset']} 的实时合约价格，无法按 100% 市值计入联合保证金，已忽略")
                continue
            swap_usdt_balance += row['walletBalance'] * price

        # 联合保证金（coin_margin）历史配置已被上面按实时价格计算的逻辑取代，若仍配置会导致重复计算，直接告警提醒
        if self.coin_margin:
            logger.warning(f'检测到【coin_margin】仍有配置：{self.coin_margin}，'
                            f'该配置已被自动按实时价格计算联合保证金取代，不再生效，请从账户配置文件中移除。')

        swap_position_df = self.get_swap_position_df()
        spot_position_df = self.get_spot_position_df()
        logger.ok('获取账户资产数据完成')

        logger.info('准备处理资产数据...')
        # 获取当前账号现货U的数量
        spot_usdt_balance = 0
        # 获取当前账号现货U的数量
        for base_coin in self.base_coins:
            if base_coin in spot_position_df['symbol'].to_list():
                base_coin_amount = spot_position_df.loc[spot_position_df['symbol'] == base_coin, '当前持仓量'].iloc[0]
                spot_usdt_balance += base_coin_amount
                self.base_coin_amount[base_coin] += (base_coin_amount or 0)
        # 去除掉USDT,USDC现货
        spot_position_df = spot_position_df[~spot_position_df['symbol'].isin(self.base_coins)]
        # 追加USDT后缀，方便计算usdt价值
        spot_position_df.loc[spot_position_df['symbol'] != 'USDT', 'symbol'] = spot_position_df['symbol'] + 'USDT'
        spot_position_df['仓位价值'] = spot_position_df.apply(
            lambda row: row['当前持仓量'] * spot_ticker.get(row["symbol"], 0), axis=1)

        # 过滤掉不含报价的币
        spot_position_df = spot_position_df[spot_position_df['仓位价值'] != 0]
        # 仓位价值 小于 5U，无法下单的碎币，单独记录
        dust_spot_df = spot_position_df[spot_position_df['仓位价值'] < 5]
        # 过滤掉仓位价值 小于 5U
        spot_position_df = spot_position_df[spot_position_df['仓位价值'] > 5]
        # 过滤掉BNB，用于抵扣手续费，不参与现货交易
        spot_position_df = spot_position_df[spot_position_df['symbol'] != 'BNBUSDT']

        # 去除掉已经下架的币种，避免下架币的资金影响到当前账户的配置
        if 'spot' not in self.market_info:
            self.get_market_info(symbol_type='spot')
        spot_position_df = spot_position_df[spot_position_df['symbol'].isin(self.market_info['spot']['symbol_list'])]

        # 现货净值
        spot_equity = spot_position_df['仓位价值'].sum() + spot_usdt_balance

        # 持仓盈亏
        account_pnl = swap_position_df['持仓盈亏'].sum()

        # =====处理现货持仓列表信息
        # 构建币种的balance信息
        # 币种 : 价值
        spot_assets_pos_dict = spot_position_df[['symbol', '仓位价值']].to_dict(orient='records')
        spot_assets_pos_dict = {_['symbol']: _['仓位价值'] for _ in spot_assets_pos_dict}

        # 币种 : 数量
        spot_asset_amount_dict = spot_position_df[['symbol', '当前持仓量']].to_dict(orient='records')
        spot_asset_amount_dict = {_['symbol']: _['当前持仓量'] for _ in spot_asset_amount_dict}

        # =====处理合约持仓列表信息
        # 币种 : 价值
        swap_position_df.reset_index(inplace=True)
        swap_assets_pos_dict = swap_position_df[['symbol', '仓位价值']].to_dict(orient='records')
        swap_assets_pos_dict = {_['symbol']: _['仓位价值'] for _ in swap_assets_pos_dict}

        # 币种 : 数量
        swap_asset_amount_dict = swap_position_df[['symbol', '当前持仓量']].to_dict(orient='records')
        swap_asset_amount_dict = {_['symbol']: _['当前持仓量'] for _ in swap_asset_amount_dict}

        # 币种 : pnl
        swap_asset_pnl_dict = swap_position_df[['symbol', '持仓盈亏']].to_dict(orient='records')
        swap_asset_pnl_dict = {_['symbol']: _['持仓盈亏'] for _ in swap_asset_pnl_dict}

        # 处理完成之后在设置index
        swap_position_df.set_index('symbol', inplace=True)

        # 账户总净值 = 现货总价值 + 合约usdt + 持仓盈亏
        account_equity = (spot_equity + swap_usdt_balance + account_pnl)

        logger.ok('处理资产数据完成')

        return {
            'usdt_balance': spot_usdt_balance + swap_usdt_balance,
            'negative_balance': 0,
            'account_pnl': account_pnl,
            'account_equity': account_equity,
            'seed_coin_spot_equity': 0,
            'spot_assets': {
                'assets_pos_value': spot_assets_pos_dict,
                'assets_amount': spot_asset_amount_dict,
                'usdt': spot_usdt_balance,
                'equity': spot_equity,
                'dust_spot_df': dust_spot_df,
                'spot_position_df': spot_position_df
            },
            'swap_assets': {
                'assets_pos_value': swap_assets_pos_dict,
                'assets_amount': swap_asset_amount_dict,
                'assets_pnl': swap_asset_pnl_dict,
                'usdt': swap_usdt_balance,
                'equity': swap_usdt_balance + account_pnl,
                'swap_position_df': swap_position_df
            }
        }

    def replenish_bnb(self, buy_bnb_value, is_use_spot=True):
        """
        补充BNB，用于抵扣手续费

        :param buy_bnb_value:   补充价值多少U的bnb
        :param is_use_spot:     是否使用现货模式
        """
        # ===获取当前持仓BNB的价值
        # 获取BNB价格
        logger.info('自动补充BNB...')
        ticker = retry_wrapper(self.exchange.public_get_ticker_price, params={'symbol': 'BNBUSDT'},
                               func_name='获取BNB现货价格')
        price = float(ticker['price'])  # bnb当前现货价格

        # 获取合约BNB数量
        swap_balance = retry_wrapper(self.exchange.fapiprivatev3_get_account, params={'timestamp': ''},
                                     func_name='获取U本位合约账户净值')  # 获取账户净值
        swap_balance = pd.DataFrame(swap_balance['assets'])
        swap_usdt = float(swap_balance[swap_balance['asset'] == 'USDT']['walletBalance'])  # 获取usdt持仓数量
        if swap_usdt < buy_bnb_value:
            logger.warning(f'当前合约账户usdt余额【{swap_usdt:.4f}】不足以购买BNB补充手续费，跳过补充BNB操作')
            return 0

        swap_bnb = float(swap_balance[swap_balance['asset'] == 'BNB']['walletBalance'])  # 获取BNB持仓数量
        swap_bnb_value = swap_bnb * price  # 计算当前BNB价值

        # 获取现货BNB数量
        spot_balance = retry_wrapper(self.exchange.private_get_account, params={'timestamp': ''},
                                     func_name='获取现货账户净值')  # 获取账户净值
        spot_balance = pd.DataFrame(spot_balance['balances'])

        spot_balance['free'] = pd.to_numeric(spot_balance['free'])

        spot_bnb = float(spot_balance[spot_balance['asset'] == 'BNB']['free'])  # 获取BNB持仓数量
        spot_bnb_value = spot_bnb * price  # 计算当前BNB价值
        logger.info(f'当前BNB剩余数量：现货 {spot_bnb_value:.4f} U，合约 {swap_bnb_value:.4f} U')

        # ===构建判断条件和需要购买BNB的价值
        if is_use_spot:  # 现货模式，现货和合约账户都需要进行判断是否购买bnb
            condition = swap_bnb_value + spot_bnb_value < buy_bnb_value  # 判断两边BNB总和是否小于购买量
            all_buy_bnb_value = buy_bnb_value - (swap_bnb_value + spot_bnb_value)  # 现货模式，计算购买合约和现货的bnb
        else:  # 合约模式，只需要判断合约账户是否需要购买bnb
            condition = swap_bnb_value < buy_bnb_value  # 判断合约的BNB是否小于购买量
            all_buy_bnb_value = buy_bnb_value - swap_bnb_value  # 合约模式，计算购买合约的bnb

        # ===判断账户BNB是否 小于 buy_bnb_value
        if condition:
            # =判断购买BNB数量是否满足最小下单量
            if all_buy_bnb_value < 10:
                logger.warning('补充现货和合约BNB总价值不足10U，跳过补充BNB操作')
                all_buy_bnb_value = 0
            else:
                logger.info(f'补充现货和合约BNB总价值 {all_buy_bnb_value:.4f} U，准备开始补充BNB······')
                try:
                    # 划转资金去现货账户
                    self.transfer_u_from_swap_to_spot(all_buy_bnb_value)
                except BaseException as e:
                    logger.error('划转U到现货账户报错，准备尝试消耗现货预留手续费资金...')
                    logger.debug(e)
                    logger.debug(traceback.format_exc())

                # 市价单购买BNB
                ticker = self.exchange.public_get_ticker_price(params={'symbol': 'BNBUSDT'})
                price = float(ticker['price'])
                quantity = all_buy_bnb_value / price * 0.98  # 相当于2%滑点
                quantity = float(f'{quantity:.3f}')  # 根据价格精度调整，这里直接写死了
                params = {'symbol': 'BNBUSDT', 'side': 'BUY', 'type': 'MARKET', 'quantity': str(quantity),
                          'newClientOrderId': str(int(time.time())), 'timestamp': ''}
                logger.debug('购买BNB下单参数: ', params)
                try:
                    res = retry_wrapper(self.exchange.privatePostOrder, params=params, func_name='privatePostOrder')
                    logger.ok('购买BNB下单结果: ', res)
                except BaseException as e:
                    logger.error('购买BNB出错，跳过补充BNB操作')
                    logger.debug(e)
                    logger.debug(traceback.format_exc())
                    return 0

                # 休息一下，让市场消化一下单子
                time.sleep(5)
        else:
            all_buy_bnb_value = 0

        # 平衡BNB
        self.rebalance_bnb(is_use_spot)

        return all_buy_bnb_value

    def rebalance_bnb(self, is_use_spot):
        """
        根据下单模式，来平衡现货账户和合约账户之间的bnb数量

        :param is_use_spot:     是否使用现货模式
        """
        # 获取合约BNB数量
        swap_balance = retry_wrapper(self.exchange.fapiprivatev3_get_account, params={'timestamp': ''},
                                     func_name='获取U本位合约账户净值')  # 获取账户净值
        swap_balance = pd.DataFrame(swap_balance['assets'])
        swap_bnb = float(swap_balance[swap_balance['asset'] == 'BNB']['walletBalance'])  # 获取BNB持仓数量

        # 获取现货BNB数量
        spot_balance = retry_wrapper(self.exchange.private_get_account, params={'timestamp': ''},
                                     func_name='获取现货账户净值')  # 获取账户净值
        spot_balance = pd.DataFrame(spot_balance['balances'])

        spot_balance['free'] = pd.to_numeric(spot_balance['free'])

        spot_bnb = float(spot_balance[spot_balance['asset'] == 'BNB']['free'])  # 获取BNB持仓数量

        # 根据模式，来判断BNB划转
        if is_use_spot:  # 现货模式，设置现货账户与合约账户两边BNB平衡
            bnb_mean = (swap_bnb + spot_bnb) / 2  # 构建两边的平衡点
            transfer_bnb = bnb_mean - swap_bnb  # 需要给合约多少bnb
            transfer_bnb = apply_precision(transfer_bnb, 6)  # 向下取整，保留6位有效数字，划转金额可以比最小下单精度小，这里多延几位
            if transfer_bnb > 0:  # 若大于0，表示需要向合约划转
                # 划转BNB到合约账户
                self.transfer_u_from_spot_to_swap(transfer_bnb, asset='BNB')
            else:  # 若小于0，表示需要向现货划转
                # 划转BNB到现货账户
                self.transfer_u_from_swap_to_spot(abs(transfer_bnb), asset='BNB')
        else:  # 合约模式，直接将现货账户的BNB全部转入合约账户中
            spot_bnb = apply_precision(spot_bnb, 6)  # 向下取整，保留6位有效数字，划转金额可以比最小下单精度小，这里多延几位
            if spot_bnb > 0:
                self.transfer_u_from_spot_to_swap(spot_bnb, asset='BNB')

    def adjust_base_margin(self):
        if not self.base_margin.keys():
            logger.ok(f'未配置额外基础保证金')
            return

        if len(self.base_coins) > 1:
            logger.info(f'普通账户支持其他基础保证金功能，需要划转资金兑换其他稳定币，此时会引起合约账户保证金的波动，增加账户杠杆和风险，因此【普通账户】移除自动兑换功能，仅支持【统一账户】使用')

    def fetch_transfer_history(self, start_time=datetime.now()):
        """
        获取划转记录

        MAIN_UMFUTURE 现货钱包转向U本位合约钱包
        MAIN_MARGIN 现货钱包转向杠杆全仓钱包

        UMFUTURE_MAIN U本位合约钱包转向现货钱包
        UMFUTURE_MARGIN U本位合约钱包转向杠杆全仓钱包

        CMFUTURE_MAIN 币本位合约钱包转向现货钱包

        MARGIN_MAIN 杠杆全仓钱包转向现货钱包
        MARGIN_UMFUTURE 杠杆全仓钱包转向U本位合约钱包

        MAIN_FUNDING 现货钱包转向资金钱包
        FUNDING_MAIN 资金钱包转向现货钱包

        FUNDING_UMFUTURE 资金钱包转向U本位合约钱包
        UMFUTURE_FUNDING U本位合约钱包转向资金钱包

        MAIN_OPTION 现货钱包转向期权钱包
        OPTION_MAIN 期权钱包转向现货钱包

        UMFUTURE_OPTION U本位合约钱包转向期权钱包
        OPTION_UMFUTURE 期权钱包转向U本位合约钱包

        MAIN_PORTFOLIO_MARGIN 现货钱包转向统一账户钱包
        PORTFOLIO_MARGIN_MAIN 统一账户钱包转向现货钱包

        MAIN_ISOLATED_MARGIN 现货钱包转向逐仓账户钱包
        ISOLATED_MARGIN_MAIN 逐仓钱包转向现货账户钱包
        """
        start_time = start_time - pd.Timedelta(days=10)
        add_type = ['CMFUTURE_MAIN', 'MARGIN_MAIN', 'MARGIN_UMFUTURE', 'FUNDING_MAIN', 'FUNDING_UMFUTURE',
                    'OPTION_MAIN', 'OPTION_UMFUTURE', 'PORTFOLIO_MARGIN_MAIN', 'ISOLATED_MARGIN_MAIN']
        reduce_type = ['MAIN_MARGIN', 'UMFUTURE_MARGIN', 'MAIN_FUNDING', 'UMFUTURE_FUNDING', 'MAIN_OPTION',
                       'UMFUTURE_OPTION', 'MAIN_PORTFOLIO_MARGIN', 'MAIN_ISOLATED_MARGIN']

        result = []
        for _ in add_type + reduce_type:
            for base_coin in self.base_coins:
                params = {
                    'fromSymbol': base_coin,
                    'startTime': int(start_time.timestamp() * 1000),
                    'type': _,
                    'timestamp': int(round(time.time() * 1000)),
                    'size': 100,
                }
                if _ == 'MAIN_ISOLATED_MARGIN':
                    params['toSymbol'] = 'USDT'
                    del params['fromSymbol']
                # 获取划转信息(取上一小时到当前时间的划转记录)
                try:
                    account_info = self.exchange.sapi_get_asset_transfer(params)
                except BaseException as e:
                    logger.info(e)
                    logger.info(f'当前账户查询类型【{_}】失败，不影响后续操作，请忽略')
                    continue
                if account_info and int(account_info['total']) > 0:
                    res = pd.DataFrame(account_info['rows'])
                    res['timestamp'] = pd.to_datetime(res['timestamp'], unit='ms')
                    res.loc[res['type'].isin(add_type), 'flag'] = 1
                    res.loc[res['type'].isin(reduce_type), 'flag'] = -1
                    res = res[res['status'] == 'CONFIRMED']
                    result.append(res)

        # 获取主账号与子账号之间划转记录
        result2 = []
        for transfer_type in [1, 2]:  # 1: 划入。从主账号划转进来  2: 划出。从子账号划转出去
            for base_coin in self.base_coins:
                params = {
                    'asset': base_coin,
                    'type': transfer_type,
                    'startTime': int(start_time.timestamp() * 1000),
                }
                try:
                    account_info = self.exchange.sapi_get_sub_account_transfer_subuserhistory(params)
                except BaseException as e:
                    logger.info(e)
                    logger.info(f'当前账户查询类型【{transfer_type}】失败，不影响后续操作，请忽略')
                    continue
                if account_info and len(account_info):
                    res = pd.DataFrame(account_info)
                    res['time'] = pd.to_datetime(res['time'], unit='ms')
                    res.rename(columns={'qty': 'amount', 'time': 'timestamp'}, inplace=True)
                    res.loc[res['toAccountType'] == 'SPOT', 'flag'] = 1 if transfer_type == 1 else -1
                    res.loc[res['toAccountType'] == 'USDT_FUTURE', 'flag'] = 1 if transfer_type == 1 else -1
                    res = res[res['status'] == 'SUCCESS']
                    res = res[res['toAccountType'].isin(['SPOT', 'USDT_FUTURE'])]
                    result2.append(res)

        # 将账号之间的划转与单账号内部换转数据合并
        result.extend(result2)
        if not len(result):
            return pd.DataFrame()

        all_df = pd.concat(result, ignore_index=True, copy=False)
        all_df.drop_duplicates(subset=['timestamp', 'tranId', 'flag'], inplace=True)
        all_df = all_df[all_df['asset'].isin(self.base_coins)]
        all_df.sort_values('timestamp', inplace=True)

        all_df['amount'] = all_df['amount'].astype(float) * all_df['flag']
        all_df.rename(columns={'amount': '账户总净值'}, inplace=True)
        all_df['type'] = 'transfer'
        all_df = all_df[['timestamp', '账户总净值', 'type']]
        all_df['timestamp'] = all_df['timestamp'] + pd.Timedelta(hours=utc_offset)
        all_df.reset_index(inplace=True, drop=True)

        all_df['time'] = all_df['timestamp']
        result_df = all_df.resample(rule='1H', on='timestamp').agg(
            {'time': 'last', '账户总净值': 'sum', 'type': 'last'})
        result_df = result_df[result_df['type'].notna()]
        result_df.reset_index(inplace=True, drop=True)

        return result_df

    def get_unimmr(self):
        # 普通账户-现货没有杠杆，所以直接返回999
        return 999

    def _collect_asset_from_spot_to_swap(self, asset='USDT'):
        # =将现货中的U转入的合约账号
        spot_position = self.get_spot_position_df()
        if asset in spot_position['symbol'].to_list():
            asset_amount = spot_position.loc[spot_position['symbol'] == asset, 'free'].sum()
            # 转资金到合约账户
            self.transfer_u_from_spot_to_swap(round(asset_amount - 1, 1), asset)

    def collect_asset(self, asset='USDT'):
        self._collect_asset_from_spot_to_swap(asset)
