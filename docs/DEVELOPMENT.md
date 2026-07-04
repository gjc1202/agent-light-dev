# 开发与测试

## 开发环境

```bash
./scripts/setup-mac.sh     # 装环境
./scripts/start-mac.sh     # 启动系统
```

代码热加载：改 `web-dashboard/server.js` 后需重启：

```bash
pkill -f "node server.js"
cd web-dashboard && node server.js
```

前端 `web-dashboard/static/*` 改完刷新浏览器即可。

## 跑测试

```bash
# 多窗口切换压力测试（24 个用例）
python3 tests/test-multi-agent-switch.py

# 绿灯常亮边界 case 复现（直接 POST，绕过 hook）
python3 tests/test-green-light-stall.py --direct

# 通过 hook 脚本测（会受真实 Cursor 会话干扰）
python3 tests/test-green-light-stall.py
```

测试隔离：每个场景前调 `reset_state()` 清空已知会话。

## 加新测试

参考 `tests/test-multi-agent-switch.py` 的结构：

```python
def reset(api, hook):
    for sid in [...]:
        post_hook(hook, "sessionEnd", sid)
    time.sleep(0.6)

def main():
    reset(args.api, args.hook)
    # 你的场景
    post_hook(args.hook, "beforeSubmitPrompt", "sess-A")
    time.sleep(0.3)
    status = get_status(args.api)
    expect("描述", status, "thinking")
```

`post_hook` 走 hook 脚本（验证端到端），`post_direct` 直接 POST（验证服务逻辑，不受 hook 干扰）。

## 模拟事件

```bash
# 命令行模拟 Cursor 发的 hook
echo '{"hook_event_name":"preToolUse","conversation_id":"test","session_id":"test","tool_name":"Shell"}' \
  | sh ~/.cursor/hooks/agentcore-light.sh

# 直接 POST 到服务（绕过 hook 队列）
curl -X POST -H 'Content-Type: application/json' \
  -d '{"hook_event_name":"stop","conversation_id":"test","session_id":"test","status":"completed"}' \
  'http://127.0.0.1:8787/hook?agent=cursor'

# 查当前状态
curl -s http://127.0.0.1:8787/api/status | python3 -m json.tool
```

## 改灯效

编辑 `web-dashboard/data/config.json`：

```json
{
  "effects": [
    {
      "id": "my_custom",
      "frames": [
        { "leds": ["on", "off", "off"], "ms": 200 },
        { "leds": ["off", "on", "off"], "ms": 200 },
        { "leds": ["off", "off", "on"], "ms": 200 }
      ]
    }
  ],
  "event_bindings": {
    "PreToolUse": "my_custom"
  }
}
```

也可以在网页 dashboard 上改，热生效。

## 加新 Agent 支持

参考 `hooks/cursor/` 的结构。需要：

1. `hooks/<agent>/hook.sh` —— 读 stdin，写队列
2. `hooks/<agent>/hooks.json` —— Cursor/Codex/Claude 各自的 hook 配置格式
3. `scripts/install-hooks.js` —— 加 install 函数
4. `web-dashboard/server.js` 的 `detectAgent()` 加识别

## 改合并优先级

`web-dashboard/server.js`：

```javascript
const DEVICE_STATUS_PRIORITY = {
  error: 70,
  wait_confirm: 60,
  busy: 55,
  thinking: 40,
  ai: 35,
  success: 20,
  idle: 10,
  off: 0
};
```

改完重启 server。

## 改僵尸清理阈值

```javascript
// set() 内
const ZOMBIE_PRE_TOOL_MS = 3_000;    // PreToolUse 多久没 PostToolUse 算僵尸
const ZOMBIE_POST_TOOL_MS = 30_000;  // PostToolUse
const ZOMBIE_PROMPT_MS = 30_000;     // UserPromptSubmit

// statusPayload() 内
function isStaleSuccessSession(session) {
  return session.event === "Stop" && session.age_s > 6;  // success 多久过期
}
```

调小 → 状态切换更快但容易把活跃会话误判为僵尸；调大 → 状态更稳定但卡顿风险高。

## 调试 BLE

```bash
# 看 BLE 桥接日志
tail -f /tmp/agentcore-light-bridge.log

# 手动写 BLE（用 bleak 扫描）
.venv/bin/python -c "
import asyncio
from bleak import BleakScanner
async def main():
    devs = await BleakScanner.discover(timeout=5)
    for d in devs:
        if d.name and 'Agent' in d.name:
            print(d.name, d.address)
asyncio.run(main())
"
```

## 调试 ESP32 固件

USB 接电脑，串口监视：

```bash
.venv/bin/python -c "
import serial, time
s = serial.Serial('/dev/cu.usbmodem*', 115200)
while True:
    print(s.readline().decode(), end='')
"
```

固件会打印收到的状态切换 log。
