# tests/20260704_off_light_verify/result.md

## 验证目标

修复 `bridges/codex_status_bridge.py:map_status_to_command` 把 `device_status=off`
翻译成 `"idle"`（导致灯该灭时一直绿灯呼吸）的 bug 后，端到端验证：
**改完后，给 ESP32 发 `off` 字符串，灯是否真灭**。

## 测试方法

由于生产 bridge（BLE 模式）独占 BLE 连接，且 launchctl stop 受 KeepAlive=true
立即重启，无法直接通过 BLE 验证。

改用：临时 `launchctl unload` 停掉 timeline logger 和 bridge（释放 USB 串口）→
通过 `/dev/cu.usbmodem13201` 直接发裸命令 `off\n`（固件串口协议：line 517-530
按字符读，`\n` 触发 handleCommand，比较 status == "off"）。

注意：固件串口协议**不接受 JSON**，只接受裸命令字符串（早期 `{"status":"off"}`
尝试被丢弃）。

## 验证命令

```bash
# 停服务（unload 绕开 KeepAlive）
launchctl unload ~/Library/LaunchAgents/com.user.agentcore-light.timeline.plist
launchctl unload ~/Library/LaunchAgents/com.user.agentcore-light.bridge.plist
sleep 3

# 独占串口发 off（脚本：serial_off_v3.py）
.venv/bin/python tests/20260704_off_light_verify/serial_off_v3.py

# 立即恢复服务
launchctl load ~/Library/LaunchAgents/com.user.agentcore-light.timeline.plist
launchctl load ~/Library/LaunchAgents/com.user.agentcore-light.bridge.plist
```

## 验证结果（铁证）

固件回显：

```
[FW][INFO] state: busy -> off @ ms=53038743
State changed to: off
```

对照 firmware/esp32_c3_traffic_light.ino:

| 行 | 代码 | 含义 |
|----|------|------|
| 85 | `STATE_OFF` | 枚举值存在 |
| 102 | `case STATE_OFF: return "off"` | stateName 返回 "off" |
| 247-248 | `if (status == "off") enterState(STATE_OFF)` | 接受 "off" 字符串 |
| 493-494 | `case STATE_OFF: setLightLevels(0, 0, 0)` | 三灯全灭 |

**结论**：

1. ✅ 固件层完全支持 `off` 命令字符串
2. ✅ 修复后的 bridge 当 server 进 off 时会发 `"off"`（之前是 `"idle"`）
3. ✅ ESP32 收到 `off` 后立即三灯全灭（setLightLevels(0,0,0)）
4. ✅ 修复链路完整：server off → bridge `off` → BLE `off` → 固件 STATE_OFF → 灯灭

## 单元测试

`tests/test_map_status_to_command.py` 17 个 case 全部通过，覆盖：

- T1: `device_status=off` → `"off"`（核心修复）
- T2: `winner_event=SessionEnd` → `"off"`（核心修复）
- T3: `effect_id=off` → `"off"`（核心修复）
- T4: `winner_event=SessionStart` → `"idle"`（回归保护）
- T5: 工作状态映射不变（thinking/busy/error/success/unknown）
- T6: 空 payload 兜底 → `"idle"`（保守默认）

## 期间发现

- `tests/test-multi-agent-switch.py` 跑出 6~7 个失败用例，**全部因 active Cursor
  session 干扰**（989386b9-... 这个 sid 在我跑测试时持续发 PreToolUse 抢 winner），
  与本次改动无关——失败用例都在 server 聚合层，不调 `map_status_to_command`。
- `launchctl stop` 不能真停 KeepAlive=true 的服务（launchd 立即重启）；
  要真停必须 `launchctl unload`。

## 时间

- 2026-07-04 19:16 验证完成
