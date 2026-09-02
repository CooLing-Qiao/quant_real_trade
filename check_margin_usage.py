#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查币安合约账户保证金使用情况
显示：总权益、已用保证金、可用保证金、保证金占比等

使用方法：
    python check_margin_usage.py                    # 检查第一个账户
    python check_margin_usage.py 0m超混             # 检查指定账户
"""

import sys
import os
import pandas as pd
from pathlib import Path

# 添加项目路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.account_manager import load_multi_accounts, init_system
from core.utils.log_kit import logger


def format_usdt(value):
    """格式化USDT金额"""
    return f"{float(value):,.2f}"


def check_margin_usage(account_name=None):
    """检查保证金使用情况"""
    
    # 加载账户列表
    accounts = load_multi_accounts()
    
    if not accounts:
        logger.error("未找到任何账户配置文件")
        return
    
    # 选择账户
    if account_name:
        # 查找指定账户
        target_file = None
        for acc_file in accounts:
            if account_name in str(acc_file):
                target_file = acc_file
                break
        if not target_file:
            logger.error(f"未找到账户: {account_name}")
            logger.info(f"可用账户: {[f.stem for f in accounts]}")
            return
    else:
        # 使用第一个账户
        target_file = accounts[0]
    
    account_display_name = target_file.stem
    logger.info(f"正在检查账户: {account_display_name}")
    
    # 初始化账户系统
    try:
        account, _ = init_system(account_display_name)
        bn = account.bn
    except Exception as e:
        logger.error(f"初始化账户失败: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print("\n" + "="*80)
    print(f"🔍 币安合约账户保证金分析 - [{account_display_name}]".center(90))
    print("="*80 + "\n")
    
    # 获取账户信息（强制更新）
    try:
        account_info = bn.get_swap_account(require_update=True)
    except Exception as e:
        logger.error(f"获取账户信息失败: {e}")
        return
    
    if not account_info:
        logger.error("获取账户信息失败：返回为空")
        return
    
    # 提取关键数据
    total_wallet_balance = float(account_info['totalWalletBalance'])  # 账户总权益
    total_margin_balance = float(account_info['totalMarginBalance'])  # 保证金余额（包含未实现盈亏）
    total_unrealized_profit = float(account_info['totalUnrealizedProfit'])  # 未实现盈亏
    total_maint_margin = float(account_info['totalMaintMargin'])  # 维持保证金
    available_balance = float(account_info['availableBalance'])  # 可用余额
    max_withdraw = float(account_info['maxWithdrawAmount'])  # 最大可提现金额
    
    # 计算已用保证金（近似）
    # 已用保证金 ≈ 保证金余额 - 可用余额
    used_margin = total_margin_balance - available_balance
    
    # 计算保证金使用率
    margin_usage_rate = (used_margin / total_margin_balance * 100) if total_margin_balance > 0 else 0
    
    # 计算维持保证金率
    maint_margin_rate = (total_maint_margin / total_margin_balance * 100) if total_margin_balance > 0 else 0
    
    # 显示总体信息
    print("📊 账户总览")
    print("-" * 80)
    print(f"总权益（不含浮盈）:      {format_usdt(total_wallet_balance)} USDT")
    print(f"保证金余额（含浮盈）:    {format_usdt(total_margin_balance)} USDT")
    print(f"未实现盈亏:             {format_usdt(total_unrealized_profit)} USDT")
    print(f"维持保证金:             {format_usdt(total_maint_margin)} USDT")
    print()
    print(f"{'='*80}")
    print(f"💰 可用余额:             {format_usdt(available_balance)} USDT  ✅")
    print(f"🔒 已用保证金:           {format_usdt(used_margin)} USDT")
    print(f"📈 保证金使用率:         {margin_usage_rate:.2f}%  {'🔴 警告！' if margin_usage_rate > 80 else '🟢 正常' if margin_usage_rate < 50 else '🟡 偏高'}")
    print(f"⚠️  维持保证金率:        {maint_margin_rate:.2f}%")
    print(f"{'='*80}")
    print()
    
    # 获取持仓信息 - 使用更可靠的持仓接口
    try:
        position_df = bn.get_swap_position_df()
    except Exception as e:
        logger.warning(f"获取持仓详情失败: {e}")
        position_df = pd.DataFrame()
    
    active_positions = []
    
    if not position_df.empty:
        for symbol, row in position_df.iterrows():
            position_amt = float(row['当前持仓量'])
            entry_price = float(row['均价'])
            mark_price = float(row['当前标记价格'])
            unrealized_profit = float(row['持仓盈亏'])
            position_value = abs(row['仓位价值'])
            
            # 获取该币种的杠杆（从账户信息中查找）
            leverage = 7  # 默认杠杆，如果找不到就用这个
            for pos in account_info.get('positions', []):
                if pos.get('symbol') == symbol:
                    leverage = float(pos.get('leverage', 7))
                    break
            
            # 计算占用保证金（持仓价值 / 杠杆）
            margin_used = position_value / leverage if leverage > 0 else position_value
            
            active_positions.append({
                'symbol': symbol,
                'position_amt': position_amt,
                'entry_price': entry_price,
                'mark_price': mark_price,
                'position_value': position_value,
                'leverage': leverage,
                'margin_used': margin_used,
                'unrealized_profit': unrealized_profit,
                'margin_pct': (margin_used / used_margin * 100) if used_margin > 0 else 0
            })
    
    # 按占用保证金排序
    active_positions.sort(key=lambda x: x['margin_used'], reverse=True)
    
    # 显示持仓详情
    if active_positions:
        print(f"📋 持仓详情（共 {len(active_positions)} 个币种）")
        print("-" * 80)
        
        # 创建DataFrame便于展示
        df = pd.DataFrame(active_positions)
        
        # 格式化显示
        print(f"{'排名':<4} {'币种':<15} {'数量':<12} {'仓位价值':<15} {'杠杆':<6} {'占用保证金':<15} {'占比':<8} {'浮盈':<12}")
        print("-" * 80)
        
        for idx, pos in enumerate(active_positions[:30], 1):  # 只显示前30个
            direction = "做多" if pos['position_amt'] > 0 else "做空"
            print(f"{idx:<4} {pos['symbol']:<15} "
                  f"{pos['position_amt']:>11.2f} "
                  f"{format_usdt(pos['position_value']):<15} "
                  f"{pos['leverage']:>5.0f}x "
                  f"{format_usdt(pos['margin_used']):<15} "
                  f"{pos['margin_pct']:>6.2f}% "
                  f"{format_usdt(pos['unrealized_profit']):<12}")
        
        if len(active_positions) > 30:
            print(f"\n... 还有 {len(active_positions) - 30} 个币种未显示 ...")
        
        print("-" * 80)
        
        # 统计前10占用保证金比例
        top10_margin = sum([p['margin_used'] for p in active_positions[:10]])
        top10_pct = (top10_margin / used_margin * 100) if used_margin > 0 else 0
        
        print(f"\n📌 Top 10 币种占用保证金: {format_usdt(top10_margin)} USDT ({top10_pct:.2f}%)")
        print(f"📌 其他币种占用保证金:   {format_usdt(used_margin - top10_margin)} USDT ({100-top10_pct:.2f}%)")
    else:
        print("当前无持仓")
    
    print("\n" + "="*80)
    print("💡 建议")
    print("-" * 80)
    
    if margin_usage_rate > 90:
        print("🔴 保证金使用率超过90%！强烈建议：")
        print("   1. 立即降低杠杆（推荐降到3-4倍）")
        print("   2. 减少持仓币种数量")
        print("   3. 平掉部分低收益仓位")
    elif margin_usage_rate > 80:
        print("🟡 保证金使用率偏高！建议：")
        print("   1. 降低杠杆（推荐降到4-5倍）")
        print("   2. 适当减少持仓")
    elif margin_usage_rate > 60:
        print("🟡 保证金使用率中等，建议适当控制持仓规模")
    else:
        print("🟢 保证金使用率健康，可以正常交易")
    
    print()
    print(f"当前可用余额: {format_usdt(available_balance)} USDT")
    print(f"可以新开仓的金额约: {format_usdt(available_balance * 0.8)} USDT（建议预留20%缓冲）")
    print("="*80 + "\n")


if __name__ == '__main__':
    try:
        # 支持命令行参数指定账户
        account_name = sys.argv[1] if len(sys.argv) > 1 else None
        check_margin_usage(account_name)
    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        logger.error(f"执行出错: {e}", exc_info=True)
        import traceback
        traceback.print_exc()
