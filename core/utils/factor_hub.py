"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import importlib

import pandas as pd


class DummyFactor:
    """
    ！！！！抽象因子对象，仅用于代码提示！！！！
    """
    # 额外数据
    extra_data_dict: dict = {}

    # 因子模式：时序因子/截面因子（factors/sections）
    # False： 时序因子
    # True：  截面因子
    is_cross: bool = False

    def signal(self, *args) -> pd.DataFrame:
        raise NotImplementedError

    def signal_multi_params(self, df, param_list: list | set | tuple) -> dict:
        raise NotImplementedError

    def get_factor_list(self, n):
        raise NotImplementedError


class FactorHub:
    _factor_cache = {}

    # noinspection PyTypeChecker
    @staticmethod
    def get_by_name(factor_name) -> DummyFactor:
        import locale
        # 防止部分操作系统，无法识别中文名的策略或者因子
        locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')

        if factor_name in FactorHub._factor_cache:
            return FactorHub._factor_cache[factor_name]

        try:
            try:
                module = 'factors'
                factor_module = importlib.import_module(f"{module}.{factor_name}")
            except ModuleNotFoundError:
                module = 'sections'
                factor_module = importlib.import_module(f"{module}.{factor_name}")

            # 创建一个包含模块变量和函数的字典
            factor_content = {
                name: getattr(factor_module, name) for name in dir(factor_module)
                if not name.startswith("__")
            }
            factor_content['is_cross'] = module == 'sections'

            # 创建一个包含这些变量和函数的对象
            factor_instance = type(factor_name, (), factor_content)

            # 缓存策略对象
            FactorHub._factor_cache[factor_name] = factor_instance

            return factor_instance
        except ModuleNotFoundError:
            raise ValueError(f"Factor {factor_name} not found.")
        except AttributeError:
            raise ValueError(f"Error accessing factor content in module {factor_name}.")


# 使用示例
if __name__ == "__main__":
    factor = FactorHub.get_by_name("PctChange")
