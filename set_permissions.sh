#!/bin/bash
# 设置所有脚本的执行权限

echo "设置脚本执行权限..."

# 设置Python脚本权限
chmod +x weather.py
echo "✓ weather.py"

# 设置Shell脚本权限
chmod +x weather.sh
echo "✓ weather.sh"

# 设置测试脚本权限
chmod +x test_weather.sh
echo "✓ test_weather.sh"

# 设置安装脚本权限
chmod +x setup_cron.sh
echo "✓ setup_cron.sh"

chmod +x setup_systemd_timer.sh
echo "✓ setup_systemd_timer.sh"

echo ""
echo "所有脚本已设置执行权限。"
echo "现在可以运行: ./test_weather.sh 来测试天气脚本。"