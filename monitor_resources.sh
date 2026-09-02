#!/usr/bin/env bash
# ============================================================
# 服务器资源监控脚本（评估降配用）
#
# 采集整机 CPU / 内存 / Swap / 负载，以及交易相关进程
# （startup.py 主循环、account_exec.py 调仓子进程、dingzhen.py）
# 各自的 CPU% 和常驻内存(RSS)，写成 CSV，方便事后统计峰值/均值。
#
# 用法：
#   nohup bash monitor_resources.sh > /dev/null 2>&1 &
#   建议至少跑满 24 小时，覆盖所有整点调仓时刻和平时的空闲时段。
#
# 停止：
#   pkill -f monitor_resources.sh
#
# 输出：~/resource_monitor.csv
#
# 注意：
#   - 采样间隔默认 5 秒。整点调仓一般几十秒到几分钟，间隔太大会漏掉峰值。
#   - account_exec.py 因子计算阶段会用 job_num 个并行子进程（默认 CPU核数-1），
#     Linux 下 multiprocessing 默认用 fork，子进程会保留和父进程一样的命令行，
#     所以每个子进程都会各自被记一行 proc 记录——分析时如果想看"调仓那一刻
#     总共吃了多少 CPU"，需要按 timestamp 把所有 account_exec 的 proc_cpu_pct 加起来
#     （单行的 %CPU 是相对单核的，比如占满2个核会显示200）。
#   - 一次运行几天问题不大（每行几十字节），但不建议无限期挂着不管，
#     跑完评估就 pkill 掉。
# ============================================================
set -u

INTERVAL=5
OUT="$HOME/resource_monitor.csv"
PROC_PATTERNS='startup\.py|account_exec\.py|dingzhen\.py'

if [ ! -f "$OUT" ]; then
    echo "timestamp,row_type,cpu_pct,mem_used_mb,mem_avail_mb,mem_total_mb,swap_used_mb,load1,load5,load15,proc_name,proc_pid,proc_cpu_pct,proc_mem_mb" > "$OUT"
fi

read_cpu_stat() {
    # /proc/stat 第一行：cpu  user nice system idle iowait irq softirq steal guest guest_nice
    read -r _ u n s i io irq sirq st _ _ < /proc/stat
    echo "$u $n $s $i $io $irq $sirq $st"
}

prev=$(read_cpu_stat)
echo "监控已启动，采样间隔 ${INTERVAL}s，输出：$OUT"
echo "停止：pkill -f monitor_resources.sh"

while true; do
    sleep "$INTERVAL"

    curr=$(read_cpu_stat)
    read -r pu pn ps_ pi pio pirq psirq pst <<< "$prev"
    read -r cu cn cs ci cio cirq csirq cst <<< "$curr"
    prev_idle=$((pi + pio)); curr_idle=$((ci + cio))
    prev_total=$((pu + pn + ps_ + pi + pio + pirq + psirq + pst))
    curr_total=$((cu + cn + cs + ci + cio + cirq + csirq + cst))
    total_delta=$((curr_total - prev_total))
    idle_delta=$((curr_idle - prev_idle))
    if [ "$total_delta" -gt 0 ]; then
        cpu_pct=$(awk -v td="$total_delta" -v id="$idle_delta" 'BEGIN{printf "%.1f", (td-id)*100/td}')
    else
        cpu_pct="0.0"
    fi
    prev="$curr"

    read -r mem_total mem_used mem_avail swap_used < <(
        free -m | awk '/^Mem:/{mt=$2; mu=$3; ma=$7} /^Swap:/{su=$3} END{print mt, mu, ma, su+0}'
    )
    read -r load1 load5 load15 _ < /proc/loadavg
    ts=$(date '+%Y-%m-%d %H:%M:%S')

    printf '%s,sys,%s,%s,%s,%s,%s,%s,%s,%s,,,,\n' \
        "$ts" "$cpu_pct" "$mem_used" "$mem_avail" "$mem_total" "$swap_used" "$load1" "$load5" "$load15" >> "$OUT"

    ps -eo pid,%cpu,rss,args --no-headers 2>/dev/null | grep -E "$PROC_PATTERNS" | grep -v grep | \
    while IFS= read -r line; do
        pid=$(awk '{print $1}' <<< "$line")
        cpu=$(awk '{print $2}' <<< "$line")
        rss_kb=$(awk '{print $3}' <<< "$line")
        cmd=$(awk '{$1=$2=$3=""; print $0}' <<< "$line")
        rss_mb=$(awk -v r="$rss_kb" 'BEGIN{printf "%.1f", r/1024}')
        case "$cmd" in
            *account_exec.py*) pname="account_exec" ;;
            *dingzhen.py*)      pname="dingzhen" ;;
            *startup.py*)       pname="startup" ;;
            *)                  pname="other" ;;
        esac
        printf '%s,proc,,,,,,,,,%s,%s,%s,%s\n' "$ts" "$pname" "$pid" "$cpu" "$rss_mb" >> "$OUT"
    done
done
