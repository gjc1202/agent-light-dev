# SSH 远程 Cursor 接入（gjc_2031 实例）

## 适用场景

你在 Mac 上用 Cursor 连一台 Linux 服务器（如 `gjc_2031`）做远程开发，希望 SSH 窗口里的 Agent 也能驱动 Mac 上的红绿灯。

## 前置条件

- Mac 上已完成 [SETUP-MAC.md](./SETUP-MAC.md)，`./scripts/doctor.sh` 全 OK
- Mac → Linux 的 SSH 已配置免密登录（`~/.ssh/config` 有对应条目）
- Linux 上有 `curl`、`python3`、`sh`（一般都有）

## 三个步骤

### 1. Mac 上配 SSH 反向隧道

编辑 `~/.ssh/config`，给你的 Linux 主机加 `RemoteForward`：

```sshconfig
Host gjc_2031
  HostName 202.120.36.100
  Port 2031
  User gjc
  IdentityFile ~/.ssh/id_ed25519
  # ↓ 加这一行：Linux 的 18787 反向转发到 Mac 的 8787
  RemoteForward 18787 127.0.0.1:8787
```

**重连 SSH 窗口**让配置生效。验证：

```bash
ssh gjc_2031 'curl -s http://127.0.0.1:18787/api/status | head -c 50'
# 应返回 {"ok":true,...}
```

### 2. Linux 上装 hooks + queue worker

在 Mac 上跑（脚本会自己 scp 到 Linux）：

```bash
cd ~/Documents/agent-light-dev
scp scripts/install-remote.sh gjc_2031:/tmp/install.sh
ssh gjc_2031 'bash /tmp/install.sh'
```

这个脚本在 Linux 上做：
- 装 `~/.cursor/hooks/agentcore-light.sh`（写队列、标 `source=ssh`）
- 装 `~/.cursor/hooks/hook-queue-worker.py`（异步消费者）
- 装 `~/.cursor/hooks.json`（11 个 Cursor hook 事件）
- 用 **systemd user 服务**（或 nohup 兜底）跑 worker，开机自启、断线重启

### 3. 重启 Cursor 的 SSH 窗口

让 Cursor 重新读 Linux 上的 `~/.cursor/hooks.json`，并触发 `RemoteForward`。

在 SSH 窗口里给 Agent 发消息，Mac 上灯应跟着变。

## 工作原理

```
Linux Cursor 发 hook event
   ↓
~/.cursor/hooks/agentcore-light.sh 写队列（标 source=ssh）
   ↓
hook-queue-worker.py（systemd）异步读队列
   ↓
POST http://127.0.0.1:18787/hook?agent=cursor
   ↓
SSH 反向隧道（随 SSH 连接自动建立）
   ↓
Mac 的 :8787 服务（合并本地 + 远程事件）
   ↓
BLE 桥接 → ESP32 灯
```

## 多窗口同时跑 Agent 时的行为

灯按全局优先级合并：

| 同时发生 | 灯显示 |
|----------|--------|
| 本地调工具 + SSH 完成 | **黄灯闪**（busy 55 > success 20） |
| 本地出错 + SSH 思考 | **红灯闪**（error 70） |
| 本地完成 + SSH 完成 | **绿灯常亮几秒** → 回呼吸 |
| 两边都空闲 | 绿色呼吸 |

详情看 `http://127.0.0.1:8787`，每个会话前会有 `LOCAL` / `SSH` 标签。

## 排查

### Linux 上 `curl 127.0.0.1:18787` 不通

SSH 隧道没建立。检查：
- Mac 的 `~/.ssh/config` 有 `RemoteForward 18787 127.0.0.1:8787`
- 当前 SSH 连接是配置改完**之后**才建立的（旧连接没隧道）
- `ssh -O check gjc_2031` 能查到 ControlMaster 连接

修复：`ssh -O exit gjc_2031` 然后重连。

### Linux 上 worker 没跑

```bash
ssh gjc_2031 'systemctl --user status agentcore-light-hook'
# 或
ssh gjc_2031 'pgrep -af hook-queue-worker.py'
```

如未跑：
```bash
ssh gjc_2031 'systemctl --user start agentcore-light-hook'
# 或重装
scp scripts/install-remote.sh gjc_2031:/tmp/ && ssh gjc_2031 'bash /tmp/install.sh'
```

### Mac 上没收到远程事件

```bash
# Mac 上看 server log
tail -f /tmp/agentcore-light-web.log

# 触发一次测试事件
ssh gjc_2031 'echo "{\"hook_event_name\":\"preToolUse\",\"conversation_id\":\"tunnel-test\",\"session_id\":\"tunnel-test\",\"tool_name\":\"Shell\"}" | sh ~/.cursor/hooks/agentcore-light.sh'

# Mac 上查
curl -s http://127.0.0.1:8787/api/status | python3 -m json.tool | head -30
```

应能看到 `tunnel-test` 会话。

## 不同 Linux 服务器

如果你想接另一台 Linux（不是 gjc_2031），改两处：

1. `~/.ssh/config` 给那台机器加 `RemoteForward`
2. scp `install-remote.sh` 过去跑

每个 Linux 都用一个独立的 worker + queue 目录，互不干扰。

## 限制

- **隧道依赖 SSH 连接存活**：Cursor 重连 SSH 后会自动重建，但断线期间远程事件进不来
- **共享 Linux 端口冲突**：如果同机有别人也用了 18787，改 `RemoteForward` 第一参数为别的端口，同步改 worker URL
- **不能区分本地窗口 vs SSH 窗口的「焦点」**：灯只反映「有没有 Agent 在干活」，不反映「你现在盯着哪个窗口」
