"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
# ==================================================================================================
# !!! 前置非常重要说明
# !!! 前置非常重要说明
# !!! 前置非常重要说明
# ---------------------------------------------------------------------------------------------------
# ** 帐户说明 **
# spot：对于普通账户来说，是纯现货；对于统一账户来说是margin
# swap：对于普通账户和统一账户来说，都是 um swap
# ---------------------------------------------------------------------------------------------------
# ** 方法名前缀规范 **
# 1. load_* 从硬盘获取数据
# 2. fetch_* 从接口获取数据
# 3. get_* 从对象获取数据，可能从硬盘，也可能从接口
# ====================================================================================================

import math
import os
import time
import traceback
from datetime import datetime, timedelta

import ccxt
import numpy as np
import pandas as pd
from core.utils.commons import retry_wrapper
from core.utils.encryptor import AESEncryptor
from core.utils.log_kit import logger

from config import exchange_basic_config, utc_offset, stable_symbol, error_webhook_url
from core.utils.commons import apply_precision
from core.utils.dingding import send_wechat_work_msg, send_msg_for_order
from core.utils.log_kit import divider


# 现货接口
# sapi

# 合约接口
# dapi：普通账户，包含币本位交易
# fapi，普通账户，包含U本位交易

# 统一账户
# papi, um的接口：U本位合约
# papi, cm的接口：币本位合约
# papi, margin：现货API，全仓杠杆现货

