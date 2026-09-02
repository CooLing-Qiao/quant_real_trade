"""
邢不行｜策略分享会
仓位管理实盘框架

检查账户实际杠杆是否与配置一致

Author: 邢不行
"""
import sys
import os
import warnings
import pandas as pd
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec

# 导入项目配置
from core.account_manager import init_system, load_multi_accounts
from core.utils.log_kit import logger, divider
from core.utils.functions import refresh_diff_time
from core.utils.path_kit import get_file_path

# 忽略警告
warnings.filterwarnings('ignore')
pd.set_option('display.max_rows', 1000)
pd.set_option('expand_frame_repr', False)
pd.set_option('display.unicode.ambiguous_as_wide', True)
pd.set_option('display.unicode.east_asian_width', True)


def load_account_config(account_file_path):
    """
    加载账户配置文件
    :param account_file_path: 账户配置文件路径
    :return: 配置模块
    """
    spec = spec_from_file_location("account_config", account_file_path)
    account_module = module_from_spec(spec)
    spec.loader.exec_module(account_module)
    return account_module


def calculate_theoretical_leverage(account_module):
    """
    根据策略配置计算理论上的多空头杠杆分配
    :param account_module: 账户配置模块
    :return: (理论多头杠杆, 理论空头杠杆, 理论总杠杆, 多头占比, 空头占比)
    """
    leverage = account_module.leverage
    strategy_pool = account_module.strategy_pool
    
    # 收集所有策略
    all_strategies = []
    for pool in strategy_pool:
        if 'strategy_list' in pool:
            all_strategies.extend(pool['strategy_list'])
    
    # 计算总权重和多空权重
    total_weight = 0
    long_weight_sum = 0
    short_weight_sum = 0
    
    strategy_details = []
    
    for strategy in all_strategies:
        cap_weight = strategy.get('cap_weight', 0)
        long_cap_weight = strategy.get('long_cap_weight', 0)
        short_cap_weight = strategy.get('short_cap_weight', 0)
        
        # 计算该策略的多空权重
        long_weight = cap_weight * long_cap_weight
        short_weight = cap_weight * short_cap_weight
        
        total_weight += cap_weight
        long_weight_sum += long_weight
        short_weight_sum += short_weight
        
        strategy_details.append({
            '策略': strategy.get('strategy', 'Unknown'),
            '资金权重': cap_weight,
            '多头权重': long_cap_weight,
            '空头权重': short_cap_weight,
            '实际多头': long_weight,
            '实际空头': short_weight,
        })
    
    # 归一化（按总权重）
    if total_weight > 0:
        long_weight_sum = long_weight_sum / total_weight
        short_weight_sum = short_weight_sum / total_weight
    
    # 计算总的多空权重
    total_direction_weight = long_weight_sum + short_weight_sum
    
    # 计算理论杠杆
    if total_direction_weight > 0:
        theoretical_long_leverage = (long_weight_sum / total_direction_weight) * leverage
        theoretical_short_leverage = (short_weight_sum / total_direction_weight) * leverage
    else:
        theoretical_long_leverage = 0
        theoretical_short_leverage = 0
    
    theoretical_total_leverage = leverage
    
    return (
        theoretical_long_leverage,
        theoretical_short_leverage,
        theoretical_total_leverage,
        long_weight_sum,
        short_weight_sum,
        strategy_details
    )


