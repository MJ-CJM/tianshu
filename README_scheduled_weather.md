# 每日天气查询定时任务设置

## 概述
本目录包含设置每日18:00天气查询定时任务的脚本，支持两种方式：
1. **cron任务**（适用于所有Linux/Unix系统）
2. **systemd timer**（适用于支持systemd的现代Linux系统）

## 文件说明
- `weather.py` - Python天气查询脚本（主脚本）
- `weather.sh` - Shell天气查询脚本（备选）
- `setup_cron.sh` - 设置cron定时任务
- `setup_systemd_timer.sh` - 设置systemd定时任务
- `test_weather.sh` - 测试天气脚本是否正常工作
- `README_scheduled_weather.md` - 本说明文件

## 设置步骤

### 1. 测试天气脚本
首先确保天气脚本能正常工作：
```bash
chmod +x test_weather.sh
./test_weather.sh
```

### 2. 选择定时任务方式

#### 方式一：使用cron（推荐）
```bash
chmod +x setup_cron.sh
./setup_cron.sh
```

**优点**：
- 兼容性好，适用于所有Linux/Unix系统
- 配置简单，无需额外依赖
- 日志自动按日期分割

**配置详情**：
- 执行时间：每天 18:00
- 日志位置：`~/logs/weather_YYYYMMDD.log`
- 日志格式：追加写入，按日期分割

#### 方式二：使用systemd timer
```bash
chmod +x setup_systemd_timer.sh
./setup_systemd_timer.sh
```

**优点**：
- 现代Linux系统的标准方式
- 支持更精确的时间控制
- 可与systemd日志系统集成
- 支持持久化（错过执行时间会在系统启动后补执行）

**配置详情**：
- 服务文件：`~/.config/systemd/user/weather.service`
- 定时器文件：`~/.config/systemd/user/weather.timer`
- 日志位置：`~/logs/weather_YYYYMMDD.log`

### 3. 验证设置

#### 验证cron任务
```bash
# 查看当前cron任务
crontab -l

# 查看日志文件
tail -f ~/logs/weather_$(date +%Y%m%d).log
```

#### 验证systemd timer
```bash
# 查看timer状态
systemctl --user status weather.timer

# 查看服务状态
systemctl --user status weather.service

# 查看日志
journalctl --user -u weather.service
```

## 管理定时任务

### 停用定时任务

#### 停用cron任务
```bash
# 编辑cron任务
crontab -e

# 删除相关行后保存
```

#### 停用systemd timer
```bash
systemctl --user stop weather.timer
systemctl --user disable weather.timer
```

### 手动触发

#### 手动触发cron任务
```bash
python3 ~/weather.py >> ~/logs/weather_$(date +%Y%m%d).log 2>&1
```

#### 手动触发systemd服务
```bash
systemctl --user start weather.service
```

## 日志管理

### 日志位置
- cron方式：`~/logs/weather_YYYYMMDD.log`
- systemd方式：`~/logs/weather_YYYYMMDD.log` + systemd日志

### 日志清理
可以添加cron任务自动清理30天前的日志：
```bash
# 添加到crontab
0 0 * * * find ~/logs -name "weather_*.log" -mtime +30 -delete
```

## 故障排除

### 常见问题
1. **脚本无执行权限**
   ```bash
   chmod +x ~/weather.py
   chmod +x setup_*.sh
   chmod +x test_weather.sh
   ```

2. **网络连接问题**
   - 确保能访问 `wttr.in` 域名
   - 检查防火墙设置

3. **Python路径问题**
   - 使用完整路径：`/usr/bin/python3`
   - 或使用虚拟环境

4. **日志目录不存在**
   ```bash
   mkdir -p ~/logs
   ```

### 调试命令
```bash
# 测试脚本直接运行
python3 ~/weather.py

# 查看cron日志
grep CRON /var/log/syslog

# 查看systemd日志
journalctl --user -u weather.service -f
```

## 自定义配置

### 修改执行时间
编辑cron任务或systemd timer文件中的时间设置。

### 修改日志位置
编辑setup脚本中的`LOG_DIR`和`LOG_FILE`变量。

### 添加邮件通知
可以在cron任务中添加邮件发送：
```bash
30 9 * * * /usr/bin/python3 ~/weather.py | mail -s "每日天气报告" your@email.com
```

## 技术支持
如需帮助，请联系兵部·唐伯虎。