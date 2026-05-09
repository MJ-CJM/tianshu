#!/bin/bash
# 设置每日9:30的天气查询定时任务
# 使用cron任务，将输出记录到日志文件

LOG_DIR="$HOME/logs"
LOG_FILE="$LOG_DIR/weather_$(date +%Y%m%d).log"
WEATHER_SCRIPT="$HOME/weather.py"

# 创建日志目录
mkdir -p "$LOG_DIR"

# 检查天气脚本是否存在
if [ ! -f "$WEATHER_SCRIPT" ]; then
    echo "错误: 天气脚本不存在: $WEATHER_SCRIPT"
    echo "请确保weather.py脚本位于家目录"
    exit 1
fi

# 创建cron任务
# 每天9:30运行，输出追加到日志文件
CRON_JOB="30 9 * * * /usr/bin/python3 $WEATHER_SCRIPT >> $LOG_FILE 2>&1"

# 检查是否已存在相同的cron任务
(crontab -l 2>/dev/null | grep -v "$WEATHER_SCRIPT") | { cat; echo "$CRON_JOB"; } | crontab -

echo "cron任务已设置:"
echo "  时间: 每天 9:30"
echo "  脚本: $WEATHER_SCRIPT"
echo "  日志: $LOG_FILE"
echo ""
echo "当前cron任务列表:"
crontab -l

echo ""
echo "要查看日志，请运行: tail -f $LOG_FILE"
echo "要测试脚本，请运行: python3 $WEATHER_SCRIPT"