def check_account_leverage(account_name):
    """
    检查单个账户的实际杠杆
    :param account_name: 账户名称（配置文件名，不含.py后缀）
    """
    divider(f'检查账户: {account_name}', '=')
    
    try:
        # 加载账户配置文件
        account_file_path = get_file_path('accounts', f'{account_name}.py', as_path_type=True)
        account_module = load_account_config(account_file_path)
        
        # 计算理论杠杆
        (theoretical_long_lev, theoretical_short_lev, theoretical_total_lev,
         long_weight, short_weight, strategy_details) = calculate_theoretical_leverage(account_module)
        
        logger.info(f'📊 账户名称: {account_name}')
        logger.info(f'⚙️  配置总杠杆: {theoretical_total_lev}x')
        logger.info(f'📈 理论多头杠杆: {theoretical_long_lev:.2f}x (权重占比: {long_weight:.2%})')
        logger.info(f'📉 理论空头杠杆: {theoretical_short_lev:.2f}x (权重占比: {short_weight:.2%})')
        
        # 显示策略详情
        logger.info('\n策略配置详情:')
        strategy_df = pd.DataFrame(strategy_details)
        print(strategy_df.to_string(index=False))
        print()
        
        # 初始化账户系统
        account, _ = init_system(account_name)
        
        # 获取账户全景信息（包含现货+合约+盈亏）
        account_overview = account.bn.get_account_overview()
        if account_overview is None:
            logger.error('❌ 获取账户信息失败')
            return False
        
        # 获取账户净值（包含现货、合约、未实现盈亏）
        account_equity = account_overview['account_equity']
        swap_equity = account_overview['swap_assets']['equity']
        spot_equity = account_overview['spot_assets']['equity']
        
        logger.info(f'💰 账户总净值: {account_equity:.2f} USDT')
        logger.info(f'   ├─ 合约净值: {swap_equity:.2f} USDT (含盈亏)')
        logger.info(f'   └─ 现货净值: {spot_equity:.2f} USDT')
        
        # 获取持仓信息
        position_df = account.bn.get_swap_position_df()
        
        if position_df.empty:
            logger.warning('⚠️  账户没有持仓')
            logger.info(f'实际杠杆: 0x (理论应为: {theoretical_total_lev}x)')
            return True
        
        # 计算实际杠杆
        position_df['仓位价值_abs'] = position_df['仓位价值'].abs()
        
        # 分离多空头持仓
        long_positions = position_df[position_df['当前持仓量'] > 0]
        short_positions = position_df[position_df['当前持仓量'] < 0]
        
        long_value = long_positions['仓位价值_abs'].sum() if not long_positions.empty else 0
        short_value = short_positions['仓位价值_abs'].sum() if not short_positions.empty else 0
        total_position_value = long_value + short_value
        
        # 计算实际杠杆
        actual_long_leverage = long_value / account_equity if account_equity > 0 else 0
        actual_short_leverage = short_value / account_equity if account_equity > 0 else 0
        actual_total_leverage = total_position_value / account_equity if account_equity > 0 else 0
        
        logger.info(f'\n📊 持仓统计:')
        logger.info(f'  多头持仓数: {len(long_positions)}')
        logger.info(f'  空头持仓数: {len(short_positions)}')
        logger.info(f'  多头仓位价值: {long_value:.2f} USDT')
        logger.info(f'  空头仓位价值: {short_value:.2f} USDT')
        logger.info(f'  总仓位价值: {total_position_value:.2f} USDT')
        
        # 显示持仓明细
        logger.info(f'\n📋 持仓明细:')
        display_df = position_df[['当前持仓量', '当前标记价格', '仓位价值', '持仓盈亏']].copy()
        display_df['方向'] = display_df['当前持仓量'].apply(lambda x: '多头' if x > 0 else '空头')
        print(display_df.to_string())
        print()
        
        # 计算差异
        long_lev_diff = abs(actual_long_leverage - theoretical_long_lev) / theoretical_long_lev * 100 if theoretical_long_lev > 0 else 0
        short_lev_diff = abs(actual_short_leverage - theoretical_short_lev) / theoretical_short_lev * 100 if theoretical_short_lev > 0 else 0
        total_lev_diff = abs(actual_total_leverage - theoretical_total_lev) / theoretical_total_lev * 100
        
        # 判断是否通过（5%误差）
        long_pass = long_lev_diff <= 5.0 or theoretical_long_lev == 0
        short_pass = short_lev_diff <= 5.0 or theoretical_short_lev == 0
        total_pass = total_lev_diff <= 5.0
        
        # 显示结果
        logger.info(f'\n🔍 杠杆检查结果:')
        logger.info(f'  {"项目":<12} {"理论值":<12} {"实际值":<12} {"差异":<12} {"状态":<8}')
        logger.info(f'  {"-"*60}')
        
        long_status = '✅ 通过' if long_pass else '❌ 超差'
        short_status = '✅ 通过' if short_pass else '❌ 超差'
        total_status = '✅ 通过' if total_pass else '❌ 超差'
        
        logger.info(f'  {"多头杠杆":<10} {theoretical_long_lev:>10.2f}x {actual_long_leverage:>10.2f}x {long_lev_diff:>10.2f}% {long_status}')
        logger.info(f'  {"空头杠杆":<10} {theoretical_short_lev:>10.2f}x {actual_short_leverage:>10.2f}x {short_lev_diff:>10.2f}% {short_status}')
        logger.info(f'  {"总杠杆":<10} {theoretical_total_lev:>10.2f}x {actual_total_leverage:>10.2f}x {total_lev_diff:>10.2f}% {total_status}')
        
        # 分析杠杆不足的原因
        if not total_pass and actual_total_leverage < theoretical_total_lev:
            logger.warning(f'\n⚠️  杠杆不足分析:')
            
            # 计算理论应该有的仓位价值
            theoretical_position_value = account_equity * theoretical_total_lev
            missing_position_value = theoretical_position_value - total_position_value
            missing_percentage = missing_position_value / theoretical_position_value * 100
            
            logger.warning(f'  理论总仓位价值: {theoretical_position_value:.2f} USDT')
            logger.warning(f'  实际总仓位价值: {total_position_value:.2f} USDT')
            logger.warning(f'  缺失仓位价值: {missing_position_value:.2f} USDT ({missing_percentage:.1f}%)')
            
            # 分析可能的原因
            logger.info(f'\n💡 可能的原因:')
            logger.info(f'  1. 持仓数量过多 (当前: {len(position_df)} 个)')
            logger.info(f'     → 平均每个币种: {total_position_value / len(position_df):.2f} USDT')
            logger.info(f'     → 很多订单可能低于交易所最小下单金额(5 USDT)而被跳过')
            
            # 统计小仓位
            small_positions = position_df[position_df['仓位价值_abs'] < 50]
            if not small_positions.empty:
                small_position_value = small_positions['仓位价值_abs'].sum()
                logger.info(f'  2. 小仓位统计 (仓位价值 < 50 USDT):')
                logger.info(f'     → 数量: {len(small_positions)} 个')
                logger.info(f'     → 总价值: {small_position_value:.2f} USDT')
                logger.info(f'     → 占比: {small_position_value / total_position_value * 100:.1f}%')
            
            logger.info(f'\n🔧 建议解决方案:')
            logger.info(f'  1. 减少选币数量 (修改策略配置中的 long_select_coin_num 和 short_select_coin_num)')
            logger.info(f'  2. 增加账户资金')
            logger.info(f'  3. 调整资金权重分配，集中资金到更少的币种')
        
        all_pass = long_pass and short_pass and total_pass
        
        if all_pass:
            logger.ok(f'\n✅ 账户杠杆检查通过！')
        else:
            logger.error(f'\n❌ 账户杠杆存在偏差，请检查！')
        
        return all_pass
        
    except Exception as e:
        logger.error(f'❌ 检查账户 {account_name} 时出错: {str(e)}')
        import traceback
        logger.debug(traceback.format_exc())
        return False


def main():
    """
    主函数：检查所有账户的杠杆设置
    """
    divider('开始检查账户杠杆设置', '=')
    
    # 刷新与交易所的时差
    refresh_diff_time()
    
    # 加载所有账户配置文件
    account_profile_files = load_multi_accounts()
    
    if not account_profile_files:
        logger.critical('❌ 没有检测到可用的账户配置文件')
        sys.exit(1)
    
    logger.info(f'📋 检测到 {len(account_profile_files)} 个账户配置文件\n')
    
    # 检查每个账户
    all_results = {}
    for account_file in account_profile_files:
        account_name = account_file.stem  # 获取文件名（不含扩展名）
        result = check_account_leverage(account_name)
        all_results[account_name] = result
        print()
    
    # 汇总结果
    divider('检查结果汇总', '=')
    for account_name, result in all_results.items():
        status = '✅ 通过' if result else '❌ 失败'
        logger.info(f'{account_name}: {status}')
    
    # 总体结果
    print()
    if all(all_results.values()):
        logger.ok('🎉 所有账户杠杆检查全部通过！')
    else:
        logger.error('⚠️  部分账户存在杠杆设置问题，请及时处理！')
    
    divider('检查完成', '=')


if __name__ == '__main__':
    main()
