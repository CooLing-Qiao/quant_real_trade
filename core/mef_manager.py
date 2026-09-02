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
from core.model.backtest_config import MultiEquityBacktestConfig


# ====================================================================================================
# ** 系统config初始化相关 **
# ===================================================================================================
def init_mef_system(env_account: str = None) -> MultiEquityBacktestConfig:
    if env_account:
        os.environ["X3S_TRADING_ACCOUNT"] = env_account
        importlib.reload(config)
        import core.model.backtest_config as backtest_config
        importlib.reload(backtest_config)

    # 把成功初始化的对象放入到返回结果
    return MultiEquityBacktestConfig(
        name=config.strategy_name,
        strategy_config=config.strategy_config,
        strategies=config.strategy_pool
    )
