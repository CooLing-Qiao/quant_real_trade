"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
from typing import Dict

import numpy as np

from core.utils.factor_hub import FactorHub


def calc_factor_vals(candle_df, factor_name, factor_param_list, shift=0) -> Dict[str, np.ndarray]:
    """
    计算因子值
    :param candle_df:   一个币种的k线数据 dataframe（只读，不会修改的哦）
    :param factor_name: 因子名称
    :param factor_param_list: 因子参数
    :param shift: 因子计算之后便宜，默认是0也就是不偏移，也可以是正数或负数
    :return: 因子值
    """
    factor_series_dict = {}
    # 根据因子内部的函数，来判断是否进行加速操作
    factor = FactorHub.get_by_name(factor_name)  # 获取因子信息

    # 如果存在外部数据，则使用 data_bridge 中的加载函数 load 数据
    if hasattr(factor, 'extra_data_dict') and factor.extra_data_dict:
        from core.utils.datatools import merge_data
        for data_name in factor.extra_data_dict.keys():
            extra_data_dict = merge_data(candle_df, data_name, factor.extra_data_dict[data_name])
            for extra_data_name, extra_data_series in extra_data_dict.items():
                candle_df[extra_data_name] = extra_data_series.values

    if hasattr(factor, 'signal_multi_params'):  # 如果存在 signal_multi_params ，使用最新的因子加速写法
        result_dict = factor.signal_multi_params(candle_df, factor_param_list)
        for param, factor_series in result_dict.items():
            factor_series_dict[f'{factor_name}_{param}'] = factor_series.shift(shift).values

    else:  # 如果存在 signal，使用之前的老因子写法
        legacy_candle_df = candle_df.copy()  # 如果是老的因子计算逻辑，单独拿出来一份数据
        for param in factor_param_list:
            factor_col_name = f'{factor_name}_{param}'
            legacy_candle_df = factor.signal(legacy_candle_df, param, factor_col_name)
            factor_series_dict[factor_col_name] = legacy_candle_df[factor_col_name].shift(shift).values
    return factor_series_dict
