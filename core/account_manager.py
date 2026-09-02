"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import importlib
import os

import config
from core.mef_manager import init_mef_system
from core.model.account_config import AccountConfig
from core.model.backtest_config import MultiEquityBacktestConfig
from core.utils.log_kit import logger
from core.utils.path_kit import get_folder_path

account_config_dict = {}


# ====================================================================================================
# ** 系统config初始化相关 **
# ===================================================================================================
def init_system(env_account: str = None) -> tuple[AccountConfig, MultiEquityBacktestConfig]:
    if env_account:
        os.environ["X3S_TRADING_ACCOUNT"] = env_account
        importlib.reload(config)

    mef = init_mef_system(env_account)

    base_config = dict(
        get_kline_num=config.data_config['get_kline_num'],
        # 以下配置和回测过程同步
        rebalance_mode=getattr(config, 'rebalance_mode', None),  # rebalance类型
        min_kline_num=config.data_config['min_kline_num'],
        leverage=config.leverage,
        black_list=[item.replace('-', '') for item in config.black_list],  # 拉黑名单
        white_list=[item.replace('-', '') for item in config.white_list],  # 只交易名单
        is_pure_long=mef.is_pure_long,  # 纯多模式设置
    )
    config_dict = {**base_config, **config.account_config}

    cfg: AccountConfig = AccountConfig(config.account_name, **config_dict)
    cfg.use_spot = mef.use_spot

    # 初始化交易所的配置
    _apikey, _secret = cfg.api_key, cfg.secret
    if _apikey and _secret:
        # 更新交易所基础配置
        config.exchange_basic_config['apiKey'], config.exchange_basic_config['secret'] = _apikey, _secret

        # 初始化交易所相关接口
        cfg.init_exchange(config.exchange_basic_config)

    # 把成功初始化的对象放入到返回结果
    return cfg, mef


def get_usable_account_config_dict() -> tuple[AccountConfig, MultiEquityBacktestConfig]:
    from core.utils.functions import update_accounts_info

    acct_conf, mef = init_system()

    if config.is_debug:
        logger.debug('🐞 调试模式 - 不更新账户信息')
        return acct_conf, mef
    else:
        config_dict = update_accounts_info({config.account_name: acct_conf})

        # 判断是否没能成功读取任一账户
        if not len(config_dict.keys()):  # 如果update_account_info数据为空，表示更新账户信息失败
            logger.warning(f'所有账户更新信息失败')

        return config_dict[config.account_name]


def load_multi_accounts():
    # 读取 accounts 文件夹下的所有账户配置文件
    return [
        f for f in sorted(get_folder_path('accounts', as_path_type=True).rglob('*.py')) if
        f.is_file() and not f.stem.startswith('_')]
