"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import gc
import os.path
import platform
import random
import re
import shutil
import subprocess
import sys
import time
import traceback
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd

from config import is_debug, error_webhook_url, debug_flag, clean_start, snapshot_path, is_simulate
from core.account_manager import init_system, load_multi_accounts
from core.utils.commons import sleep_until_run_time, remedy_until_run_time, get_debug_run_time
from core.utils.datatools import check_bmac_update_flag
from core.utils.dingding import send_wechat_work_msg
from core.utils.functions import refresh_diff_time
from core.utils.log_kit import logger, divider
from core.utils.path_kit import get_file_path, PROJECT_ROOT
from core.version import version_prompt

# ====================================================================================================
# ** 脚本运行前配置 **
# 主要是解决各种各样奇怪的问题们
# ====================================================================================================
# region 脚本运行前准备
warnings.filterwarnings('ignore')  # 过滤一下warnings，不要吓到老实人

# pandas相关的显示设置，基础课程都有介绍
pd.set_option('display.max_rows', 1000)
pd.set_option('expand_frame_repr', False)  # 当列太多时不换行
pd.set_option('display.unicode.ambiguous_as_wide', True)  # 设置命令行输出时的列对齐功能
pd.set_option('display.unicode.east_asian_width', True)

# 解决pm2在win系统下的乱码问题
os_type = platform.system()
if os_type == 'Windows':  # win执行
    # noinspection PyUnresolvedReferences
    sys.stdout.reconfigure(encoding='utf-8')
    # 若上面sys.stdout = ... 这一行不行，可以试试下一行
    # import io
    # sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8')
# endregion


# ====================================================================================================
# ** 本脚本的全局变量 **
# ====================================================================================================
# 构建线程池，给统计脚本异步执行
stat_executor = ThreadPoolExecutor()  # 统计脚本真的会跑很久很久
python_exec = sys.executable  # 获取当前环境下的python解释器，也是给统计脚本用的


# ====================================================================================================
# ** 重要的函数区域 **
# 负责我们保温杯系统平顺执行的核心组件
# ====================================================================================================
def run_statistics_async(run_time, random_time, account_name):
    # debug 模式不统计
    if is_debug and is_simulate == 'debug':
        return
    logger.info(f'sleep 一下保证统计脚本在 offset 下单之后开始计算数据···')
    time.sleep(random_time if random_time else 120)  # 提前随机多久，就休息多久，默认2分钟
    subprocess.run(
        [sys.executable, get_file_path("core", "utils", f"statistics{'_debug' if is_debug else ''}.py"), f'{int(run_time.timestamp())}', account_name],
        check=True
    )


def read_hour_offset_from_config(config_file_path):
    pattern = r'[\'"]hour_offset[\'"]\s*:\s*[\'"]([^\'"]+)[\'"]'
    match = re.search(pattern, config_file_path.read_text(errors='ignore'))
    return match.group(1) if match else '0m'