class BinanceClient:
    diff_timestamp = 0
    constants = dict()

    market_info = {}  # 缓存市场信息，并且自动更新，全局共享
    common_exchange = ccxt.binance(exchange_basic_config)

    def __init__(self, **config):
        self.api_key: str = config.get('apiKey', '')
        self.secret: str = config.get('secret', '')

        self.order_money_limit: dict = {
            'spot': config.get('spot_order_money_limit', 10),
            'swap': config.get('swap_order_money_limit', 5),
        }

        exchange_config = config.get('exchange_config', exchange_basic_config)
        self.exchange = ccxt.binance(self.decrypt(exchange_config.copy()))
        self.wechat_webhook_url: str = config.get('wechat_webhook_url', '')

        self.swap_account = None

        self.coin_margin: dict = config.get('coin_margin', {})  # 用做保证金的币种

        self.base_margin: dict = config.get('base_margin', {'USDT': 1})  # 额外基础保证金

        self.base_coin_amount: dict = {base_coin: 0 for base_coin in self.base_margin.keys()}

        self._swap_ticker_price = {}
        self._spot_ticker_price = {}

    @property
    def base_coins(self):
        return list(self.base_margin.keys())

    @staticmethod
    def decrypt(exchange_config):
        # 从环境变量中获取密钥
        x3s_trading_secret_key = os.getenv('X3S_TRADING_SECRET_KEY')

        if x3s_trading_secret_key:
            encryptor = AESEncryptor(x3s_trading_secret_key)
            if 'apiKey' in exchange_config and 'secret' in exchange_config and not exchange_config.get('decrypt', False):
                try:
                    exchange_config['apiKey'] = encryptor.decrypt(exchange_config['apiKey'])
                    exchange_config['secret'] = encryptor.decrypt(exchange_config['secret'])
                    exchange_config['decrypt'] = True
                except Exception as e:
                    logger.error(e)
                    logger.error(traceback.format_exc())
                    logger.warning(f'apikey:{exchange_config["apiKey"]}, secret:{exchange_config["secret"]}')
                    logger.error(f'解密apiKey和secret发生错误，请检查配置是否正确，程序自动退出。')
                    send_wechat_work_msg('解密apiKey和secret发生错误，请检查配置是否正确', error_webhook_url)
                    exit(1)

        return exchange_config

    # ====================================================================================================
    # ** 市场信息 **
    # ====================================================================================================
    def _fetch_swap_exchange_info_list(self) -> list:
        exchange_info = retry_wrapper(self.exchange.fapipublic_get_exchangeinfo, func_name='获取BN合约币种规则数据')
        return exchange_info['symbols']

    def _fetch_spot_exchange_info_list(self) -> list:
        exchange_info = retry_wrapper(self.exchange.public_get_exchangeinfo, func_name='获取BN现货币种规则数据')
        return exchange_info['symbols']

    # region 市场信息数据获取
    def fetch_market_info(self, symbol_type='swap', quote_symbol='USDT'):
        """
        加载市场数据
        :param symbol_type: 币种信息。swap为合约，spot为现货
        :param quote_symbol: 报价币种
        :return:
            symbol_list     交易对列表
            price_precision 币种价格精     例： 2 代表 0.01
                {'BTCUSD_PERP': 1, 'BTCUSD_231229': 1, 'BTCUSD_240329': 1, 'BTCUSD_240628': 1, ...}
            min_notional    最小下单金额    例： 5.0 代表 最小下单金额是5U
                {'BTCUSDT': 5.0, 'ETHUSDT': 5.0, 'BCHUSDT': 5.0, 'XRPUSDT': 5.0...}
        """
        logger.debug(f'🔢 更新{symbol_type}市场数据...')
        # ===获取所有币种信息
        if symbol_type == 'swap':  # 合约
            exchange_info_list = self._fetch_swap_exchange_info_list()
        else:  # 现货
            exchange_info_list = self._fetch_spot_exchange_info_list()

        # ===获取币种列表
        symbol_list = []  # 如果是合约，只包含永续合约。如果是现货，包含所有数据
        full_symbol_list = []  # 包含所有币种信息

        # ===获取各个交易对的精度、下单量等信息
        min_qty = {}  # 最小下单精度，例如bnb，一次最少买入0.001个
        price_precision = {}  # 币种价格精，例如bnb，价格是158.887，不能是158.8869
        min_notional = {}  # 最小下单金额，例如bnb，一次下单至少买入金额是5usdt
        # 遍历获得想要的数据
        for info in exchange_info_list:
            symbol = info['symbol']  # 交易对信息

            # 过滤掉非报价币对 ， 非交易币对
            if info['quoteAsset'] != quote_symbol or info['status'] != 'TRADING':
                continue

            # 过滤盘前交易币种
            if 'permissionSets' in info and len(info['permissionSets']) > 1 and 'PRE_MARKET' in info['permissionSets'][
                1]:
                continue

            full_symbol_list.append(symbol)  # 添加到全量信息中

            if (symbol_type == 'swap' and info['contractType'] != 'PERPETUAL') or info['baseAsset'] in stable_symbol:
                pass  # 获取合约的时候，非永续的symbol会被排除
            else:
                symbol_list.append(symbol)

            for _filter in info['filters']:  # 遍历获得想要的数据
                if _filter['filterType'] == 'PRICE_FILTER':  # 获取价格精度
                    price_precision[symbol] = int(math.log(float(_filter['tickSize']), 0.1))
                elif _filter['filterType'] == 'LOT_SIZE':  # 获取最小下单量
                    min_qty[symbol] = int(math.log(float(_filter['minQty']), 0.1))
                elif _filter['filterType'] == 'MIN_NOTIONAL' and symbol_type == 'swap':  # 合约的最小下单金额
                    min_notional[symbol] = float(_filter['notional'])
                elif _filter['filterType'] == 'NOTIONAL' and symbol_type == 'spot':  # 现货的最小下单金额
                    min_notional[symbol] = float(_filter['minNotional'])

        self.market_info[symbol_type] = {
            'symbol_list': symbol_list,  # 如果是合约，只包含永续合约。如果是现货，包含所有数据
            'full_symbol_list': full_symbol_list,  # 包含所有币种信息
            'min_qty': min_qty,
            'price_precision': price_precision,
            'min_notional': min_notional,
            'last_update': int(time.time())
        }
        return self.market_info[symbol_type]

    def get_market_info(self, symbol_type, expire_seconds: int = 3600 * 12, require_update: bool = False,
                        quote_symbol='USDT') -> dict:
        if require_update:  # 如果强制刷新的话，就当我们系统没有更新过
            last_update = 0
        else:
            last_update = self.market_info.get(symbol_type, {}).get('last_update', 0)
        if last_update + expire_seconds < int(time.time()):
            self.fetch_market_info(symbol_type, quote_symbol)

        return self.market_info[symbol_type]

    # endregion

    # ====================================================================================================
    # ** 行情数据获取 **
    # ====================================================================================================
    # region 行情数据获取
    """K线数据获取"""

    def get_candle_df(self, symbol, run_time, limit=1500, interval='1h', symbol_type='swap') -> pd.DataFrame:
        # ===获取K线数据
        _limit = limit
        # 定义请求的参数：现货最大1000，合约最大499。
        if limit > 1000:  # 如果参数大于1000
            if symbol_type == 'spot':  # 如果是现货，最大设置1000
                _limit = 1000
            else:  # 如果不是现货，那就设置499
                _limit = 499
        # limit = 1000 if limit > 1000 and symbol_type == 'spot' else limit  # 现货最多获取1000根K
        # 计算获取k线的开始时间
        start_time_dt = run_time - pd.to_timedelta(interval) * limit

        df_list = []  # 定义获取的k线数据
        data_len = 0  # 记录数据长度
        params = {
            'symbol': symbol,  # 获取币种
            'interval': interval,  # 获取k线周期
            'limit': _limit,  # 获取多少根
            'startTime': int(time.mktime(start_time_dt.timetuple())) * 1000  # 获取币种开始时间
        }
        while True:
            # 获取指定币种的k线数据
            try:
                if symbol_type == 'swap':
                    kline = retry_wrapper(
                        self.exchange.fapipublic_get_klines, params=params, func_name='获取币种K线',
                        if_exit=False
                    )
                else:
                    kline = retry_wrapper(
                        self.exchange.public_get_klines, params=params, func_name='获取币种K线',
                        if_exit=False
                    )
            except Exception as e:
                logger.error(e)
                logger.error(traceback.format_exc())
                # 如果获取k线重试出错，直接返回，当前币种不参与交易
                return pd.DataFrame()

            # ===整理数据
            # 将数据转换为DataFrame
            df = pd.DataFrame(kline, dtype='float')
            if df.empty:
                break
            # 对字段进行重命名，字段对应数据可以查询文档（https://binance-docs.github.io/apidocs/futures/cn/#k）
            columns = {0: 'candle_begin_time', 1: 'open', 2: 'high', 3: 'low', 4: 'close', 5: 'volume', 6: 'close_time',
                       7: 'quote_volume',
                       8: 'trade_num', 9: 'taker_buy_base_asset_volume', 10: 'taker_buy_quote_asset_volume',
                       11: 'ignore'}
            df.rename(columns=columns, inplace=True)
            df['candle_begin_time'] = pd.to_datetime(df['candle_begin_time'], unit='ms')
            df.sort_values(by=['candle_begin_time'], inplace=True)  # 排序

            # 数据追加
            df_list.append(df)
            data_len = data_len + df.shape[0] - 1

            # 判断请求的数据是否足够
            if data_len >= limit:
                break

            if params['startTime'] == int(df.iloc[-1]['candle_begin_time'].timestamp()) * 1000:
                break

            # 更新一下k线数据
            params['startTime'] = int(df.iloc[-1]['candle_begin_time'].timestamp()) * 1000
            # 下载太多的k线的时候，中间sleep一下
            time.sleep(0.1)

        if not df_list:
            return pd.DataFrame()

        all_df = pd.concat(df_list, ignore_index=True, copy=False)
        all_df['symbol'] = symbol  # 添加symbol列
        all_df['symbol_type'] = symbol_type  # 添加类型字段
        all_df.sort_values(by=['candle_begin_time'], inplace=True)  # 排序
        all_df.drop_duplicates(subset=['candle_begin_time'], keep='last', inplace=True)  # 去重

        # 删除runtime那根未走完的k线数据（交易所有时候会返回这条数据）
        all_df = all_df[all_df['candle_begin_time'] + pd.Timedelta(hours=utc_offset) < run_time]
        all_df.reset_index(drop=True, inplace=True)

        return all_df

    def get_candle_df_bulk(self, symbol_list, run_time, limit, interval='1h', symbol_type='swap') -> dict:
        """
        获取所有币种永续合约数据的1天K线数据

        :param symbol_list: 币种泪飙
        :param run_time:    当前运行时间
        :param limit:       请求k线数量
        :param interval:    获取k线的周期
        :param symbol_type: 请求数据类型swap/spot
        :return:

        {
        'BTCUSDT':
                                symbol  ... taker_buy_quote_asset_volume
                0     BTCUSDT  ...                 1.451404e+08
                1     BTCUSDT  ...                 1.492456e+08
                2     BTCUSDT  ...                 1.200780e+08
                3     BTCUSDT  ...                 9.680288e+07
                4     BTCUSDT  ...                 6.867702e+08
                ...       ...  ...                          ...
                1495  BTCUSDT  ...                 1.858995e+08
                1496  BTCUSDT  ...                 1.151737e+08
                1497  BTCUSDT  ...                 8.091855e+07
                1498  BTCUSDT  ...                 1.037028e+08
                1499  BTCUSDT  ...                 1.111743e+07,

        'ETHUSDT':
                symbol  ... taker_buy_quote_asset_volume
                0     ETHUSDT  ...                 2.023519e+08
                1     ETHUSDT  ...                 1.813869e+08
                2     ETHUSDT  ...                 1.298206e+08
                3     ETHUSDT  ...                 1.544976e+08
                4     ETHUSDT  ...                 6.494550e+08
                ...       ...  ...                          ...
                1495  ETHUSDT  ...                 2.792866e+08
                1496  ETHUSDT  ...                 1.220917e+08
                1497  ETHUSDT  ...                 7.935349e+07
                1498  ETHUSDT  ...                 1.557781e+08
                1499  ETHUSDT  ...                 2.241793e+07,
        ......
        }
        """
        # 这里用dict存储，方便后面数据处理操作
        result = {}
        for symbol in symbol_list:
            # 获取k线数据
            df = self.get_candle_df(symbol, run_time, limit, interval=interval, symbol_type=symbol_type)
            # 返回None或者空的df，不放到result里
            if df is None or df.empty:
                continue
            # 将数据添加到result中
            result[symbol] = df

        return result

    """最新报价数据获取"""

    def fetch_ticker_price(self, symbol: str = None, symbol_type: str = 'swap') -> dict:
        params = {'symbol': symbol} if symbol else {}
        match symbol_type:
            case 'spot':
                api_func = self.exchange.public_get_ticker_price
                func_name = f'获取{symbol}现货的ticker数据' if symbol else '获取所有现货币种的ticker数据'
            case 'swap':
                api_func = self.exchange.fapipublic_get_ticker_price
                func_name = f'获取{symbol}合约的ticker数据' if symbol else '获取所有合约币种的ticker数据'
            case _:
                raise ValueError(f'未知的symbol_type：{symbol_type}')

        tickers = retry_wrapper(api_func, params=params, func_name=func_name)
        return tickers

    def fetch_spot_ticker_price(self, spot_symbol: str = None) -> dict:
        return self.fetch_ticker_price(spot_symbol, symbol_type='spot')

    def fetch_swap_ticker_price(self, swap_symbol: str = None) -> dict:
        return self.fetch_ticker_price(swap_symbol, symbol_type='swap')

    def get_spot_ticker_price_series(self) -> pd.Series:
        ticker_price_df = pd.DataFrame(self.fetch_ticker_price(symbol_type='spot'))
        ticker_price_df['price'] = pd.to_numeric(ticker_price_df['price'], errors='coerce')
        self._spot_ticker_price = ticker_price_df.set_index(['symbol'])['price']
        return self._spot_ticker_price

    def get_swap_ticker_price_series(self) -> pd.Series:
        ticker_price_df = pd.DataFrame(self.fetch_ticker_price(symbol_type='swap'))
        ticker_price_df['price'] = pd.to_numeric(ticker_price_df['price'], errors='coerce')
        self._swap_ticker_price = ticker_price_df.set_index(['symbol'])['price']
        return self._swap_ticker_price

    """盘口数据获取"""

    def fetch_book_ticker(self, symbol, symbol_type='swap') -> dict:
        if symbol_type == 'swap':
            # 获取合约的盘口数据
            swap_book_ticker_data = retry_wrapper(
                self.exchange.fapiPublicGetTickerBookTicker, params={'symbol': symbol}, func_name='获取合约盘口数据')
            return swap_book_ticker_data
        else:
            # 获取现货的盘口数据
            spot_book_ticker_data = retry_wrapper(
                self.exchange.publicGetTickerBookTicker, params={'symbol': symbol}, func_name='获取现货盘口数据'
            )
            return spot_book_ticker_data

    def fetch_spot_book_ticker(self, spot_symbol) -> dict:
        return self.fetch_book_ticker(spot_symbol, symbol_type='spot')

    def fetch_swap_book_ticker(self, swap_symbol) -> dict:
        return self.fetch_book_ticker(swap_symbol, symbol_type='swap')

    def fetch_spot_swap_sell1_buy1(self, spot_symbol, swap_symbol) -> dict:
        # 获取现货的盘口数据
        spot_book_ticker_data = self.fetch_spot_book_ticker(spot_symbol)

        # 获取合约的盘口数据
        swap_book_ticker_data = self.fetch_swap_book_ticker(swap_symbol)
        return {
            'spot': {
                'sell1': spot_book_ticker_data['askPrice'],
                'buy1': spot_book_ticker_data['bidPrice'],
            },
            'swap': {
                'sell1': swap_book_ticker_data['askPrice'],
                'buy1': swap_book_ticker_data['bidPrice'],
            }
        }

    # endregion

    # ====================================================================================================
    # ** 资金费数据 **
    # ====================================================================================================
    def get_premium_index_df(self) -> pd.DataFrame:
        """
        获取币安的最新资金费数据
        """
        last_funding_df = retry_wrapper(self.exchange.fapipublic_get_premiumindex, func_name='获取最新的资金费数据')
        last_funding_df = pd.DataFrame(last_funding_df)

        last_funding_df['nextFundingTime'] = pd.to_numeric(last_funding_df['nextFundingTime'], errors='coerce')
        last_funding_df['time'] = pd.to_numeric(last_funding_df['time'], errors='coerce')

        last_funding_df['nextFundingTime'] = pd.to_datetime(last_funding_df['nextFundingTime'], unit='ms')
        last_funding_df['time'] = pd.to_datetime(last_funding_df['time'], unit='ms')
        last_funding_df = last_funding_df[['symbol', 'nextFundingTime', 'lastFundingRate']]  # 保留部分字段
        last_funding_df.rename(columns={'nextFundingTime': 'fundingTime', 'lastFundingRate': 'fundingRate'},
                               inplace=True)

        return last_funding_df

    def get_funding_rate_df(self, symbol, limit=1000) -> pd.DataFrame:
        """
        获取币安的历史资金费数据
        :param symbol: 币种名称
        :param limit: 请求获取多少条数据，最大1000
        """
        param = {'symbol': symbol, 'limit': limit}
        # 获取历史数据
        try:
            funding_df = retry_wrapper(
                self.exchange.fapipublic_get_fundingrate, params=param,
                func_name='获取合约历史资金费数据'
            )
        except Exception as e:
            logger.debug(e)
            return pd.DataFrame()
        funding_df = pd.DataFrame(funding_df)
        if funding_df.empty:
            return funding_df

        funding_df['fundingTime'] = pd.to_datetime(funding_df['fundingTime'].astype(float) // 1000 * 1000,
                                                   unit='ms')  # 时间戳内容含有一些纳秒数据需要处理
        funding_df.sort_values('fundingTime', inplace=True)

        return funding_df

    # ====================================================================================================
    # ** 账户设置 **
    # ====================================================================================================
    def _set_position_side(self):
        raise NotImplementedError

    def set_single_side_position(self):
        raise NotImplementedError

    def set_dual_side_position(self):
        raise NotImplementedError

    def set_multi_assets_margin(self):
        """
        检查是否开启了联合保证金模式
        """
        # 查询保证金模式
        pass

    def reset_max_leverage(self, max_leverage=5, coin_list=()):
        """
        重置一下页面最大杠杆
        :param max_leverage:    设置页面最大杠杆
        :param coin_list:       对指定币种进行调整页面杠杆
        """
        """
        重置一下页面最大杠杆
        :param exchange:        交易所对象，用于获取数据
        :param max_leverage:    设置页面最大杠杆
        :param coin_list:       对指定币种进行调整页面杠杆
        """
        # 获取账户持仓风险（这里有杠杆数据）
        account_info = self.get_swap_account()
        if account_info is None:
            logger.info('获取账户持仓风险数据为空')
            exit(1)

        position_risk = pd.DataFrame(account_info['positions'])  # 将数据转成DataFrame
        if len(coin_list) > 0:
            position_risk = position_risk[position_risk['symbol'].isin(coin_list)]  # 只对选币池中的币种进行调整页面杠杆
        position_risk.set_index('symbol', inplace=True)  # 将symbol设为index

        # 遍历每一个可以持仓的币种，修改页面最大杠杆
        for symbol, row in position_risk.iterrows():
            if int(row['leverage']) != max_leverage:
                reset_leverage_func = getattr(self.exchange, self.constants.get('reset_page_leverage_api'))
                # 设置杠杆
                retry_wrapper(
                    reset_leverage_func,
                    params={'symbol': symbol, 'leverage': max_leverage, 'timestamp': ''},
                    func_name='设置杠杆'
                )

    def get_unimmr(self):
        raise NotImplementedError

    # ====================================================================================================
    # ** 交易函数 **
    # ====================================================================================================
    def cancel_all_spot_orders(self, symbol_list=None):
        # 现货撤单
        get_spot_open_orders_func = getattr(self.exchange, self.constants.get('get_spot_open_orders_api'))
        orders = retry_wrapper(
            get_spot_open_orders_func,
            params={'timestamp': ''}, func_name='查询现货所有挂单'
        )
        symbols = {_['symbol'] for _ in orders}

        # 确定目标交易对列表
        if symbol_list:
            # 筛选出同时存在于symbols和symbol_list中的交易对
            target_symbols = [s for s in symbol_list if s in symbols]
        else:
            # 保持原有逻辑，处理所有交易对
            target_symbols = list(symbols)

        # 如果没有需要处理的交易对，提前返回
        if not target_symbols:
            return

        logger.info(f'取消现货挂单: {target_symbols}')
        cancel_spot_open_orders_func = getattr(self.exchange, self.constants.get('cancel_spot_open_orders_api'))
        for _ in target_symbols:
            retry_wrapper(
                cancel_spot_open_orders_func,
                params={'symbol': _, 'timestamp': ''}, func_name='取消现货挂单'
            )

    def cancel_all_swap_orders(self, symbol_list=None):
        # 合约撤单
        get_swap_open_orders_func = getattr(self.exchange, self.constants.get('get_swap_open_orders_api'))
        orders = retry_wrapper(
            get_swap_open_orders_func,
            params={'timestamp': ''}, func_name='查询U本位合约所有挂单'
        )
        symbols = {_['symbol'] for _ in orders}

        # 确定目标交易对列表
        if symbol_list:
            # 筛选出同时存在于symbols和symbol_list中的交易对
            target_symbols = [s for s in symbol_list if s in symbols]
        else:
            # 保持原有逻辑，处理所有交易对
            target_symbols = list(symbols)

        # 如果没有需要处理的交易对，提前返回
        if not target_symbols:
            return

        logger.info(f'取消合约挂单: {target_symbols}')
        cancel_swap_open_orders_func = getattr(self.exchange, self.constants.get('cancel_swap_open_orders_api'))
        for _ in target_symbols:
            retry_wrapper(
                cancel_swap_open_orders_func,
                params={'symbol': _, 'timestamp': ''}, func_name='取消U本位合约挂单'
            )

    def prepare_order_params_list(
            self, orders_df: pd.DataFrame, symbol_type: str, symbol_ticker_price: pd.Series,
            slip_rate: float = 0.015) -> list:
        """
        根据策略产生的订单数据，构建每个币种的下单参数
        TODO: 完成一下order list的数据结构
        :param orders_df: 策略产生的订单信息
        :param symbol_type: 下单类型。spot/swap
        :param symbol_ticker_price: 每个币种最新价格
        :param slip_rate: 滑点
        :return: order_params_list 每个币种的下单参数
        """
        orders_df.sort_values('实际下单资金', ascending=True, inplace=True)
        orders_df.set_index('symbol', inplace=True)  # 重新设置index

        market_info = self.get_market_info(symbol_type)
        min_qty = market_info['min_qty']
        price_precision = market_info['price_precision']
        min_notional = market_info['min_notional']

        # 遍历symbol_order，构建每个币种的下单参数
        order_params_list = []
        for symbol, row in orders_df.iterrows():
            # ===若当前币种没有最小下单精度、或最小价格精度，报错
            if (symbol not in min_qty) or (symbol not in price_precision):
                # 报错
                logger.error(f'当前币种{symbol}没有最小下单精度、或最小价格精度，币种信息异常')
                continue

            # ===计算下单量、方向、价格
            quantity = row['实际下单量']
            # 按照最小下单量对合约进行四舍五入，对现货就低不就高处理
            # 注意点：合约有reduceOnly参数可以超过你持有的持仓量，现货不行，只能卖的时候留一点点残渣
            quantity = round(quantity, min_qty[symbol]) if symbol_type == 'swap' else apply_precision(quantity,
                                                                                                      min_qty[symbol])
            # 计算下单方向、价格，并增加一定的滑点
            if quantity > 0:
                side = 'BUY'
                price = symbol_ticker_price[symbol] * (1 + slip_rate)
            elif quantity < 0:
                side = 'SELL'
                price = symbol_ticker_price[symbol] * (1 - slip_rate)
            else:
                logger.warning('下单量为0，不进行下单')
                continue
            # 下单量取绝对值
            quantity = abs(quantity)
            # 通过最小价格精度对下单价格进行四舍五入
            price = round(price, price_precision[symbol])

            # ===判断是否是清仓交易
            reduce_only = True if row['交易模式'] == '清仓' else False

            # ===判断交易金额是否小于最小下单金额（一般是5元），小于的跳过
            if (quantity * price < min_notional.get(symbol, self.order_money_limit[symbol_type]) or
                    quantity * price < self.order_money_limit[symbol_type]):
                if not reduce_only or symbol_type == 'spot':  # 清仓状态不跳过（现货除外）
                    logger.warning(f'{symbol}交易金额是小于最小下单金额（一般合约是5元，现货是10元），跳过该笔交易')
                    logger.info(f'下单量：{quantity},价格：{price}')
                    continue

            # ===构建下单参数
            price = f'{price:.{price_precision[symbol]}f}'  # 根据精度将价格转成str
            quantity = np.format_float_positional(quantity).rstrip('.')  # 解决科学计数法的问题
            order_params = {
                'symbol': symbol,
                'side': side,
                'type': 'LIMIT',
                'price': price,
                'quantity': quantity,
                'newClientOrderId': str(int(time.time())),
                'timeInForce': 'GTC',
                'reduceOnly': str(bool(reduce_only)),
                'selfTradePreventionMode': 'EXPIRE_MAKER',  # BN 2024/12/10 限制，传 NONE 订单会被拒绝，这里使用默认值
                'newOrderRespType': 'FULL' if symbol_type == 'spot' else 'RESULT',
                'timestamp': ''
            }
            # 如果是合约下单，添加进行下单列表中，放便后续批量下单
            order_params_list.append(order_params)
        return order_params_list

    def prepare_order_params_and_place_order(
            self, orders_df: pd.DataFrame, symbol_type: str,
            slip_rate: float = 0.015, **kwargs) -> list:
        """
        根据策略产生的订单数据，构建每个币种的下单参数
        :param orders_df: 策略产生的订单信息
        :param symbol_type: 下单类型。spot/swap
        :param slip_rate: 滑点
        :return: order_params_list 每个币种的下单参数
        """
        orders_df.set_index('symbol', inplace=True)  # 重新设置index

        symbol_ticker_price = self._spot_ticker_price if symbol_type == 'spot' else self._swap_ticker_price
        market_info = self.get_market_info(symbol_type)
        min_qty = market_info['min_qty']
        price_precision = market_info['price_precision']
        min_notional = market_info['min_notional']

        order_error_list = []
        # 遍历symbol_order，构建每个币种的下单参数
        for symbol, row in orders_df.iterrows():
            # ===若当前币种没有最小下单精度、或最小价格精度，报错
            if (symbol not in min_qty) or (symbol not in price_precision):
                # 报错
                logger.error(f'当前币种{symbol}没有最小下单精度、或最小价格精度，币种信息异常')
                continue

            # ===计算下单量、方向、价格
            amount = row['拆单金额']
            # 计算下单方向、价格，并增加一定的滑点
            if amount > 0:
                side = 'BUY'
                price = symbol_ticker_price[symbol] * (1 + slip_rate)
            elif amount < 0:
                side = 'SELL'
                price = symbol_ticker_price[symbol] * (1 - slip_rate)
            else:
                logger.warning('下单金额为0，不进行下单')
                continue
            # 通过最小价格精度对下单价格进行四舍五入
            price = round(price, price_precision[symbol])

            # quantity = amount / price
            quantity = row['实际下单量']
            # 按照最小下单量对合约进行四舍五入，对现货就低不就高处理
            # 注意点：合约有reduceOnly参数可以超过你持有的持仓量，现货不行，只能卖的时候留一点点残渣
            quantity = round(quantity, min_qty[symbol]) if symbol_type == 'swap' else apply_precision(quantity,
                                                                                                      min_qty[symbol])
            # 下单量取绝对值
            quantity = abs(quantity)
            if quantity == 0:
                logger.warning('下单量为0，不进行下单')
                continue

            # ===判断是否是清仓交易
            reduce_only = True if row['交易模式'] == '清仓' else False

            # ===判断交易金额是否小于最小下单金额（一般是5元），小于的跳过
            if (quantity * price < min_notional.get(symbol, self.order_money_limit[symbol_type]) or
                    quantity * price < self.order_money_limit[symbol_type]):
                if not reduce_only or symbol_type == 'spot':  # 清仓状态不跳过（现货除外）
                    logger.warning(f'{symbol}交易金额是小于最小下单金额（一般合约是5元，现货是10元），跳过该笔交易')
                    logger.info(f'下单量：{quantity},价格：{price}')
                    continue

            # ===构建下单参数
            price = f'{price:.{price_precision[symbol]}f}'  # 根据精度将价格转成str
            quantity = np.format_float_positional(quantity).rstrip('.')  # 解决科学计数法的问题
            order_params = {
                'symbol': symbol,
                'side': side,
                'type': 'LIMIT',
                'price': price,
                'quantity': quantity,
                'newClientOrderId': str(int(time.time())),
                'timeInForce': 'IOC',
                'reduceOnly': str(bool(reduce_only)),
                'selfTradePreventionMode': 'EXPIRE_MAKER',  # BN 2024/12/10 限制，传 NONE 订单会被拒绝，这里使用默认值
                'newOrderRespType': 'FULL' if symbol_type == 'spot' else 'RESULT',
                'timestamp': ''
            }
            # 直接下单
            if symbol_type == 'spot':
                del order_params['reduceOnly']
                order_error = self.place_spot_order(**order_params, **kwargs)
            else:
                order_error = self.place_swap_order(**order_params, **kwargs)
            # 返回值为空，下单报错，存在未成交部分，都记录到失败订单中
            if self._should_record_order_error(order_error):
                if order_error and self._has_partial_fill(order_error):
                    row['实际下单量'] = float(order_error['origQty']) - float(order_error['executedQty'])
                order_error_list.append(row)

        return order_error_list

    @staticmethod
    def _has_partial_fill(order_error):
        """检查是否存在部分成交"""
        return 'executedQty' in order_error and order_error['executedQty'] != order_error['origQty']

    def _should_record_order_error(self, order_error):
        """判断是否需要记录订单错误"""
        if not order_error:
            return True
        if 'msg' in order_error:
            return True
        if self._has_partial_fill(order_error):
            return True
        return False

    # 下单
    def place_orders(self, orders_df: pd.DataFrame, symbol_type: str, slip_rate: float = 0.015):
        """
        根据计算好的下单数据，进行下单

        :param orders_df:        计算好的币种下单数据
        :param symbol_type:         下单类型。swap/spot
        :param slip_rate:           滑点。默认0.015下单
        """
        # 小细节：现货下单需要先卖出，再买入(下单金额从小到大排序即可)
        match symbol_type:
            case 'spot':
                return self.place_spot_orders_bulk(orders_df, slip_rate)
            case 'swap':
                return self.place_swap_orders_bulk(orders_df, slip_rate)
            case _:
                raise NotImplementedError

    def place_spot_orders_bulk(self, orders_df, slip_rate=0.015):
        symbol_last_price = self.get_spot_ticker_price_series()
        order_params_list = self.prepare_order_params_list(orders_df, 'spot', symbol_last_price, slip_rate)

        result_list = []
        for order_param in order_params_list:
            del order_param['reduceOnly']  # 现货没有这个参数，进行移除
            result_list.append(self.place_spot_order(**order_param))

        return result_list

    def place_swap_orders_bulk(self, orders_df, slip_rate=0.015):
        symbol_last_price = self.get_swap_ticker_price_series()
        order_params_list = self.prepare_order_params_list(orders_df, 'swap', symbol_last_price, slip_rate)

        result_list = []
        for order_params in order_params_list:
            result_list.append(self.place_swap_order(**order_params))

        return result_list

    def place_spot_orders_bulk2(self, orders_df, slip_rate=0.015):
        self.get_swap_ticker_price_series()
        error_orders = self.prepare_order_params_and_place_order(orders_df, 'spot', slip_rate)

        # 一批次下单结束，再重试一次
        if error_orders:
            error_orders = pd.DataFrame(error_orders)
            error_orders.index.name = 'symbol'
            error_orders = error_orders.reset_index()
            self.prepare_order_params_and_place_order(error_orders, 'spot', slip_rate)

    def place_swap_orders_bulk2(self, orders_df, slip_rate=0.015):
        self.get_swap_ticker_price_series()
        error_orders = self.prepare_order_params_and_place_order(orders_df, 'swap', slip_rate)

        # 一批次下单结束，再重试一次
        if error_orders:
            error_orders = pd.DataFrame(error_orders)
            error_orders.index.name = 'symbol'
            error_orders = error_orders.reset_index()
            self.prepare_order_params_and_place_order(error_orders, 'swap', slip_rate)

    def place_spot_order(self, symbol, side, quantity, price=None, **kwargs) -> dict:
        divider(f'`{symbol}`现货下单 {side} {quantity}', '.')

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
            logger.info(f'现货下单参数：{params}')
            # 下单
            order_res = self.retry_wrapper_for_order(
                self.exchange.private_post_order,
                params=params,
                func_name='现货下单',
                symbol_type='spot',
            )
            logger.ok(f'现货下单完成，现货下单信息结果：{order_res}')
        except Exception as e:
            logger.error(f'现货下单出错：{e}')
            send_wechat_work_msg(
                f'现货 {symbol} 下单 {float(quantity) * float(price)}U 出错，请查看程序日志',
                self.wechat_webhook_url
            )
            return {}
            # 发送下单结果到钉钉
        send_msg_for_order([params], [order_res], self.wechat_webhook_url)
        return order_res

    def place_swap_order(self, symbol, side, quantity, price=None, reduce_only=False, **kwargs) -> dict:
        divider(f'`{symbol}`U本位合约下单 {side} {quantity}', '.')

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
            logger.info(f'U本位合约下单参数：{params}')
            # 下单
            order_res = self.retry_wrapper_for_order(
                self.exchange.fapiprivate_post_order,
                params=params,
                func_name='U本位合约下单',
                symbol_type='swap'
            )
            logger.ok(f'U本位合约下单完成，U本位合约下单信息结果：{order_res}')
        except Exception as e:
            logger.error(f'U本位合约下单出错：{e}')
            send_wechat_work_msg(
                f'U本位合约 {symbol} 下单 {float(quantity) * float(price)}U 出错，请查看程序日志',
                self.wechat_webhook_url
            )
            return {}
        send_msg_for_order([params], [order_res], self.wechat_webhook_url)
        return order_res

    def transfer_u_from_spot_to_swap(self, amount):
        raise NotImplementedError

    def transfer_u_from_swap_to_spot(self, amount):
        raise NotImplementedError

    def get_spot_position_df(self) -> pd.DataFrame:
        """
        获取账户净值


        :return:
            swap_equity=1000  (表示账户里资金总价值为 1000U )

        """
        # 获取U本位合约账户净值(不包含未实现盈亏)
        position_df = retry_wrapper(self.exchange.private_get_account, params={'timestamp': ''},
                                    func_name='获取现货账户净值')  # 获取账户净值
        position_df = pd.DataFrame(position_df['balances'])

        position_df['free'] = pd.to_numeric(position_df['free'])
        position_df['locked'] = pd.to_numeric(position_df['locked'])

        position_df['当前持仓量'] = position_df['free'] + position_df['locked']
        position_df = position_df[position_df['当前持仓量'] != 0]

        position_df.rename(columns={'asset': 'symbol'}, inplace=True)

        # 保留指定字段
        position_df = position_df[['symbol', '当前持仓量', 'free']]
        position_df['仓位价值'] = None  # 设置默认值

        return position_df

    # =====获取持仓
    # 获取币安账户的实际持仓
    def get_swap_position_df(self) -> pd.DataFrame:
        """
        获取币安账户的实际持仓

        :return:

                  当前持仓量   均价  持仓盈亏
        symbol
        RUNEUSDT       -82.0  1.208 -0.328000
        FTMUSDT        523.0  0.189  1.208156

        """
        # 获取原始数据
        get_swap_position_func = getattr(self.exchange, self.constants.get('get_swap_position_api'))
        position_df = retry_wrapper(get_swap_position_func, params={'timestamp': ''}, func_name='获取账户持仓风险')
        if position_df is None or len(position_df) == 0:
            return pd.DataFrame(columns=['symbol', '当前持仓量', '均价', '持仓盈亏', '当前标记价格', '仓位价值'])

        position_df = pd.DataFrame(position_df)  # 将原始数据转化为dataframe

        # 整理数据
        columns = {'positionAmt': '当前持仓量', 'entryPrice': '均价', 'unRealizedProfit': '持仓盈亏',
                   'markPrice': '当前标记价格'}
        position_df.rename(columns=columns, inplace=True)
        for col in columns.values():  # 转成数字
            position_df[col] = pd.to_numeric(position_df[col])

        position_df = position_df[position_df['当前持仓量'] != 0]  # 只保留有仓位的币种
        position_df.set_index('symbol', inplace=True)  # 将symbol设置为index
        position_df['仓位价值'] = position_df['当前持仓量'] * position_df['当前标记价格']

        # 保留指定字段
        position_df = position_df[['当前持仓量', '均价', '持仓盈亏', '当前标记价格', '仓位价值']]

        return position_df

    def update_swap_account(self) -> dict:
        self.swap_account = retry_wrapper(
            self.exchange.fapiprivatev2_get_account, params={'timestamp': ''},
            func_name='获取U本位合约账户信息'
        )
        return self.swap_account

    def get_swap_account(self, require_update: bool = False) -> dict:
        if self.swap_account is None or require_update:
            self.update_swap_account()
        return self.swap_account

    def get_swap_usdt_balance(self, asset='USDT') -> float:
        equity = self.get_swap_account()
        equity = pd.DataFrame(equity['assets'])
        if equity.empty:
            return 0

        if asset not in equity['asset'].to_list():
            return 0

        usdt_balance = float(equity[equity['asset'] == asset]['walletBalance'])  # 获取usdt资产
        equity = usdt_balance

        return equity

    def get_account_overview(self):
        raise NotImplementedError

    def transfer_bnb_for_dust_spot(self, dust_spot):
        """
        小额资产兑换成BNB，交易所规定6小时可以调用一次接口
        :param dust_spot:   当前账户的一些碎单
        :return:
        """
        # 没有碎单直接跳过
        if dust_spot.empty:
            return
        spot_account_type = self.constants.get('spot_account_type')

        # ===获取小额资产转换的历史(转换接口6H交易一次)
        res = retry_wrapper(self.exchange.sapiGetAssetDribblet, params={'accountType': spot_account_type, 'timestamp': ''},
                            func_name='获取小额资产转换的历史', if_exit=False)
        if res is None:
            time_list = []
        else:
            time_list = [int(_['operateTime']) for _ in res['userAssetDribblets']]
            time_list.sort(reverse=True)  # 从大到小排序

        # ===判断执行兑换BNB操作
        if_transfer_bnb = False
        if time_list:
            last_operate_time = datetime.fromtimestamp(time_list[0] / 1000)  # 将时间戳转成日期
            # 当前时间超过最后一次换BNB操作时间15天之后，才可以进行再次换BNB
            if datetime.now() > last_operate_time + timedelta(days=15):
                if_transfer_bnb = True
        else:  # 没有操作记录，表示近期没有兑换过，可以直接兑换
            if_transfer_bnb = True

        # ===执行兑换BNB
        if if_transfer_bnb:
            # 查询可以小额资产换BNB的列表
            res = retry_wrapper(
                self.exchange.sapiPostAssetDustBtc,
                params={'accountType': spot_account_type, 'timestamp': ''},
                func_name='获取可以转换成BNB的小额资产', if_exit=False
            )
            if res is None:
                return
            asset_list = [_['asset'] for _ in res['details']]  # 获取可以转换的列表（这个里面会包含你当前的其他持仓）
            # 获取持仓中的碎单
            dust_spot.reset_index(inplace=True)
            dust_list = [_.replace('USDT', '') for _ in dust_spot['symbol'].to_list()]  # 筛选处当前账户的碎单
            # 求出实际需要处理的币种
            common_list = set(asset_list).intersection(set(dust_list))  # 求出 asset_list 与 dust_list 的交集
            common_list = list(common_list)  # 数据转成list
            # 判断是否有币种需要进行兑换BNB
            if common_list:
                # 小额资产换BNB
                logger.info(f'小额资产换BNB参数:{common_list}')
                dust = retry_wrapper(
                    self.exchange.sapiPostAssetDust,
                    params={'asset': common_list, 'accountType': spot_account_type, 'timestamp': ''},
                    func_name='小额资产转换BNB', if_exit=False
                )
                logger.ok(f'小额资产换BNB完成，结果返回：{dust}')

    def replenish_bnb(self, buy_bnb_value, is_use_spot=True):
        """
        1.获取账户余额
        2.获取BNB余额
        3.计算需要购买BNB的量
        4.购买BNB
        5.平衡BNB
        """
        raise NotImplementedError

    def fetch_transfer_history(self, start_time=datetime.now()):
        """
        获取账户的划转记录
        """
        raise NotImplementedError

    def fetch_spot_trades(self, symbol, end_time) -> pd.DataFrame:
        # =设置获取订单时的参数
        params = {
            'symbol': symbol,  # 设置获取订单的币种
            'endTime': int(time.mktime(end_time.timetuple())) * 1000,  # 设置获取订单的截止时间
            'limit': 1000,  # 最大获取1000条订单信息
            'timestamp': ''
        }

        # =调用API获取订单信息
        get_spot_my_trades_func = getattr(self.exchange, self.constants.get('get_spot_my_trades_api'))
        trades = retry_wrapper(get_spot_my_trades_func, params=params, func_name='获取币种历史订单信息',
                               if_exit=False)  # 获取账户净值
        # 如果获取订单数据失败，进行容错处理，返回空df
        if trades is None:
            return pd.DataFrame()

        trades = pd.DataFrame(trades)  # 转成df格式
        # =如果获取到的该币种的订单数据是空的，则跳过，继续获取另外一个币种
        if trades.empty:
            return pd.DataFrame()

        # 转换数据格式
        for col in ('isBuyer', 'price', 'qty', 'quoteQty', 'commission'):
            trades[col] = pd.to_numeric(trades[col], errors='coerce')

        # =如果isBuyer为1则为买入，否则为卖出
        trades['方向'] = np.where(trades['isBuyer'] == 1, 1, -1)
        # =整理下有用的数据
        trades = trades[['time', 'symbol', 'price', 'qty', 'quoteQty', 'commission', 'commissionAsset', '方向']]

        return trades

    def adjust_base_margin(self):
        # ===额外基础保证金的比例动态调整
        # 比例波动阈值 ±5%（暂时写死）
        # 比例过高，全部币种按比列换成USDT。
        # 比例过低，只针对 BF 进行购买加仓。
        raise NotImplementedError

    def get_base_coin_equity(self):
        account_overview = self.get_account_overview()
        negative_balance = account_overview.get('negative_balance', 0)

        spot_ticker_data = self.fetch_spot_ticker_price()
        spot_ticker = {_['symbol']: float(_['price']) for _ in spot_ticker_data}
        # 遍历self.base_coin_amount，计算account_equity
        account_equity = sum([amount * spot_ticker.get(f'{base_coin}USDT', 1) for base_coin, amount in self.base_coin_amount.items()])
        account_equity += negative_balance
        return account_equity or 0

    def collect_asset(self, asset='USDT'):
        raise NotImplementedError

    def retry_order_param(self, params, symbol_type, slip_rate, attempt):
        market_info = self.market_info[symbol_type]
        price_precision = market_info['price_precision']
        symbol = params['symbol']

        symbol_ticker_price = self.get_swap_ticker_price_series() if symbol_type == 'swap' else self.get_spot_ticker_price_series()
        if params['side'] == 'BUY':
            price = symbol_ticker_price[symbol] * (1 + slip_rate * attempt)
        else:
            price = symbol_ticker_price[symbol] * (1 - slip_rate * attempt)
        price = round(price, price_precision[symbol])

        params['price'] = f'{price:.{price_precision[symbol]}f}'  # 根据精度将价格转成str
        return params

    def retry_wrapper_for_order(self, func, params=None, func_name='', retry_times=5, symbol_type='swap', slip_rate=0.015):
        """
        需要在出错时不断重试的函数，例如和交易所交互，可以使用本函数调用。
        :param func:            需要重试的函数名
        :param params:          参数
        :param func_name:       方法名称
        :param retry_times:     重试次数
        :param symbol_type:     币种类型
        :param slip_rate:       滑点
        :return:
        """
        if params is None:
            params = {}
        for attempt in range(retry_times):
            try:
                params['timestamp'] = int(time.time() * 1000) - self.diff_timestamp
                # 首次不刷新 ticker
                if attempt >= 1:
                    logger.warning(f"币种:{params['symbol']},下单量:{params['quantity']}，价格:{params['price']}，准备重试。")
                    params = self.retry_order_param(params, symbol_type, slip_rate, attempt)

                result = func(params=params)
                if params['timeInForce'] == 'IOC':
                    # 判断返回数据，如果没有完全成交/未成交，更新一下 params 继续重试
                    if result['executedQty'] != result['origQty']:
                        # 总下单量 - 已成交量
                        params['quantity'] = float(result['origQty']) - float(result['executedQty'])
                        logger.warning(f"下单量:{result['origQty']}, 成交量:{result['executedQty']}，未完全成交，更新下单量，准备重试。")
                        continue
                return result
            except Exception as e:
                logger.error(f'{func_name} 报错')
                logger.error(e)
                logger.debug(params)
                msg = str(e).strip()
                # 出现1021错误码之后，刷新与交易所的时差
                if '-1021' in msg:
                    from core.utils.functions import refresh_diff_time
                    refresh_diff_time()
                    logger.info('======时间刷新成功======')
                if 'binance Account has insufficient balance for requested action' in msg:
                    logger.warning(f'{func_name} 现货下单资金不足')
                    raise ValueError(func_name, '现货下单资金不足')
                elif '-2022' in msg:
                    logger.warning(f'{func_name} ReduceOnly订单被拒绝, 合约仓位已经平完')
                    raise ValueError(func_name, 'ReduceOnly订单被拒绝, 合约仓位已经平完')
                elif '-4118' in msg:
                    logger.warning(f'{func_name} 统一账户 ReduceOnly订单被拒绝, 合约仓位已经平完')
                    raise ValueError(func_name, '统一账户 ReduceOnly订单被拒绝, 合约仓位已经平完')
                elif '-2019' in msg:
                    logger.warning(f'{func_name} 合约下单资金不足')
                    raise ValueError(func_name, '合约下单资金不足')
                elif '-2015' in msg and 'Invalid API-key' in msg:
                    # {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action, request ip: xxx.xxx.xxx.xxx"}
                    logger.error(f'{func_name} API配置错误，可能写错或未配置权限')
                    break
                elif '-1121' in msg and 'Invalid symbol' in msg:
                    logger.error(f'{func_name} 没有交易对')
                    break
                elif '-5013' in msg and 'Asset transfer failed' in msg:
                    logger.error(f'{func_name} 余额不足，无法资金划转')
                    break
                elif '-2027' in msg and 'Exceeded the maximum allowable position at current leverage' in msg:
                    logger.error(f'{func_name} 调整合约杠杆失败')
                    break
                elif '-2028' in msg and 'Leverage is smaller than permitted' in msg:
                    logger.error(f'{func_name} 调整合约杠杆过低')
                    break
                elif '-4028' in msg and 'valid' in msg:
                    logger.error(f'{func_name} 无效的杠杆')
                    break
                else:
                    logger.error(f'{e}，报错内容如下')
                    divider(f'ERR:{func_name}', sep='-')
                    logger.debug(traceback.format_exc())
                    divider(f'ERR:{func_name}', sep='-')
        else:
            raise ValueError(func_name, '报错重试次数超过上限，程序退出。')

    @classmethod
    def get_dummy_client(cls) -> 'BinanceClient':
        return cls()
