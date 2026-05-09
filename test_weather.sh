#!/bin/bash
# 测试天气脚本是否正常工作

WEATHER_SCRIPT="$HOME/weather.py"

if [ ! -f "$WEATHER_SCRIPT" ]; then
    echo "错误: 天气脚本不存在: $WEATHER_SCRIPT"
    exit 1
fi

echo "测试天气脚本..."
echo "========================"
python3 "$WEATHER_SCRIPT"
echo ""
echo "========================"
echo "测试完成。如果看到天气信息，说明脚本正常工作。"
echo "如果看到错误，请检查网络连接和脚本权限。"