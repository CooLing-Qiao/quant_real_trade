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
from typing import Optional

import pandas as pd

from core.binance.base_client import BinanceClient
from core.utils.commons import retry_wrapper, apply_precision
from core.utils.dingding import send_wechat_work_msg, send_msg_for_order
from core.utils.log_kit import divider, logger
from config import utc_offset


class PortfolioMarginClient(BinanceClient):
    """
    统一账户
    - 杠杆现货账户：margin
    - U本位合约账户：um
    - 币本位合约账户：cm
    """
    constants = dict(
        spot_account_type='MARGIN',
        reset_page_leverage_api='papi_post_um_leverage',
        get_swap_position_api='papiGetUmPositionRisk',
        get_spot_open_orders_api='papi_get_margin_openorders',
        cancel_spot_open_orders_api='papi_delete_margin_allopenorders',
        get_swap_open_orders_api='papi_get_um_openorders',
        cancel_swap_open_orders_api='papi_delete_um_allopenorders',
        get_spot_my_trades_api='sapi_get_margin_mytrades',
    )

    def __init__(self, **config):
        super(PortfolioMarginClient, self).__init__(**config)
        # 参数
        self.seed_coins: list = config.get('seed_coins', [])  # 套利的底仓币种

        # 状态
        self.balance = None

    def update_balance(self):
        self.balance = retry_wrapper(
            self.exchange.papi_get_balance, params={'timestamp': ''},
            func_name='获取统一账户的持仓资产'
        )  # 获取账户净值

    def get_balance(self, require_update: bool = True):
        if (self.balance is None) or require_update:
            self.update_balance()
        return self.balance

    def update_swap_account(self):
        self.swap_account = retry_wrapper(
            self.exchange.papi_get_um_account, params={'timestamp': ''},
            func_name='获取U账户信息'
        )
        return self.swap_account

    def get_swap_usdt_balance(self, asset='USDT'):
        """
        统一账户，获取USDT的余额，是不存在的，直接返回 0
        :param asset:  资产类型
        :return:
        """
        account = self.get_swap_account()
        assets_df = pd.DataFrame(account['assets'])
        if assets_df.empty:
            return 0

        if asset not in assets_df['asset'].unique():
            return 0

        usdt_balance = float(assets_df[assets_df['asset'] == asset]['crossWalletBalance'])  # 获取usdt资产

        return usdt_balance

    def _fetch_spot_exchange_info_list(self) -> list:
        spot_exchange_info = retry_wrapper(self.exchange.public_get_exchangeinfo, func_name='获取现货币种规则数据')
        margin_all_pairs = retry_wrapper(self.exchange.sapiGetMarginAllPairs, func_name='获取杠杆现货币种规则数据')

        # 筛选出允许交易的杠杆现货币种
        allowed_margin_symbol_list = []
        for margin_pair in margin_all_pairs:
            if margin_pair['isBuyAllowed'] and margin_pair['isSellAllowed']:
                allowed_margin_symbol_list.append(margin_pair['symbol'])

        # 筛选包含可以杠杆现货交易币种的exchange info(里面包含了现货币种的规则数据，例如价格精度等)
        exchange_info_list = spot_exchange_info['symbols']
        exchange_info_list = [info for info in exchange_info_list if info['symbol'] in allowed_margin_symbol_list]

        return exchange_info_list

    def get_unimmr(self):
        account = retry_wrapper(self.exchange.papiGetAccount, params={'timestamp': ''}, func_name='获取现货账户净值')
        return float(account['uniMMR'])

    # ====================================================================================================
    # ** 账户设置 **
    # ====================================================================================================
    def _set_position_side(self, dual_side_position=False):
        """
        检查是否是单向持仓模式
        """
        params = {'dualSidePosition': 'true' if dual_side_position else 'false', 'timestamp': ''}
        retry_wrapper(self.exchange.papi_post_um_positionside_dual, params=params,
                      func_name='papi_post_um_positionside_dual', if_exit=False)
        logger.ok('修改持仓模式为单向持仓')

    def set_single_side_position(self):
        # 查询持仓模式
        res = retry_wrapper(
            self.exchange.papi_get_um_positionside_dual, params={'timestamp': ''}, func_name='设置单向持仓',
            if_exit=False
        )

        is_duel_side_position = bool(res['dualSidePosition'])

        # 判断是否是单向持仓模式
        if is_duel_side_position:  # 若当前持仓模式不是单向持仓模式，则调用接口修改持仓模式为单向持仓模式
            self._set_position_side(dual_side_position=False)

    def set_duel_side_position(self):
        # 查询持仓模式
        res = retry_wrapper(
            self.exchange.papi_get_um_positionside_dual, params={'timestamp': ''}, func_name='设置双向持仓',
            if_exit=False
        )

        is_duel_side_position = bool(res['dualSidePosition'])

        # 判断是否是单向持仓模式
        if not is_duel_side_position:  # 若当前持仓模式不是单向持仓模式，则调用接口修改持仓模式为单向持仓模式
            self._set_position_side(dual_side_position=True)

    def set_manual_repay_futures(self):
        """
        设置手动还款
        :return:
        """
        repay_res = retry_wrapper(
            self.exchange.papiPostRepayFuturesSwitch,
            params={'autoRepay': 'false', 'timestamp': ''},
            func_name='设置手动还款'
        )
        logger.ok(f'统一账户设置手动还款完成，完成结果：{repay_res}')

    def set_auto_repay_futures(self):
        """
        设置自动还款
        :return:
        """
        repay_res = retry_wrapper(
            self.exchange.papiPostRepayFuturesSwitch,
            params={'autoRepay': 'true', 'timestamp': ''},
            func_name='设置自动还款'
        )
        logger.ok(f'统一账户设置自动还款完成，完成结果：{repay_res}')

    # ====================================================================================================
    # ** 账户持仓 **
    # ====================================================================================================

    # ====================================================================================================
    # ** 资金操作 **
    # ====================================================================================================
    # region 资金操作
    # 资金归集到统一账户钱包
    def _collect_asset_from_um_to_margin(self, asset='USDT'):
        amount = self.get_swap_usdt_balance(asset)
        if amount <= 0:
            logger.ok(f'当前合约账户 {asset}: {amount}，无需归集')
            return
        collect_res = retry_wrapper(self.exchange.papiPostAssetCollection, params={'asset': asset, 'timestamp': ''},
                                    func_name='资金归集到统一账户钱包')
        logger.ok(f'{asset} 资金归集到统一账户钱包完成，归集结果：{collect_res}')

    def collect_asset(self, asset='USDT'):
        self._collect_asset_from_um_to_margin(asset)

    def repay_futures_negative_balance(self):
        """
        清还合约负余额
        :return:
        """
        repay_res = retry_wrapper(
            self.exchange.papiPostRepayFuturesNegativeBalance, params={'timestamp': ''},
            func_name='清还合约负余额'
        )
        logger.ok(f'统一账户清还合约负余额完成，完成结果：{repay_res}')

    def transfer_u_from_spot_to_swap(self, amount, asset='USDT'):
        logger.warning('统一账户暂不支持划转U到合约钱包')

    def transfer_bnb_from_margin_to_um(self, amount):
        """
        把BNB 从 现货账户 转到 U本位合约账户
        :param amount:      划转资金
        :return:
        """
        if amount <= 0:
            logger.info(f'当前划转金额【{amount}】小于0，本次不进行划转。(不是错误不要担心)')
            return

        params = {
            'amount': str(amount),
            'transferSide': 'TO_UM',
            'timestamp': '',
        }
        transfer_info = retry_wrapper(self.exchange.papiPostBnbTransfer, params=params,
                                      func_name='BN现货账户转U去U本位合约账户')
        logger.info(f'BN 资金转移BNB 杠杆账户 => U本位合约账户 接口返回: {transfer_info}')
        logger.info(f'BN 资金转移BNB 杠杆账户 => U本位合约账户 资金量: {amount}')

    def transfer_bnb_from_um_to_margin(self, amount):
        """
        BN U本位合约账户 转U 到 现货账户
        :param amount:      划转资金
        """
        if amount <= 0:
            logger.info(f'当前划转金额【{amount}】小于0，本次不进行划转。(不是错误不要担心)')
            return
        params = {
            'amount': str(amount),
            'transferSide': 'FROM_UM',
            'timestamp': '',
        }
        transfer_info = retry_wrapper(self.exchange.papiPostBnbTransfer, params=params,
                                      func_name='U本位合约账户转U去现货账户')
        logger.info(f'BN 资金转移 BNB U本位合约账户 => 杠杆账户 接口返回: {transfer_info}')
        logger.info(f'BN 资金转移 BNB U本位合约账户 => 杠杆账户 资金量: {amount}')

    def replenish_bnb(self, buy_bnb_value, is_use_spot=True):
        """
        补充BNB，用于抵扣手续费

        :param buy_bnb_value:   补充价值多少U的bnb
        :param is_use_spot:     是否使用现货模式
        """
        # ===获取当前持仓BNB的价值
        # 获取BNB价格
        ticker = retry_wrapper(self.exchange.public_get_ticker_price, params={'symbol': 'BNBUSDT'},
                               func_name='获取BNB现货价格')
        price = float(ticker['price'])  # bnb当前现货价格

        # 获取合约BNB数量
        balance = self.get_balance_df()

        if 'USDT' in balance['symbol'].to_list():
            usdt = float(balance[balance['symbol'] == 'USDT']['crossMarginFree'])
            if usdt < buy_bnb_value:
                logger.warning(f'当前账户usdt余额【{usdt:.4f}】不足以购买BNB补充手续费，跳过补充BNB操作')
                return 0
        else:
            logger.warning(f'当前账户未查询到usdt币种，跳过补充BNB操作')
            return 0

        # 获取现货BNB数量
        if 'BNB' in balance['symbol'].to_list():
            spot_bnb = float(balance[balance['symbol'] == 'BNB']['crossMarginFree'])
            swap_bnb = float(balance[balance['symbol'] == 'BNB']['umWalletBalance'])  # 获取BNB持仓数量
        else:
            spot_bnb = 0
            swap_bnb = 0

        swap_bnb_value = swap_bnb * price  # 计算当前BNB价值
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
                logger.info('补充现货和合约BNB总价值不足10U，跳过补充BNB操作')
                all_buy_bnb_value = 0
            else:
                logger.info(f'补充现货和合约BNB总价值 {all_buy_bnb_value:.4f} U，准备开始补充BNB······')

                # 市价单购买BNB
                ticker = self.exchange.public_get_ticker_price(params={'symbol': 'BNBUSDT'})
                price = float(ticker['price'])
                quantity = all_buy_bnb_value / price * 0.98  # 相当于2%滑点
                quantity = float(f'{quantity:.3f}')  # 根据价格精度调整，这里直接写死了
                params = {'symbol': 'BNBUSDT', 'side': 'BUY', 'type': 'MARKET', 'quantity': str(quantity),
                          'newClientOrderId': str(int(time.time())), 'timestamp': ''}
                logger.info(f'购买BNB下单参数: {params}')
                try:
                    res = retry_wrapper(self.exchange.papiPostMarginOrder, params=params, func_name='privatePostOrder')
                    logger.info(f'购买BNB下单结果: {res}')
                except BaseException as e:
                    logger.debug(e)
                    logger.debug(traceback.format_exc())
                    logger.error('购买BNB出错，跳过补充BNB操作')
                    return 0

                # 休息一下，让市场消化一下单子
                time.sleep(5)
        else:
            all_buy_bnb_value = 0

        # 平衡BNB
        self.rebalance_bnb(is_use_spot)
        logger.ok('BNB数量检查、调整完成')

        return all_buy_bnb_value

    def rebalance_bnb(self, is_use_spot):
        """
        根据下单模式，来平衡现货账户和合约账户之间的bnb数量

        :param is_use_spot:     是否使用现货模式
        """
        # 获取合约BNB数量
        balance = self.get_balance_df()
        swap_bnb = float(balance[balance['symbol'] == 'BNB']['umWalletBalance'])  # 获取BNB持仓数量

        # 获取现货BNB数量
        spot_bnb = float(balance[balance['symbol'] == 'BNB']['crossMarginFree'])  # 获取BNB持仓数量

        # 根据模式，来判断BNB划转
        if is_use_spot:  # 现货模式，设置现货账户与合约账户两边BNB平衡
            bnb_mean = (swap_bnb + spot_bnb) / 2  # 构建两边的平衡点
            transfer_bnb = bnb_mean - swap_bnb  # 需要给合约多少bnb
            transfer_bnb = apply_precision(transfer_bnb, 6)  # 向下取整，保留6位有效数字，划转金额可以比最小下单精度小，这里多延几位
            if transfer_bnb > 0:  # 若大于0，表示需要向合约划转
                # 划转BNB到合约账户
                self.transfer_bnb_from_margin_to_um(transfer_bnb)
            elif transfer_bnb < 0:  # 若小于0，表示需要向现货划转
                # 划转BNB到现货账户
                self.transfer_bnb_from_um_to_margin(abs(transfer_bnb))
            else:
                logger.info('划转BNB数量为0，跳过划转操作')
        else:  # 合约模式，直接将现货账户的BNB全部转入合约账户中
            spot_bnb = apply_precision(spot_bnb, 6)  # 向下取整，保留6位有效数字，划转金额可以比最小下单精度小，这里多延几位
            if spot_bnb > 0:
                self.transfer_bnb_from_margin_to_um(spot_bnb)

    def adjust_base_margin(self):
        if not self.base_margin.keys():
            logger.ok(f'未配置额外基础保证金')
            return

        # 计算一下基础稳定币总价值
        base_coin_equity = self.get_base_coin_equity()
        if base_coin_equity < 10:
            logger.warning(f'总资产 < 10u, 忽略额外基础保证金操作')
            return

        # 需要撤销未成交挂单
        self.cancel_all_spot_orders([f'{base_coin}USDT' for base_coin in self.base_coins])
        logger.info(f'基础币保证金: {self.base_coin_amount}')

        spot_ticker_data = self.fetch_spot_ticker_price()
        spot_ticker = {_['symbol']: float(_['price']) for _ in spot_ticker_data}

        for base_coin, amount in self.base_coin_amount.items():
            if base_coin == 'USDT':
                continue

            # 比例 低于 配置阈值-5%，对 BF 加仓
            current_rate = amount * spot_ticker.get(f'{base_coin}USDT', 1) / base_coin_equity
            if current_rate <= self.base_margin[base_coin] - 0.05:
                logger.info(f'{base_coin} 持有比例过低，需要加仓购买')
                quantity = int((self.base_margin[base_coin] - current_rate) * base_coin_equity)
                book_ticker = self.fetch_book_ticker(symbol=f'{base_coin}USDT', symbol_type='spot')
                logger.info(f'盘口信息: {book_ticker}')
                if float(book_ticker['bidPrice']) > 1:
                    logger.warning(f'当前{base_coin}盘口买一价格超过 1，等待价格回落再可考虑成交')
                    continue
                params = {
                    'symbol': f'{base_coin}USDT',
                    'side': 'BUY',
                    'type': 'LIMIT',
                    'price': book_ticker['bidPrice'],  # 挂买一，等成交
                    'timeInForce': 'GTC',
                    'quantity': str(quantity),
                    'newClientOrderId': str(int(time.time())),
                    'timestamp': ''
                }
                self.place_spot_order(**params)
                logger.ok(f'{base_coin} 购买操作完成')

            # 比例 高于 配置阈值+5%，所有币种换成 USDT
            if current_rate >= self.base_margin[base_coin] + 0.05:
                logger.info(f'{base_coin} 持有比例过高，需要兑换成 USDT')
                quantity = int((current_rate - self.base_margin[base_coin]) * base_coin_equity)
                book_ticker = self.fetch_book_ticker(symbol=f'{base_coin}USDT', symbol_type='spot')
                logger.info(f'盘口信息: {book_ticker}')
                params = {
                    'symbol': f'{base_coin}USDT',
                    'side': 'SELL',
                    'type': 'LIMIT',
                    'price': book_ticker['bidPrice'],  # 挂买一
                    'timeInForce': 'IOC',
                    'quantity': str(quantity),
                    'newClientOrderId': str(int(time.time())),
                    'timestamp': ''
                }
                self.place_spot_order(**params)
                logger.ok(f'{base_coin} 出售操作完成')

        time.sleep(5)
        # BFUSD 奖励发放到现货账户的 BFUSD，统一账户需要单独转一下
        spot_position_df  = self.get_spot_position_df()
        amount = spot_position_df.loc[spot_position_df['symbol'] == 'BFUSD', 'free'].sum()
        if amount > 0:
            self.transfer_asset_spot_to_margin(amount, asset='BFUSD')

    # endregion

    def transfer_asset_spot_to_margin(self, amount, asset='USDT'):
        params = {
            'type': 'MAIN_PORTFOLIO_MARGIN',  # 现货钱包转向统一账户钱包
            'asset': asset,
            'amount': str(amount),
            'timestamp': ''
        }
        res = retry_wrapper(self.exchange.sapi_post_asset_transfer, params=params, func_name='现货划转到杠杆全仓')
        logger.ok(f'现货划转 {asset} 到杠杆全仓 -> result: {res}')

    # endregion

    def get_balance_df(self) -> pd.DataFrame:
        """
        获取统一账户的持仓资产

        :return:
            统一账户杠杆钱包所有持仓资产数据

        """
        # 获取现货账户净值
        balance_list = self.get_balance()
        if balance_list is None or len(balance_list) == 0:
            return pd.DataFrame(columns=['symbol', 'totalWalletBalance', 'crossMarginAsset', 'crossMarginBorrowed',
                                         'crossMarginFree', 'crossMarginInterest', 'crossMarginLocked', 'umWalletBalance',
                                         'umUnrealizedPNL', 'cmWalletBalance', 'cmUnrealizedPNL', '当前持仓量', '仓位价值'])

        balance_df = pd.DataFrame(balance_list)

        # 转换数据类型
        balance_df['totalWalletBalance'] = pd.to_numeric(balance_df['totalWalletBalance'], errors='coerce')
        balance_df['crossMarginAsset'] = pd.to_numeric(balance_df['crossMarginAsset'], errors='coerce')
        balance_df['crossMarginBorrowed'] = pd.to_numeric(balance_df['crossMarginBorrowed'], errors='coerce')
        balance_df['crossMarginFree'] = pd.to_numeric(balance_df['crossMarginFree'], errors='coerce')
        balance_df['crossMarginInterest'] = pd.to_numeric(balance_df['crossMarginInterest'], errors='coerce')
        balance_df['crossMarginLocked'] = pd.to_numeric(balance_df['crossMarginLocked'], errors='coerce')
        balance_df['umWalletBalance'] = pd.to_numeric(balance_df['umWalletBalance'], errors='coerce')
        balance_df['umUnrealizedPNL'] = pd.to_numeric(balance_df['umUnrealizedPNL'], errors='coerce')
        balance_df['cmWalletBalance'] = pd.to_numeric(balance_df['cmWalletBalance'], errors='coerce')
        balance_df['cmUnrealizedPNL'] = pd.to_numeric(balance_df['cmUnrealizedPNL'], errors='coerce')
        balance_df['当前持仓量'] = balance_df['crossMarginFree']  # 现货账户的目前可用持仓数量
        balance_df = balance_df[balance_df['totalWalletBalance'] != 0]
        balance_df.rename(columns={'asset': 'symbol'}, inplace=True)
        balance_df.reset_index(inplace=True, drop=True)

        balance_df['仓位价值'] = None  # 设置默认值，后面有地方会用到这个数值

        return balance_df

    def get_swap_income_df(self, date_time, account_type='um') -> Optional[pd.DataFrame]:
        """
        通过接口获取账户的Income，数据结构如下：
        CM
        "TRANSFER","WELCOME_BONUS", "FUNDING_FEE", "REALIZED_PNL", "COMMISSION", "INSURANCE_CLEAR",
        and "DELIVERED_SETTELMENT"

        UM
        TRANSFER, WELCOME_BONUS, REALIZED_PNL, FUNDING_FEE, COMMISSION, INSURANCE_CLEAR, REFERRAL_KICKBACK,
        COMMISSION_REBATE, API_REBATE, CONTEST_REWARD, CROSS_COLLATERAL_TRANSFER, OPTIONS_PREMIUM_FEE,
        OPTIONS_SETTLE_PROFIT, INTERNAL_TRANSFER, AUTO_EXCHANGE, DELIVERED_SETTELMENT, COIN_SWAP_DEPOSIT,
        COIN_SWAP_WITHDRAW, POSITION_LIMIT_INCREASE_FEE
        :param date_time: 截止日期
        :param account_type: 账户类型
        :return:
        """
        params = {
            # 'incomeType': '',
            'startTime': int(date_time.timestamp()) * 1000,
            'limit': 1000
        }
        # 不管写多少最多只返回7天
        df_list = []
        while True:
            if account_type == 'um':
                logger.info(f'获取U合约资金流水，{params}...')
                income = retry_wrapper(self.exchange.papiGetUmIncome, params=params, func_name='获取U合约资金流水')
            elif account_type == 'cm':
                logger.info(f'获取币合约资金流水，{params}...')
                income = retry_wrapper(self.exchange.papiGetCmIncome, params=params, func_name='获取币合约资金流水')
            else:
                break

            df = pd.DataFrame(income)
            if df.empty:
                break

            df['income'] = pd.to_numeric(df['income'], errors='coerce')
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df_list.append(df)

            if df.iloc[-1]['time'] >= datetime.now():
                break

            if int(df.iloc[-1]['time'].timestamp()) * 1000 == params['startTime']:
                break

            params['startTime'] = int(df.iloc[-1]['time'].timestamp()) * 1000

        if not df_list:
            return None

        all_df = pd.concat(df_list, ignore_index=True, copy=False)
        all_df.drop_duplicates(subset=['time', 'symbol', 'incomeType', 'income', 'asset', 'info'], keep='last',
                               inplace=True)
        all_df.sort_values(by=['time', 'symbol'], ascending=[True, True], inplace=True)
        return all_df

    def get_account_overview(self):
        """
        根据最新行情价格，当前账户资产，计算账户的现金、持仓价值和负债
        :return:
        """
        spot_ticker_data = self.fetch_spot_ticker_price()
        spot_ticker = {_['symbol']: float(_['price']) for _ in spot_ticker_data}
        for base_coin in self.base_coins:
            spot_ticker[base_coin] = 1  # 锚定 1:1 兑换 美元

        # 获取账户余额信息
        balance = self.get_balance_df()
        swap_usdt_balance = self.get_swap_usdt_balance()
        swap_position_df = self.get_swap_position_df()
        logger.ok('获取账户资产数据完成')

        if balance.empty:
            return {
                'usdt_balance': 0,
                'negative_balance': 0,
                'account_pnl': 0,
                'account_equity': 0,
                'spot_assets': {
                    'assets_pos_value': {},
                    'assets_amount': {},
                    'usdt': 0,
                    'equity': 0,
                    'dust_spot_df': pd.DataFrame(),
                    'spot_position_df': pd.DataFrame()
                },
                'swap_assets': {
                    'assets_pos_value': {},
                    'assets_amount': {},
                    'assets_pnl': 0,
                    'usdt': 0,
                    'equity': 0,
                    'swap_position_df': pd.DataFrame()
                }
            }

        logger.info('准备处理资产数据...')
        # 追加USDT后缀，方便计算usdt价值
        balance.loc[~balance['symbol'].isin(self.base_coins), 'symbol'] = balance['symbol'] + 'USDT'

        # 计算钱包价值和PNL
        balance['UM钱包价值'] = balance.apply(
            lambda row: row['umWalletBalance'] * spot_ticker.get(row["symbol"], 0), axis=1)
        balance['CM钱包价值'] = balance.apply(
            lambda row: row['cmWalletBalance'] * spot_ticker.get(row["symbol"], 0), axis=1)
        balance['杠杆钱包价值'] = balance.apply(
            lambda row: row['crossMarginBorrowed'] * spot_ticker.get(row["symbol"], 0), axis=1)
        balance['UM钱包PNL'] = balance.apply(
            lambda row: row['umUnrealizedPNL'] * spot_ticker.get(row["symbol"], 0), axis=1)
        balance['CM钱包PNL'] = balance.apply(
            lambda row: row['cmUnrealizedPNL'] * spot_ticker.get(row["symbol"], 0), axis=1)
        balance['仓位价值'] = balance.apply(
            lambda row: row['当前持仓量'] * spot_ticker.get(row["symbol"], 0), axis=1)
        balance['杠杆利息'] = balance.apply(
            lambda row: row['crossMarginInterest'] * spot_ticker.get(row["symbol"], 0), axis=1)
        balance['锁仓资金'] = balance.apply(
            lambda row: row['crossMarginLocked'] * spot_ticker.get(row["symbol"], 0), axis=1)
        balance['当前价格'] = balance.apply(lambda row: spot_ticker.get(row["symbol"], 0), axis=1)

        # 负债 = UM和CM的账户负余额 + 杠杆借币 + 杠杆利息
        negative_balance = (
                - balance['杠杆钱包价值'].sum()
                - balance['杠杆利息'].sum()
                + balance.loc[balance['UM钱包价值'] < 0, 'UM钱包价值'].sum()
                + balance.loc[balance['CM钱包价值'] < 0, 'CM钱包价值'].sum()
        )

        # 未实现盈亏 = UM + CM
        account_pnl = (balance['UM钱包PNL'].sum() + balance['CM钱包PNL'].sum())

        # 获取当前账号现货U的数量
        spot_usdt_balance = 0
        for base_coin in self.base_coins:
            if base_coin in balance['symbol'].to_list():
                base_coin_amount = (
                        balance.loc[balance['symbol'] == base_coin, '当前持仓量'].sum()
                        + balance.loc[balance['symbol'] == base_coin, '锁仓资金'].sum()
                )
                spot_usdt_balance += base_coin_amount
                self.base_coin_amount[base_coin] = base_coin_amount or 0
        # 去除掉USDT,USDC现货
        balance = balance[~balance['symbol'].isin(self.base_coins)]

        # 过滤掉不含报价的币
        balance = balance[balance['仓位价值'] != 0]
        # 过滤掉BNB，用于抵扣手续费，不参与现货交易
        balance = balance[balance['symbol'] != 'BNBUSDT']
        # 仓位价值 小于 5U，无法下单的碎币，单独记录
        dust_spot_df = balance[balance['仓位价值'] < 5]
        # 过滤掉仓位价值 大于 5U
        balance = balance[balance['仓位价值'] > 5]
        # 去除掉已经下架的币种，避免下架币的资金影响到当前账户的配置
        if 'spot' not in self.market_info:
            self.get_market_info(symbol_type='spot')
        balance = balance[balance['symbol'].isin(self.market_info['spot']['symbol_list'])]

        # 处理联合保证金
        if self.coin_margin:
            for _symbol, _coin_balance in self.coin_margin.items():
                if _symbol in balance['symbol'].to_list():
                    balance.loc[balance['symbol'] == _symbol, '仓位价值'] = _coin_balance
                else:
                    logger.warning(f'现货杠杆钱包未找到 {_symbol} 的资产，无法计算 {_symbol} 的保证金')

        # 现货净值 = 现货仓位价值总和
        spot_equity = balance['仓位价值'].sum() + spot_usdt_balance
        seed_coin_spot_equity = 0

        # =====处理现货持仓列表信息
        if self.seed_coins:
            seed_coin_spot_equity = balance[balance['symbol'].isin(self.seed_coins)]['仓位价值'].sum()
            balance = balance[~balance['symbol'].isin(self.seed_coins)]
        if self.coin_margin:
            balance = balance[~balance['symbol'].isin(self.coin_margin.keys())]
        # 构建币种的balance信息
        # 币种 : 价值
        spot_assets_pos_dict = balance[['symbol', '仓位价值']].to_dict(orient='records')
        spot_assets_pos_dict = {_['symbol']: _['仓位价值'] for _ in spot_assets_pos_dict}

        # 币种 : 数量
        spot_asset_amount_dict = balance[['symbol', '当前持仓量']].to_dict(orient='records')
        spot_asset_amount_dict = {_['symbol']: _['当前持仓量'] for _ in spot_asset_amount_dict}

        # =====处理合约持仓列表信息
        swap_position_df.reset_index(inplace=True)
        swap_pnl = swap_position_df['持仓盈亏'].sum()
        # 判断是否需要过滤掉套利仓位
        if self.seed_coins:
            # 去除底仓的持仓币种
            swap_position_df = swap_position_df[~swap_position_df['symbol'].isin(self.seed_coins)]

        # 币种 : 价值
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

        # 账户总净值 = 现货总价值 + 账户总体未实现盈亏 + 负债
        account_equity = (spot_equity + account_pnl + negative_balance)

        logger.ok('处理资产数据完成')

        return {
            'usdt_balance': spot_usdt_balance + swap_usdt_balance,
            'negative_balance': negative_balance,
            'account_pnl': account_pnl,
            'account_equity': account_equity,
            'seed_coin_spot_equity': seed_coin_spot_equity,
            'spot_assets': {
                'assets_pos_value': spot_assets_pos_dict,
                'assets_amount': spot_asset_amount_dict,
                'usdt': spot_usdt_balance,
                'equity': spot_equity,
                'dust_spot_df': dust_spot_df,
                'spot_position_df': balance
            },
            'swap_assets': {
                'assets_pos_value': swap_assets_pos_dict,
                'assets_amount': swap_asset_amount_dict,
                'assets_pnl': swap_asset_pnl_dict,
                'usdt': swap_usdt_balance,
                'equity': swap_usdt_balance + swap_pnl,
                'swap_position_df': swap_position_df
            }
        }

    def fetch_transfer_history(self, start_time=datetime.now()):
        """
        获取划转记录

        MAIN_MARGIN 现货钱包转向杠杆全仓钱包
        MARGIN_MAIN 杠杆全仓钱包转向现货钱包

        MAIN_PORTFOLIO_MARGIN 现货钱包转向统一账户钱包
        PORTFOLIO_MARGIN_MAIN 统一账户钱包转向现货钱包

        """

        start_time = start_time - pd.Timedelta(days=10)
        add_type = ['MAIN_PORTFOLIO_MARGIN', 'MAIN_MARGIN']
        reduce_type = ['PORTFOLIO_MARGIN_MAIN', 'MARGIN_MAIN']

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
                    # res.loc[res['toAccountType'] == 'SPOT', 'flag'] = 1 if transfer_type == 1 else -1
                    res.loc[res['toAccountType'] == 'MARGIN', 'flag'] = 1 if transfer_type == 1 else -1
                    res = res[res['status'] == 'SUCCESS']
                    res = res[res['toAccountType'].isin(['MARGIN'])]
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

    # ================================================================
    # 订单底层函数
    # - spot_order_margin: 在币币账户买卖现货
    # - swap_order_um: 在统一账户合约开空、平空
    # ================================================================
    # 在币币账户下单
    def place_spot_order(self, symbol, side, quantity, price=None, **kwargs):
        divider(f'`{symbol}`杠杆现货下单 {side} {quantity}', '.')
        # 确定下单参数
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': str(quantity),
            **kwargs
        }

        if price is not None:
            params['price'] = str(price)

        if 'timeInForce' not in params:
            params['timeInForce'] = 'IOC'
            params['type'] = 'LIMIT'

        try:
            logger.info(f'杠杆现货下单参数：{params}')
            # 下单
            order_res = self.retry_wrapper_for_order(
                self.exchange.papiPostMarginOrder,
                params=params,
                func_name='杠杆现货下单',
                symbol_type='spot'
            )
            logger.ok(f'杠杆现货下单完成，杠杆现货下单信息结果：{order_res}')
        except Exception as e:
            logger.error(f'杠杆现货下单出错：{e}')
            send_wechat_work_msg(
                f'杠杆现货 {symbol} 下单 {float(quantity) * float(price)}U 出错，请查看程序日志',
                self.wechat_webhook_url
            )
            return None
            # 发送下单结果到钉钉
        send_msg_for_order([params], [order_res], self.wechat_webhook_url)
        return order_res

    # 使用统一账户，
    def place_swap_order(self, symbol, side, quantity, price=None, reduce_only=False, **kwargs):
        """
        :param symbol: 合约代码，例如'BTCUSD_210625'
        :param side: 方向
        :param quantity: 数量
        :param price: 开仓价格
        :param reduce_only: 只减仓模式
        :return:

        timeInForce参数的几种类型
        GTC - Good Till Cancel 成交为止
        IOC - Immediate or Cancel 无法立即成交(吃单)的部分就撤销
        FOK - Fill or Kill 无法全部立即成交就撤销
        GTX - Good Till Crossing 无法成为挂单方就撤销
        """
        divider(f'`{symbol}`U本位合约下单 {side} {quantity}', '.')

        # 确定下单参数
        params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': str(quantity),
            'reduce_only': str(reduce_only),
            **kwargs
        }

        if price is not None:
            params['price'] = str(price)

        if 'timeInForce' not in params:
            params['timeInForce'] = 'IOC'
            params['type'] = 'LIMIT'

        try:
            logger.info(f'U本位合约下单参数：{params}')
            # 下单
            order_res = self.retry_wrapper_for_order(
                self.exchange.papiPostUmOrder,
                params=params,
                func_name='U本位合约下单',
                symbol_type='swap',
            )
            logger.ok(f'U本位合约下单完成，U本位合约下单信息结果：{order_res}')
        except Exception as e:
            logger.error(f'U本位合约下单出错：{e}')
            send_wechat_work_msg(
                f'U本位合约 {symbol} 下单 {float(quantity) * float(price)}U 出错，请查看程序日志',
                self.wechat_webhook_url
            )
            return None
        send_msg_for_order([params], [order_res], self.wechat_webhook_url)
        return order_res

    # ================================================================
    # 下单相关函数
    # - pm_buy_spot: 买入现货
    # - pm_sell_spot: 卖出现货
    # - pm_open_swap_short_position: 合约开空单
    # - pm_close_swap_short_position: 合约平空单
    # ================================================================
    def buy_spot(self, spot_symbol, coin_amount) -> dict:
        """
        在币安买入现货
        :param spot_symbol: 买入现货的交易对
        :param coin_amount: 买入现货的数量
        :return: 交易回执dict
        """
        spot_order_info = self.place_spot_order(symbol=spot_symbol, side='buy', quantity=coin_amount)
        logger.debug(f'交易回执:\n{spot_order_info}')
        logger.ok(f'买币完成')
        return spot_order_info

    def sell_spot(self, spot_symbol, coin_amount):
        """
        在币安卖出现货
        :param spot_symbol: 卖出现货的交易对
        :param coin_amount: 卖出现货的数量
        :return: 交易回执dict
        """
        spot_order_info = self.place_spot_order(symbol=spot_symbol, side='sell', quantity=coin_amount)
        logger.debug(f'交易回执:\n{spot_order_info}')
        logger.ok(f'卖币完成')
        return spot_order_info

    def sell_swap(self, swap_symbol, coin_amount):
        """
        在币安U本位合约开空单
        :param swap_symbol: 合约开空单的交易对
        :param coin_amount: 需要开空单的币的数量
        :return: 交易回执dict
        """
        swap_order_info = self.place_swap_order(symbol=swap_symbol, side='sell', quantity=coin_amount)
        logger.debug(f'交易回执:\n{swap_order_info}')
        logger.ok(f'合约开空仓完成')
        return swap_order_info

    def buy_swap(self, swap_symbol, coin_amount):
        """
        在币安U本位合约平空单
        :param swap_symbol: 合约平空单的交易对
        :param coin_amount: 需要平空单的币的数量
        :return: 交易回执dict
        """
        swap_order_info = self.place_swap_order(symbol=swap_symbol, side='buy', quantity=coin_amount)
        logger.debug(f'交易回执:\n{swap_order_info}')
        logger.ok(f'合约开空仓完成')
        return swap_order_info

    # ====================================================================================================
    # ** BNB操作 **
    # ====================================================================================================
