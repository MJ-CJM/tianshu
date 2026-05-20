#!/bin/bash
# 设置每日9:30的天气查询定时任务
# 使用systemd timer（适用于支持systemd的Linux系统）

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

# 创建systemd用户服务文件
SERVICE_DIR="$HOME/.config/systemd/user"
mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_DIR/weather.service" << EOF
[Unit]
Description=每日天气查询服务
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $WEATHER_SCRIPT
StandardOutput=append:$LOG_FILE
StandardError=append:$LOG_FILE
EOF

# 创建systemd用户timer文件
cat > "$SERVICE_DIR/weather.timer" << EOF
[Unit]
Description=每日18:00天气查询定时器

[Timer]
OnCalendar=*-*-* 18:00:00
Persistent=true
RandomizedDelaySec=0

[Install]
WantedBy=timers.target
EOF

# 重新加载systemd配置
systemctl --user daemon-reload

# 启用并启动timer
systemctl --user enable weather.timer
systemctl --user start weather.timer

echo "systemd timer已设置:"
echo "  时间: 每天 9:30"
echo "  脚本: $WEATHER_SCRIPT"
echo "  日志: $LOG_FILE"
echo ""
echo "查看timer状态: systemctl --user status weather.timer"
echo "查看服务状态: systemctl --user status weather.service"
echo "查看日志: tail -f $LOG_FILE"
echo ""
echo "要手动触发一次: systemctl --user start weather.service"