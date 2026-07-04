# 常见问题排查

按 `./scripts/doctor.sh` 的输出对照排查。

## 灯完全不亮 / 不响应

### 1. Mac web 服务没跑

```
[FAIL] Mac web 服务          无法访问 http://127.0.0.1:8787/api/status
```

**修复**：

```bash
./scripts/start-mac.sh
```

### 2. BLE 桥接没跑

```
[FAIL] BLE 桥接              未在跑
```

**修复**：

```bash
./scripts/start-bridge-mac.sh
# 看日志
tail -f /tmp/agentcore-light-bridge.log
```

### 3. ESP32 没供电 / BLE 没配对

- USB 线插好（接电脑或显示器 hub 都行，只要 5V 供电）
- 重启 ESP32（断电再插）
- BLE 设备名应是 `AgentCore-Light`

### 4. Cursor hooks 没装

```
[FAIL] Cursor hooks          ~/.cursor/hooks.json 不存在
```

**修复**：

```bash
node scripts/install-hooks.js
# 然后 Reload Window（Cmd+Shift+P）
```

## 灯卡在某个状态不变

### 5. 一直绿灯呼吸（idle）

可能原因：
- Cursor 重启后 hook 还没加载完（等几秒）
- 你切到 SSH 窗口干活，本地没事件（正常行为，详见 SSH-SETUP.md）
- hook 被 Cursor 取消（看 `~/Library/Application Support/Cursor/logs/.../cursor.hooks.*.log`）

**诊断**：

```bash
curl -s http://127.0.0.1:8787/api/status | python3 -m json.tool
# 看 sessions 字段：有没有活跃会话？
```

### 6. 一直黄灯闪（busy）

**根因**：Cursor 偶尔漏发 `PostToolUse` 或 `Stop`，留下的 `PreToolUse` 把 winner 锁住。

**自动修复**：周期性僵尸扫描会清掉 3 秒以上的 PreToolUse。如果还卡住：

```bash
# 手动清空会话池
curl -X POST http://127.0.0.1:8787/event -d 'O'  # 模拟「关灯」按钮
```

### 7. 一直绿灯常亮（success）

**根因**：旧代码里 success 优先级过高 + 过期太慢。新代码已修。

如果还遇到，确认 server 是新代码：

```bash
grep -c "ZOMBIE_PRE_TOOL_MS" web-dashboard/server.js
# 应返回 ≥1
```

不是的话，重启服务用新代码：

```bash
pkill -f "node server.js"
./scripts/start-mac.sh
```

## SSH 远程不工作

### 8. Linux 上 `curl 127.0.0.1:18787` 不通

SSH 隧道没建立。详见 [SSH-SETUP.md §排查](./SSH-SETUP.md#排查)。

### 9. Linux worker 没跑

```bash
ssh gjc_2031 'systemctl --user status agentcore-light-hook'
# 或重装
scp scripts/install-remote.sh gjc_2031:/tmp/ && ssh gjc_2031 'bash /tmp/install.sh'
```

## 多窗口同时跑时灯乱跳

### 10. 灯和当前窗口不一致

**这是设计限制，不是 bug**：一盏灯只能表示一种全局状态。看 dashboard 上的 `LOCAL` / `SSH` 标签可知道每个会话来自哪里。

如果真的需要分清「是哪个窗口在干活」：
- 看网页 `http://127.0.0.1:8787` 的 sessions 列表
- 或买多盏灯（每盏绑一个 source）

## 性能问题

### 11. Cursor 变慢

理论上不会，hook 脚本执行时间 < 50ms（写一个文件就退出）。如果还是慢：

```bash
# 看 hook 执行时间
grep "agentcore-light" ~/Library/Application\ Support/Cursor/logs/.../cursor.hooks.*.log | tail -10
# 应都是 (Xms) exit code: 0，X < 50
```

### 12. Mac 风扇狂转

BLE 扫描占 CPU 不正常。检查：

```bash
ps aux | grep ble_status_bridge | grep -v grep
# CPU% 应 < 5%
```

如果高，可能是 BLE 重连风暴。重启桥接：

```bash
pkill -f ble_status_bridge.py
./scripts/start-bridge-mac.sh
```

## 重置一切

```bash
# 杀所有相关进程
pkill -f "node server.js|ble_status_bridge|hook-queue-worker"

# 清空会话池（重启服务即清）
./scripts/start-mac.sh

# 看 server 日志
tail -f /tmp/agentcore-light-web.log

# 跑测试
python3 tests/test-multi-agent-switch.py
python3 tests/test-green-light-stall.py --direct
```

## 还是不行

收集这些信息后开 issue：

```bash
./scripts/doctor.sh 2>&1
curl -s http://127.0.0.1:8787/api/status | python3 -m json.tool
tail -50 /tmp/agentcore-light-web.log
tail -50 /tmp/agentcore-light-bridge.log
tail -50 ~/Library/Application\ Support/Cursor/logs/$(ls -t ~/Library/Application\ Support/Cursor/logs | head -1)/window1_wb0/$(ls -t ~/Library/Application\ Support/Cursor/logs/$(ls -t ~/Library/Application\ Support/Cursor/logs | head -1)/window1_wb0 | grep cursor.hooks | head -1)
```
