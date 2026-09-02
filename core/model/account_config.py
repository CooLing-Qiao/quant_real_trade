"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import json
import random
import time
from typing import Optional

import pandas as pd

from core.binance.base_client import BinanceClient
from core.binance.portfolio_margin_client import PortfolioMarginClient
from core.binance.standard_client import StandardClient
from core.model.account_type import AccountType
from core.model.rebalance_mode import RebalanceMode
from core.trade import split_order_twap, split_order_balanced
from core.utils.dingding import send_wechat_work_msg
from core.utils.log_kit import logger
from core.utils.path_kit import get_file_path


class AccountConfig:

    def __init__(self, name: str, **config):
        """
        初始化AccountConfig类

        参数:
        config (dict): 包含账户配置信息的字典

        配置示例:
        config = {
            "if_use_bnb_burn": True,  # 是否开启BNB燃烧，抵扣手续费
            "buy_bnb_value": 11,  # 买多少U的bnb来抵扣手续费。建议最低11U，现货最小下单量限制10U
            "if_transfer_bnb": False,  # 是否开启小额资产兑换BNB功能。仅现货模式下生效
            "get_kline_num": 999,  # 获取多少根K线。这里跟策略日频和小时频影响。日线策略，代表999根日线k。小时策略，代表999根小时k
            "max_one_order_amount": 100,  # 最大拆单金额。
            "twap_interval": 2,  # 下单间隔
            "hour_offset": '0m',  # 分钟偏移设置，可以自由设置时间，配置必须是kline脚本中interval的倍数。默认：0m，表示不偏移。15m，表示每个小时偏移15m下单。
            "wechat_webhook_url": 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=',  # 企业微信机器人
            'order_spot_money_limit': order_spot_money_limit,  # 现货下单最小金额限制，适当增加可以减少部分reb。默认10，不建议小于10，这会让你的下单报错，10是交易所的限制。
            'order_swap_money_limit': order_swap_money_limit,  # 合约下单最小金额限制，适当增加可以减少部分reb。默认5，不建议小于5，这会让你的下单报错，5是交易所的限制。
            # ** 重要 ** ---------------------------------------------------------------------------------------
            # 以下配置框架自动和回测保持一致的配置，手动修改之后会有灾难性后果
            "rebalance_mode": None,  # Rebalance 模式
            "leverage": 1,  # 杠杆。现货模式下：最大杠杆限制1.3倍。合约模式不做限制。
            "min_kline_num": 168,  # 最低要求b中有多少小时的k线。这里与回测一致。168：表示168小时
            "black_list": ['BTCUSDT', 'ETHUSDT', 'BTSUSDT'],  # 黑名单。不参与交易的币种
            "white_list": [],  # 白名单。只参与交易的币种
            "is_pure_long": False,  # 纯多设置(https://bbs.quantclass.cn/thread/36230)
        """

        self.name: str = name  # 账户名称，建议用英文，不要带有特殊符号

        # 交易所API
        self.api_key: str = config.get("apiKey", "")
        self.secret: str = config.get("secret", "")

        # ======== 实盘功能配置 ========
        # 是否开启BNB燃烧，抵扣手续费
        self.if_use_bnb_burn: bool = config.get("if_use_bnb_burn", False)

        # 买多少U的bnb来抵扣手续费。建议最低11U，现货最小下单量限制10U
        self.buy_bnb_value: int = config.get("buy_bnb_value", 11)

        # 是否开启小额资产兑换BNB功能，仅现货模式下生效
        self.if_transfer_bnb: bool = config.get("if_transfer_bnb", False)

        # 获取多少根K线，这里跟策略日频和小时频影响。日线策略，代表999根日线k。小时策略，代表999根小时k
        self.get_kline_num: int = config.get("get_kline_num", 999)

        # 最大拆单金额
        self.max_one_order_amount: int | float = (
                config.get("max_one_order_amount", 100) * (1 + random.uniform(-0.2, 0.2))
        )

        # 纯多设置
        self.is_pure_long: bool = config.get("is_pure_long", False)

        # 下单间隔
        self.twap_interval: int = config.get("twap_interval", 2)

        # 分钟偏移设置，可以自由设置时间，配置必须是kline脚本中interval的倍数。默认：0m表示不偏移，15m表示每个小时偏移15m下单
        self.hour_offset: str = config.get("hour_offset", '0m')

        # 企业微信机器人Webhook URL
        # 创建企业微信机器人 参考帖子: https://bbs.quantclass.cn/thread/10975
        # 配置案例  https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxxxxxxxxxxxxx
        self.wechat_webhook_url: str = config.get("wechat_webhook_url", '')

        # 现货下单最小金额限制，适当增加可以减少部分reb。默认10，不建议小于10，这会让你的下单报错，10是交易所的限制
        self.order_spot_money_limit: int = config.get("order_spot_money_limit", 10)

        # 合约下单最小金额限制，适当增加可以减少部分reb。默认5，不建议小于5，这会让你的下单报错，5是交易所的限制
        self.order_swap_money_limit: int = config.get("order_swap_money_limit", 5)

        self.use_spot: bool = False  # 2024-09-05 开始，自动从子策略的load中更新

        self.seed_coins: Optional[list] = config.get("seed_coins", [])

        self.coin_margin: Optional[dict[str, dict[str, float]]] = config.get("coin_margin", {})

        self.account_type: AccountType = AccountType.translate(config.get("account_type", '普通账户'))

        # 基础保证金
        # 解释: 默认保证金都是 USDT，这里扩充基础保证金也就是稳定币保证金，锚定美元 1:1 汇率 这种保证金，如果配置什么欧元/英镑，不支持
        # 配置: { 'USDC': 0.1, 'BFUSD': 0.2, 'USDT': 0.7 }
        #   设置总资产可以持有10%的USDC，20%的BFUSD，70%的USDT
        #   设置资产必须含有 USDT
        #   设置资产比例总和不能超过 1
        #   高于比列，卖掉换成 USDT
        #   低于比列，买入按比例换成 USDC/BFUSD
        self.base_margin: Optional[dict] = config.get("base_margin", {'USDT': 1})

        # ======== 需要和资金曲线回测部分，共享的变量 ========
        # Rebalance 模式
        self.rebalance_mode: RebalanceMode = RebalanceMode.init(config.get('rebalance_mode', None))
        # 杠杆，现货模式下最大杠杆限制1.3倍，合约模式不做限制
        self.leverage: int = config.get("leverage", 1)
        # 交易所页面杠杆上限（跟上面的 leverage 是两回事：leverage 决定目标仓位规模，
        # page_leverage 决定交易所侧的保证金占用上限/杠杆挡位对齐目标）。默认5，与历史行为一致。
        self.page_leverage: int = int(config.get("page_leverage", 5))
        # 最低要求b中有多少小时的K线，这里与回测一致。168：表示168小时
        self.min_kline_num: int = config.get("min_kline_num", 168)
        # 黑名单，不参与交易的币种
        self.black_list: list = config.get("black_list", []) + self.load_delist()
        # 白名单，只参与交易的币种
        self.white_list: list = config.get("white_list", [])
        # ======== 需要和资金曲线回测部分，共享的变量 ========

        # ======== 实盘配置检查项 ========
        if 'USDT' not in self.base_margin:
            logger.warning(f'[{self.name}] 检测到基础保证金【base_margin】未配置【USDT】，当前程序自动退出。')
            exit()

        if sum(self.base_margin.values()) > 1:
            logger.warning(f'[{self.name}] 基础保证金【base_margin】配置配置资产比例总和超过 1，请调整后重新启动，当前程序自动退出。')
            exit()

        if (self.account_type == AccountType.PORTFOLIO_MARGIN and
                len(set(self.coin_margin.keys()) & set(self.seed_coins)) > 0):
            logger.warning(
                '检测到【seed_coins】与【coin_margin】有重复币种，建议联系助教了解清楚逻辑之后修改配置，当前程序自动退出。')
            exit()

        if not all((self.api_key, self.secret)):
            logger.warning(f'[{self.name}] 配置中apiKey和secret为空，请先配置账号API信息')

        if self.seed_coins:
            logger.warning('检测到您配置了底仓币种【seed_coins】，系统自动拉黑这些币，避免策略选中影响底仓。')
            self.black_list = list(set(self.black_list + self.seed_coins))

        # 初始化变量
        self.bn: Optional[BinanceClient] = BinanceClient()

        self.swap_position: Optional[pd.DataFrame] = pd.DataFrame(columns=['symbol', 'symbol_type', '当前持仓量'])
        self.swap_equity: float = 0
        self.spot_position: Optional[pd.DataFrame] = pd.DataFrame(columns=['symbol', 'symbol_type', '当前持仓量'])
        self.spot_equity: float = 0
        self.spot_usdt: float = 0

        self.is_usable: bool = False  # 会在update account 的时候，判断当前账户是否可用

    def __repr__(self):
        return f"""# {self.name} 配置信息如下：
+ 账户类型: {self.account_type}
+ Rebalance 模式: {self.rebalance_mode}
+ 燃烧BNB做手续费: {self.if_use_bnb_burn}
+ 自动购买BNB阈值(USDT): ${self.buy_bnb_value:.2f}
+ 自动划转BNB: {self.if_transfer_bnb}
+ 杠杆设置: {self.leverage}
+ 获取行情k线数量: {self.get_kline_num}
+ 产生信号最小K线数量: {self.min_kline_num}
+ 单次下单最大金额(USDT): {self.max_one_order_amount}
+ 是否纯多: {self.is_pure_long}
+ 拆单间隔(秒): {self.twap_interval}
+ Hour Offset: {self.hour_offset}
+ 拉黑名单: {self.black_list}，只交易名单: {self.white_list}
+ 底仓设置: {self.seed_coins}
+ 联合保证金设置: {self.coin_margin}
+ 微信推送URL: {self.wechat_webhook_url}
"""

    @staticmethod
    def load_delist() -> list:
        """
        加载delist数据，动态处理黑名单
        """
        delist_path = get_file_path('data', 'delist.json', as_path_type=True)

        if delist_path.exists() is False:
            return []

        try:
            with open(delist_path, 'r') as file:
                de_list = json.load(file)['list']
            return [_ for _ in de_list if _.endswith('USDT')]  # 获取USDT结尾的币种
        except Exception as e:
            print(e)
            return []

    def init_exchange(self, exchange_basic_config):
        if self.api_key and self.secret:
            exchange_basic_config['apiKey'] = self.api_key
            exchange_basic_config['secret'] = self.secret
            # 在Exchange增加纯多标记(https://bbs.quantclass.cn/thread/36230)
            exchange_basic_config['is_pure_long'] = self.is_pure_long

            config_params = dict(
                exchange_config=exchange_basic_config,
                spot_order_money_limit=self.order_spot_money_limit,
                swap_order_money_limit=self.order_swap_money_limit,
                wechat_webhook_url=self.wechat_webhook_url,
                coin_margin=self.coin_margin,
                base_margin=self.base_margin,
            )
            match self.account_type:
                case AccountType.STANDARD:
                    client_type = StandardClient
                    config_params['is_pure_long'] = self.is_pure_long
                case AccountType.PORTFOLIO_MARGIN:
                    client_type = PortfolioMarginClient
                    config_params['seed_coins'] = self.seed_coins
                case _:
                    raise Exception("不支持的账户类型：{}".format(self.account_type))
            self.bn = client_type(**config_params)
        else:
            raise Exception("请先配置账号API信息")

    @property
    def hour_offset_minute(self) -> int:
        return int(self.hour_offset[:-1])

    def update_account_info(self, is_only_spot_account: bool = False, is_operate: bool = False):
        self.is_usable = False

        # ===先做一下资金归集，防止手误划转错资金
        self.bn.collect_asset(asset='USDT')

        # 是否只保留现货账户
        if is_only_spot_account and not self.use_spot:  # 如果只保留有现货交易的账户，非现货交易账户被删除
            return False

        # ===加载合约和现货的数据
        account_overview = self.bn.get_account_overview()
        # =获取U本位合约持仓
        swap_position = account_overview.get('swap_assets', {}).get('swap_position_df', pd.DataFrame())
        # =获取U本位合约账户净值(不包含未实现盈亏)
        swap_equity = account_overview.get('swap_assets', {}).get('equity', 0)

        # ===加载现货交易对的信息
        # =获取现货持仓净值(包含实现盈亏，这是现货自带的)
        dust_spot = account_overview.get('spot_assets', {}).get('dust_spot_df', pd.DataFrame())
        spot_usdt = account_overview.get('spot_assets', {}).get('usdt', 0)
        spot_equity = account_overview.get('spot_assets', {}).get('equity', 0)
        spot_position = pd.DataFrame()
        # 判断是否使用现货实盘
        if self.use_spot:  # 如果使用现货实盘，需要读取现货交易对信息和持仓信息
            spot_position = account_overview.get('spot_assets', {}).get('spot_position_df', pd.DataFrame())
            # =小额资产转换
            if self.if_transfer_bnb:
                self.bn.transfer_bnb_for_dust_spot(dust_spot)
        elif self.account_type == AccountType.STANDARD:  # 不使用现货实盘，设置现货价值为默认值0
            spot_equity = 0
            spot_usdt = 0

        logger.info(f'合约净值(不含浮动盈亏): {swap_equity}\t现货净值: {spot_equity}\t现货的USDT:{spot_usdt}')

        # 判断当前账号是否有资金, 低于10USDT的时候，不进行操作
        if swap_equity + spot_equity <= 10:
            return None

        # 判断是否需要进行账户的调整（划转，买BNB，调整页面杠杆）
        if is_operate:
            # ===设置一下页面最大杠杆
            self.bn.reset_max_leverage(max_leverage=self.page_leverage)

            # ===将现货中的U转到合约账户（仅普通账户的时候需要）
            if ((self.account_type == AccountType.STANDARD) and (spot_usdt > self.buy_bnb_value * 2) and
                    (not self.is_pure_long)):
                self.bn.transfer_u_from_spot_to_swap(round(spot_usdt - self.buy_bnb_value * 2 - 1, 1))
                spot_equity -= round(spot_usdt - self.buy_bnb_value * 2 - 1, 1)  # 现货总净值扣除掉划走USDT
                swap_equity += round(spot_usdt - self.buy_bnb_value * 2 - 1, 1)  # 合约总净值加上掉划走USDT

            # ===补充BNB操作(用于抵扣手续费)
            if self.if_use_bnb_burn:  # 判断是否开启BNB燃烧
                # 购买bnb用于抵扣手续费燃烧
                all_buy_bnb_value = self.bn.replenish_bnb(self.buy_bnb_value, self.use_spot)
                if all_buy_bnb_value > 0:
                    # 发送信息给机器人
                    send_wechat_work_msg(f'补充BNB抵扣手续费操作完成，补充金额：{all_buy_bnb_value:.4f} U',
                                         self.wechat_webhook_url)

        self.swap_position = swap_position
        self.swap_equity = swap_equity
        self.spot_position = spot_position
        self.spot_equity = spot_equity - self.buy_bnb_value * 2
        self.spot_usdt = spot_usdt

        self.is_usable = True
        return dict(
            swap_position=swap_position,
            swap_equity=swap_equity,
            spot_position=spot_position,
            spot_equity=self.spot_equity,
        )

    def calc_order_amount(self, position_results) -> pd.DataFrame:
        """
        计算实际下单量

        :param position_results:             选币结果
        :return:

                   当前持仓量   目标持仓量  目标下单份数   实际下单量 交易模式
        AUDIOUSDT         0.0 -2891.524948          -3.0 -2891.524948     建仓
        BANDUSDT        241.1     0.000000           NaN  -241.100000     清仓
        C98USDT        -583.0     0.000000           NaN   583.000000     清仓
        ENJUSDT           0.0  1335.871133           3.0  1335.871133     建仓
        WAVESUSDT        68.4     0.000000           NaN   -68.400000     清仓
        KAVAUSDT       -181.8     0.000000           NaN   181.800000     清仓

        """
        # 更新合约持仓数据
        swap_position = self.swap_position
        swap_position.reset_index(inplace=True)
        swap_position['symbol_type'] = 'swap'

        # 更新现货持仓数据
        if self.use_spot:
            spot_position = self.spot_position
            spot_position.reset_index(inplace=True)
            spot_position['symbol_type'] = 'spot'
            current_position = pd.concat([swap_position, spot_position], ignore_index=True, copy=False)
        else:
            current_position = swap_position

        # ===创建symbol_order，用来记录要下单的币种的信息
        # =创建一个空的symbol_order，里面有select_coin（选中的币）、all_position（当前持仓）中的币种
        order_df = pd.concat([position_results[['symbol', 'symbol_type']], current_position[['symbol', 'symbol_type']]],
                             ignore_index=True, copy=False)
        order_df.drop_duplicates(subset=['symbol', 'symbol_type'], inplace=True)

        order_df.set_index(['symbol', 'symbol_type'], inplace=True)
        current_position.set_index(['symbol', 'symbol_type'], inplace=True)

        # =symbol_order中更新当前持仓量
        order_df['当前持仓量'] = current_position['当前持仓量']
        order_df['当前持仓量'].fillna(value=0, inplace=True)

        # =目前持仓量当中，可能可以多空合并
        if position_results.empty:
            order_df['目标持仓量'] = 0
        else:
            order_df['目标持仓量'] = position_results.groupby(['symbol', 'symbol_type'])[['目标持仓量']].sum()
            order_df['目标持仓量'].fillna(value=0, inplace=True)

        # ===计算实际下单量和实际下单资金
        order_df['实际下单量'] = order_df['目标持仓量'] - order_df['当前持仓量']

        # ===计算下单的模式，清仓、建仓、调仓等
        order_df = order_df[order_df['实际下单量'] != 0]  # 过滤掉实际下当量为0的数据
        if not order_df.empty:
            order_df.loc[order_df['目标持仓量'] == 0, '交易模式'] = '清仓'
            order_df.loc[order_df['当前持仓量'] == 0, '交易模式'] = '建仓'
            order_df['交易模式'].fillna(value='调仓', inplace=True)  # 增加或者减少原有的持仓，不会降为0

        symbol_spot_price = self.bn.get_spot_ticker_price_series()  # 获取现货的最新价格
        symbol_spot_price = symbol_spot_price.to_frame().reset_index()
        symbol_spot_price['symbol_type'] = 'spot'

        symbol_swap_price = self.bn.get_swap_ticker_price_series()  # 获取合约的最新价格
        symbol_swap_price = symbol_swap_price.to_frame().reset_index()
        symbol_swap_price['symbol_type'] = 'swap'

        # 实际下单资金 = 实际下单量 * 价格
        symbol_price = pd.concat([symbol_spot_price, symbol_swap_price], ignore_index=True, copy=False)
        symbol_price.set_index(['symbol', 'symbol_type'], inplace=True)
        order_df = order_df.join(symbol_price, how='left')
        order_df['实际下单资金'] = order_df['price'] * order_df['实际下单量']
        order_df.drop(columns='price', inplace=True)

        # 对没有报价的 symbol，设置 实际下单资金 为1，进行容错
        order_df['实际下单资金'] = order_df['实际下单资金'].fillna(1.)

        return order_df.reset_index()

    def calc_spot_need_usdt_amount(self, is_select_empty, spot_order):
        """
        计算现货账号需要划转多少usdt过去
        """
        # 获取需要计算的数据
        if self.account_type == AccountType.PORTFOLIO_MARGIN:
            return -1  # 统一账户不需要进行划转操作，这里直接返回-1。方便跳过后面操作

        # 现货下单总资金
        spot_strategy_equity = 0 if is_select_empty else spot_order[spot_order['实际下单资金'] > 0][
            '实际下单资金'].sum()

        # 计算现货下单总资金 与 当前现货的资金差值，需要补充（这里是多加2%的滑点）
        diff_equity = spot_strategy_equity * 1.02

        # 获取合约账户中可以划转的USDT数量
        swap_assets = self.bn.get_swap_account()  # 获取账户净值
        swap_assets = pd.DataFrame(swap_assets['assets'])
        swap_max_withdraw_amount = float(
            swap_assets[swap_assets['asset'] == 'USDT']['maxWithdrawAmount'])  # 获取可划转USDT数量
        swap_max_withdraw_amount = swap_max_withdraw_amount * 0.99  # 出于安全考虑，给合约账户预留1%的保证金

        # 计算可以划转的USDT数量
        transfer_amount = min(diff_equity, swap_max_withdraw_amount)
        # 现货需要的USDT比可划转金额要大，这里发送信息警告(前提：非纯多现货模式下)
        if not self.is_pure_long and diff_equity > swap_max_withdraw_amount:
            msg = '======警告======\n\n'
            msg += f'现货所需金额:{diff_equity:.2f}\n'
            msg += f'合约可划转金额:{swap_max_withdraw_amount:.2f}\n'
            msg += '划转资金不足，可能会造成现货下单失败！！！'
            # 重复发送五次
            for i in range(0, 5, 1):
                send_wechat_work_msg(msg, self.wechat_webhook_url)
                time.sleep(3)

        return transfer_amount

    def proceed_swap_order(self, orders_df: pd.DataFrame) -> list:
        """
        处理合约下单
        :param orders_df:    下单数据
        :return: 本轮最终仍然失败的订单列表（每一批重试之后依旧失败的）
        """
        # ===拆分订单
        swap_order = orders_df[orders_df['symbol_type'] == 'swap'].copy()
        orders_df_list = split_order_balanced(swap_order, self.max_one_order_amount)

        # ===遍历下单
        final_failed = []
        for i in range(len(orders_df_list)):
            # 逐批下单
            final_failed += (self.bn.place_swap_orders_bulk2(orders_df_list[i]) or [])
            # 下单间隔
            logger.info(f'等待 {self.twap_interval}s 后继续下单')
            time.sleep(self.twap_interval)

        if final_failed:
            failed_df = pd.DataFrame(final_failed)
            logger.error(f'[{self.name}] 本轮合约下单最终失败 {len(final_failed)} 笔：\n{failed_df}')
            msg = (f'⚠️ [{self.name}] 调仓后有 {len(final_failed)} 笔合约订单最终失败，仓位可能多空不平衡：\n'
                  + '\n'.join(f"{r.get('symbol')} {r.get('交易模式')} {r.get('实际下单资金', 0):.1f}U"
                              for r in final_failed[:20]))
            send_wechat_work_msg(msg, self.wechat_webhook_url)
        return final_failed

    def proceed_spot_order(self, orders_df, is_select_empty, is_only_sell=False) -> list:
        """
        处理现货下单
        :param orders_df:    下单数据
        :param is_select_empty:     是否没有选币结果
        :param is_only_sell:    是否仅仅进行卖单交易
        :return: 本轮最终仍然失败的订单列表
        """
        # ===现货处理
        spot_order_df = orders_df[orders_df['symbol_type'] == 'spot'].copy()

        # 判断是否需要现货下单
        if spot_order_df.empty:  # 如果使用了现货数据实盘，则进行现货下单
            return []

        # =拆分订单
        short_order = spot_order_df[spot_order_df['实际下单资金'] <= 0]
        long_order = spot_order_df[spot_order_df['实际下单资金'] > 0]
        # 判断是否只卖现货
        if is_only_sell:  # 如果是仅仅交易卖单，只进行拆单
            orders_df_list = split_order_balanced(short_order, self.max_one_order_amount)
        else:  # 如果是仅仅交易买单，拆单，划转资金去现货账户
            orders_df_list = split_order_balanced(long_order, self.max_one_order_amount)

            # 计算现货所需USDT，从合约账户转U到现货账户
            time.sleep(5)
            transfer_amount = self.calc_spot_need_usdt_amount(is_select_empty, spot_order_df)
            logger.info(f'transfer_amount {transfer_amount}')
            if transfer_amount > 0:
                # 转资金到现货账户
                self.bn.transfer_u_from_swap_to_spot(round(transfer_amount - 1, 1))  # 避免四舍五入的问题

        # =现货遍历下单
        final_failed = []
        for i in range(len(orders_df_list)):
            # 逐批下单
            final_failed += (self.bn.place_spot_orders_bulk2(orders_df_list[i]) or [])
            # 下单间隔
            logger.info(f'等待 {self.twap_interval}s 后继续下单')
            time.sleep(self.twap_interval)

        # ===归集资产
        self.bn.collect_asset()

        if final_failed:
            failed_df = pd.DataFrame(final_failed)
            logger.error(f'[{self.name}] 本轮现货下单最终失败 {len(final_failed)} 笔：\n{failed_df}')
            msg = (f'⚠️ [{self.name}] 调仓后有 {len(final_failed)} 笔现货订单最终失败：\n'
                  + '\n'.join(f"{r.get('symbol')} {r.get('交易模式')} {r.get('实际下单资金', 0):.1f}U"
                              for r in final_failed[:20]))
            send_wechat_work_msg(msg, self.wechat_webhook_url)
        return final_failed
