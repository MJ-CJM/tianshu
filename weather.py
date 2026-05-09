#!/usr/bin/env python3
"""
天气查询脚本 - 使用wttr.in API
默认查询上海天气，可指定城市和语言
"""

import sys
import json
import urllib.request
import urllib.error
from datetime import datetime

def get_weather(city="Shanghai", lang="zh"):
    """
    获取指定城市的天气信息
    
    Args:
        city: 城市英文名（默认：Shanghai）
        lang: 语言代码（zh=中文，en=英文）
    
    Returns:
        dict: 天气信息字典
    """
    url = f"https://wttr.in/{city}?format=j1&lang={lang}"
    
    try:
        # 设置请求头，模拟浏览器
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        )
        
        # 发送请求，设置超时
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data
            
    except urllib.error.URLError as e:
        print(f"网络错误: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"未知错误: {e}", file=sys.stderr)
        return None

def format_weather(data, city="Shanghai"):
    """
    格式化天气信息为易读的文本
    
    Args:
        data: 天气API返回的JSON数据
        city: 城市名称
    
    Returns:
        str: 格式化后的天气信息
    """
    if not data or 'current_condition' not in data:
        return "无法获取天气信息"
    
    current = data['current_condition'][0]
    today = data['weather'][0] if 'weather' in data else None
    
    # 获取当前时间
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 构建输出
    lines = []
    lines.append(f"🌤️ {city}天气 ({now})")
    lines.append("━" * 20)
    
    # 温度信息
    temp = current.get('temp_C', 'N/A')
    feels_like = current.get('FeelsLikeC', 'N/A')
    lines.append(f"🌡️ 温度: {temp}°C (体感 {feels_like}°C)")
    
    # 天气状况
    weather_desc = current.get('weatherDesc', [{}])[0].get('value', 'N/A')
    lines.append(f"📊 天气: {weather_desc}")
    
    # 湿度和降水概率
    humidity = current.get('humidity', 'N/A')
    precip = current.get('precipMM', '0')
    lines.append(f"💧 湿度: {humidity}%")
    lines.append(f"🌧️ 降水: {precip}mm")
    
    # 风力信息
    wind_speed = current.get('windspeedKmph', 'N/A')
    wind_dir = current.get('winddir16Point', 'N/A')
    lines.append(f"💨 风力: {wind_speed} km/h {wind_dir}")
    
    # 能见度和气压
    visibility = current.get('visibility', 'N/A')
    pressure = current.get('pressure', 'N/A')
    lines.append(f"👁️ 能见度: {visibility} km")
    lines.append(f"📈 气压: {pressure} hPa")
    
    # 今日预报
    if today:
        lines.append("━" * 20)
        max_temp = today.get('maxtempC', 'N/A')
        min_temp = today.get('mintempC', 'N/A')
        lines.append(f"📅 今日: {min_temp}°C ~ {max_temp}°C")
    
    return "\n".join(lines)

def main():
    """主函数"""
    # 解析命令行参数
    city = sys.argv[1] if len(sys.argv) > 1 else "Shanghai"
    lang = sys.argv[2] if len(sys.argv) > 2 else "zh"
    
    # 获取天气数据
    data = get_weather(city, lang)
    
    # 格式化并输出
    if data:
        print(format_weather(data, city))
    else:
        print("获取天气信息失败", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()