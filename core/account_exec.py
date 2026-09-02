"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import sys
import traceback
import warnings
from datetime import datetime

import pandas as pd

from config import is_debug, error_webhook_url
from core.account_manager import init_system
from core.real_trading import run_by_account
from core.utils.dingding import send_wechat_work_msg
from core.utils.log_kit import logger, divider

# ====================================================================================================
# ** 脚本运行前配置 **
# 主要是解决各种各样奇怪的问题们
# ====================================================================================================
warnings.filterwarnings('ignore')  # 过滤一下warnings，不要吓到老实人

# pandas相关的显示设置，基础课程都有介绍
pd.set_option('display.max_rows', 1000)
pd.set_option('expand_frame_repr', False)  # 当列太多时不换行
pd.set_option('display.unicode.ambiguous_as_wide', True)  # 设置命令行输出时的列对齐功能
pd.set_option('display.unicode.east_asian_width', True)


def main() -> datetime | None:
    # ====================================================================================================
    # 1. 启动时间
    # ====================================================================================================
    logger.info('准备中...')
    if len(sys.argv) > 1:
        timestamp = sys.argv[1]
        run_time = datetime.fromtimestamp(int(timestamp))
    else:
        logger.error(f'❌未传入时间参数，不执行后续流程')
        return None
    logger.info(f'启动时间：{run_time}')

    # ====================================================================================================
    # 2. 更新、检查账户信息
    # ====================================================================================================
    account_config, me_conf = init_system()
    if is_debug:
        logger.debug('🐞调试模式 - 不更新账户信息')
        is_ready = True
    else:
        account_config.bn.reset_max_leverage(max_leverage=account_config.page_leverage)  # 重置页面杠杆（新币可能会没有被设置过）
        is_ready = account_config.update_account_info(is_operate=True)
    if is_ready is None or not is_ready:
        logger.info(f'{account_config.name} 更新没有准备好，跳过当前账户')
        return None
    divider(f'🚀 [{account_config.name}] 开始', '+')

    # ====================================================================================================
    # 3. 执行
    # !!! 核心逻辑在这里 !!!
    # ====================================================================================================
    run_by_account(account_config, me_conf, run_time)

    divider(f'🏁 [{account_config.name}] 完成', '+')

    return run_time


if __name__ == '__main__':
    try:
        main()
    except Exception as err:
        msg = '系统出错，10s之后重新运行，出错原因: ' + str(err)
        logger.error(msg)
        logger.debug(traceback.format_exc())
        send_wechat_work_msg(msg, error_webhook_url)
