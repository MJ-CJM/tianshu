#!/bin/bash
# 天气查询脚本 - 使用wttr.in API
# 默认查询上海天气，可指定城市和语言

# 默认参数
DEFAULT_CITY="Shanghai"
DEFAULT_LANG="zh"

# 获取参数
CITY="${1:-$DEFAULT_CITY}"
LANG="${2:-$DEFAULT_LANG}"

# 检查依赖
if ! command -v curl &> /dev/null; then
    echo "错误: 未找到curl命令" >&2
    exit 1
fi

if ! command -v jq &> /dev/null; then
    echo "错误: 未找到jq命令，请安装: sudo apt-get install jq" >&2
    exit 1
fi

# 构建API URL
URL="https://wttr.in/${CITY}?format=j1&lang=${LANG}"

# 发送请求
echo "正在获取${CITY}天气信息..." >&2
RESPONSE=$(curl -s --max-time 10 -H "User-Agent: Mozilla/5.0" "$URL")

# 检查响应
if [ $? -ne 0 ]; then
    echo "错误: 网络请求失败" >&2
    exit 1
fi

# 检查JSON有效性
if ! echo "$RESPONSE" | jq empty 2>/dev/null; then
    echo "错误: 无效的JSON响应" >&2
    exit 1
fi

# 提取天气信息
CURRENT=$(echo "$RESPONSE" | jq -r '.current_condition[0]')
TODAY=$(echo "$RESPONSE" | jq -r '.weather[0]')

# 获取当前时间
NOW=$(date +"%Y-%m-%d %H:%M")

# 格式化输出
echo "🌤️ ${CITY}天气 (${NOW})"
echo "━━━━━━━━━━━━━━━━━━"

# 温度信息
TEMP=$(echo "$CURRENT" | jq -r '.temp_C')
FEELS_LIKE=$(echo "$CURRENT" | jq -r '.FeelsLikeC')
echo "🌡️ 温度: ${TEMP}°C (体感 ${FEELS_LIKE}°C)"

# 天气状况
WEATHER_DESC=$(echo "$CURRENT" | jq -r '.weatherDesc[0].value')
echo "📊 天气: ${WEATHER_DESC}"

# 湿度和降水概率
HUMIDITY=$(echo "$CURRENT" | jq -r '.humidity')
PRECIP=$(echo "$CURRENT" | jq -r '.precipMM')
echo "💧 湿度: ${HUMIDITY}%"
echo "🌧️ 降水: ${PRECIP}mm"

# 风力信息
WIND_SPEED=$(echo "$CURRENT" | jq -r '.windspeedKmph')
WIND_DIR=$(echo "$CURRENT" | jq -r '.winddir16Point')
echo "💨 风力: ${WIND_SPEED} km/h ${WIND_DIR}"

# 能见度和气压
VISIBILITY=$(echo "$CURRENT" | jq -r '.visibility')
PRESSURE=$(echo "$CURRENT" | jq -r '.pressure')
echo "👁️ 能见度: ${VISIBILITY} km"
echo "📈 气压: ${PRESSURE} hPa"

# 今日预报
if [ "$TODAY" != "null" ]; then
    echo "━━━━━━━━━━━━━━━━━━"
    MAX_TEMP=$(echo "$TODAY" | jq -r '.maxtempC')
    MIN_TEMP=$(echo "$TODAY" | jq -r '.mintempC')
    echo "📅 今日: ${MIN_TEMP}°C ~ ${MAX_TEMP}°C"
fi