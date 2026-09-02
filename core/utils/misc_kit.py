"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import os
import sys
import traceback

from concurrent.futures import ProcessPoolExecutor


def is_debug_mode():
    get_trace = getattr(sys, 'gettrace', None)
    try:
        # 仅在pycharm中存在
        import pydevd_pycharm
        is_debug = pydevd_pycharm.__builtins__['__debug__']
    except BaseException:
        is_debug = False
    if get_trace is None:
        return False or is_debug
    else:
        return get_trace() is not None or is_debug


def mem_save_exe(func, *args, **kwargs):
    if is_debug_mode() and (os.name == 'nt'):
        # Windows下并行会有问题，
        return func(*args, **kwargs)

    with ProcessPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            result = future.result(timeout=60 * 60)  # 任务超时60分钟
            return result
        except TimeoutError:
            print('任务超时，程序退出')
            os._exit(1)
        except BaseException as e:
            print(traceback.format_exc())
            raise e
