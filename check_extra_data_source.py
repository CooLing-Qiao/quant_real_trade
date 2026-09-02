"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
if __name__ == '__main__':
    """
    使用之前，可以先检查配置，检查一下当前配置的 函数 与 文件路径是否存在
    """
    from core.utils.datatools import check_cfg, check_factor

    # 检查 data_source_dict 配置
    check_cfg()
    # 检查指定因子配置
    check_factor(['CirculatingMcap'])