def run_loop() -> datetime | None:
    """
    无限循环这个函数♾️
    :return: 成功执行的话，返回本次运行的时间，否则是None
    """
    divider('本次循环开始', '=')

    # ====================================================================================================
    # 0. 调试相关配置区域
    # ====================================================================================================
    random_time = random.randint(1 * 60, 2 * 60)  # 不建议使用负数。小时级别提前时间可以设置短一点

    # 读取 accounts 文件夹下的所有账户配置文件
    account_profile_files = load_multi_accounts()

    # 如果没有检测到可用的账户配置文件，就退出
    if not account_profile_files:
        logger.critical('没有检测到可用的账户配置文件，程序退出...')
        exit(2)

    if is_debug and is_simulate == 'debug':  # 调试模式，不进行sleep，直接继续往后运行
        logger.debug(f'🐞 调试模式 - 自动读取第一个文件"{account_profile_files[0].name}"的`hour_offset`')
        first_hour_offset = read_hour_offset_from_config(account_profile_files[0])
        run_time = get_debug_run_time(first_hour_offset)
    else:
        run_time = sleep_until_run_time('5m', if_sleep=True, cheat_seconds=random_time)  # 每五分钟运行一次

    # ====================================================================================================
    # 2. 等待数据中心完成数据更新
    # ====================================================================================================
    logger.info(f'检查数据中心，{run_time}...')
    # 检查数据中心数据是否更新
    is_data_ready = check_bmac_update_flag(run_time)
    # 判断数据是否更新好了
    if not is_data_ready:  # 没有更新好，跳过当前下单
        logger.warning(f'数据中心数据未更新，跳过当前，{run_time}，[调试模式{debug_flag}]')
        logger.debug('ℹ️ 也可以确认一下数据中心的`enabled_hour_offsets`，确认你需要的offsets都被启用了。')
        logger.debug('如果你没有配置过数据中心的这个配置，那请无视上面的这条提示。')
        return None
    logger.ok(f'数据中心数据更新完成，准备选币下单，[调试模式{debug_flag}]')

    # ====================================================================================================
    # 3. 针对每一个配置好的可用账户，进行因子计算、选币、下单
    # ====================================================================================================
    """!!! 核心逻辑在这里 !!!"""
    # 从命令行参数获取账户配置文件名(可选)
    # usage: python run_with_account.py _55mBTC样例.py
    for account_profile_file in account_profile_files:
        # 读取配置信息中的hour offset
        hour_offset_value = read_hour_offset_from_config(account_profile_file)

        logger.debug(f"👤 Using account: {account_profile_file}")
        logger.debug(f"⏲️ hour_offset 的值为: {hour_offset_value}", )
        if run_time.minute in [int(hour_offset_value[:-1])]:
            os.environ["X3S_TRADING_ACCOUNT"] = account_profile_file.name
            os.environ['PYTHONPATH'] = PROJECT_ROOT
            subprocess.run(
                [sys.executable, get_file_path('core', 'account_exec.py'), f'{int(run_time.timestamp())}'],
                check=True
            )
            stat_executor.submit(run_statistics_async, run_time, random_time, account_profile_file.stem)  # 异步执行
        else:
            logger.warning(f'当前运行时间为{run_time}，不是 `{hour_offset_value}` 的offset，跳过\n')
        gc.collect()  # 强制垃圾回收

    # 本次循环结束
    divider('本次循环结束，23秒后进入下一次循环', '=')
    print('\n\n')
    time.sleep(23)
    # 每次运行完发送

    remedy_until_run_time(run_time)  # 休息休息～～～
    return run_time


def check_accounts():
    refresh_diff_time()  # 刷新与交易所的时差
    for account_profile_file in load_multi_accounts():
        account, _ = init_system(account_profile_file.stem)

        logger.debug(f'🔵 {account.name} 检查杠杆、持仓模式、保证金模式...')
        account.bn.reset_max_leverage(max_leverage=account.page_leverage)  # 检查杠杆
        account.bn.set_single_side_position()  # 检查并且设置持仓模式：单向持仓
        account.bn.set_multi_assets_margin()  # 检查联合保证金模式
        time.sleep(2)  # 多账户之间，停顿一下
        logger.ok('自检完成')


if __name__ == '__main__':
    version_prompt()
    if is_debug:
        logger.debug('🟠' * 16 + ' [调试模式] ' + '🟠' * 16)
    else:
        logger.debug('🟢' * 16 + ' [正式模式] ' + '🟢' * 16)
    logger.info(f'系统初始化，稍等...')
    time.sleep(1)

    # ====================================================================================================
    # 运行前自检和准备
    # ====================================================================================================
    # 如果不是debug模式准备账户设置
    if clean_start:
        shutil.rmtree(snapshot_path, ignore_errors=True)
    else:
        logger.critical('!!!!! 注意，你选择了允许缓存的条件下重启，可能会有灾难性后果 !!!!!')
        logger.warning('如果你对此不确定，请立即退出，并删除 `data -> snapshot` 文件夹，再重启。')
        logger.debug('给你7s时间确认...')
        time.sleep(7)

    # 如果不是debug模式准备账户设置
    if not is_debug:
        snapshot_path.mkdir(parents=True, exist_ok=True)
        check_accounts()
        time.sleep(1)
    else:
        logger.debug('🐞 调试模式 - 跳过账户检查和准备')

    # ====================================================================================================
    # 实盘主程序，电不停我不停
    # ====================================================================================================
    while True:
        try:
            run_loop()
        except Exception as err:
            msg = '系统出错，10s之后重新运行，出错原因: ' + str(err)
            logger.error(msg)
            logger.debug(traceback.format_exc())
            send_wechat_work_msg(msg, error_webhook_url)
            time.sleep(12)  # 休息十一秒钟，再冲
        finally:
            if is_debug and is_simulate == 'debug':
                break
