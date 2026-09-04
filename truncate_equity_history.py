#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
清理账户历史净值/划转记录中某个日期之前的数据，让净值法（份额法）从新的日期重新起算。

背景：net_fund（core/utils/statistics.py）是份额法净值——早期一旦发生过大幅回撤，之后又在净值还没恢复
时陆续入金，会把份额永久摊薄：入金按"当时的净值"换算份额，净值越低同样金额换到的份额越多，往后哪怕账户
美元净值完全恢复，"每份"净值也很难再抬起来。2026-09-04 在 0m超混 账户上实测遇到过：2026-02 一次真实暴跌
后陆续做了多笔低位入金，份额从1.3万摊薄到4亿，导致"净值法历史最高净值/回撤"这类全历史指标显示回撤
-99.99%，但账户实际美元净值只跌了约29%（详见当时对话记录）。

用户判断策略已经调整过好几次，cutoff 日期之前的历史不再具有参考价值，所以直接把 cutoff 之前的记录删掉，
净值法从 cutoff 那一天重新起算（第一行重新记为净值=1、份额=1）。

用法：python truncate_equity_history.py <账户名> <cutoff日期，如 2026-08-01>

效果：
    - data/<账户>/账户信息/equity.csv：只保留 time >= cutoff 的行
    - data/<账户>/账户信息/transfer.csv：只保留 time >= cutoff 的行（必须跟着截断，否则 net_fund 还是会把
      cutoff 之前的入金记录当成"新起点"发生的份额变化，摊薄效果照样带进来）
    - 截断前把两个文件各备份一份（原文件名加时间戳后缀），不会真的丢历史数据
    - 不用管 equity.pkl——它是每轮 account_exec 从 equity.csv+transfer.csv 全量重算生成的派生文件，
      下一轮调仓会自动用截断后的新历史重新生成
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.append('.')
from core.utils.path_kit import get_folder_path
from core.utils.log_kit import logger, divider


def truncate_csv(path: Path, cutoff: pd.Timestamp):
    if not path.exists():
        logger.warning(f'{path} 不存在，跳过')
        return
    df = pd.read_csv(path, encoding='gbk')
    df['time'] = pd.to_datetime(df['time'], format='mixed')
    before = len(df)

    backup_path = path.with_name(f'{path.stem}_backup_{datetime.now():%Y%m%d_%H%M%S}{path.suffix}')
    shutil.copy2(path, backup_path)

    df = df[df['time'] >= cutoff].reset_index(drop=True)
    df.to_csv(path, encoding='gbk', index=False)
    logger.ok(f'{path.name}：{before} 行 -> {len(df)} 行（已备份到 {backup_path.name}）')


def main(account_name: str, cutoff_str: str):
    cutoff = pd.Timestamp(cutoff_str)
    data_path = get_folder_path('data', as_path_type=True)
    base = data_path / account_name / '账户信息'

    divider(f'🧹 截断 [{account_name}] 净值历史，只保留 {cutoff} 之后的数据', '+')
    truncate_csv(base / 'equity.csv', cutoff)
    truncate_csv(base / 'transfer.csv', cutoff)
    logger.ok('截断完成，equity.pkl 会在下一轮调仓自动用新历史重算，不需要手动处理')


if __name__ == '__main__':
    if len(sys.argv) < 3:
        logger.error('用法：python truncate_equity_history.py <账户名> <cutoff日期，如 2026-08-01>')
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
