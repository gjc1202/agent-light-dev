# 安全规范（SAFETY）

## 1. 磁盘写满会损坏文件（atomic write 漏洞）

**事故背景**（论文工程 v7 事故的教训）：在 OneDrive 同步盘 / NFS / 写满的磁盘上，atomic write（写临时文件 → rename 覆盖）**会截断已有文件**，原文件被截断到几十行（原 800+ 行全丢）。这个漏洞是文件系统实现的，不是软件 bug。

**红绿灯工程同样适用**：
- `~/.local/` 是用户本地盘，但 macOS 可能因时间机器 / iCloud 同步挂起
- `/tmp/` 是临时盘，可能小
- OneDrive 路径**不可靠**——工程必须放 `~/.local/` 或 `~/Developer/`

**Agent 必须遵守**：

1. **重要文档先 commit**：>100 行的文档起草后立即 `git add . && git commit && git push`
2. **写操作前预检**：批量 sed/rsync/写入前 `python3 scripts/disk_gate.py` 确认 ≥ 5G 可用
3. **大文档不在 OneDrive 起草**：放 `~/.local/agent-light-dev/`，git push 是终极保险
4. **`df -h` 习惯**：任何「我先大量写入」之前先看一眼磁盘

**Red Flag**：
- `df` 显示目标盘可用 < 1G
- 任何写入工具返回 `No space left on device`
- OneDrive 同步正在进行（云图标转）

## 2. macOS 后台启动陷阱

`setsid` 在 macOS 不存在；`(cd ... &)` subshell 模式会让子进程随父 shell 退出被带走。

**正确写法**：

```bash
nohup "$NODE" server.js >/tmp/agentcore-light-web.log 2>&1 </dev/null &
disown
```

**关键三件套**：
- `nohup`：忽略 SIGHUP
- `</dev/null`：stdin 重定向（否则进程读 stdin 时会等）
- `disown`：脱离 shell job 控制

参考：`scripts/start-mac.sh` 的实现。

## 3. macOS TCC 隐私保护

launchd 启动的进程**默认不能访问**：
- `~/Documents/`（除非给 launchd 配权限）
- `~/Desktop/`
- `~/Downloads/`
- iCloud Drive

**所以工程必须放 `~/.local/` 或 `~/Developer/`**，不能放 Documents。

如果 launchd 启动报 `Operation not permitted`，99% 是路径在 TCC 保护目录下。

## 4. SSH 反向隧道断开

`RemoteForward` 跟随 SSH 连接。SSH 断 → 隧道断 → 远程事件丢失。

**缓解**：
- ControlMaster + ControlPersist 自动重连
- worker 失败重试，事件不会丢（队列堆积）
- doctor.sh 定期检查隧道

## 5. OneDrive 同步冲突

如果你在 Mac 上同时用 OneDrive 同步和 git：

- OneDrive 可能因 sync 冲突创建 `*.conflict` 文件
- git 把冲突文件加入版本，混乱
- OneDrive 可能在编辑时锁定文件，写失败

**铁律**：**git 仓库永远不放在 OneDrive 路径下**。

## 6. BLE 连接稳定性

BLE 桥接偶尔会断连（设备休眠、距离远、干扰）。已实现：
- 自动重连（每 3 秒一次）
- 命令重发（每 2 秒）
- 失败计数（连续 10 次失败重启桥接）

如果断连频繁：
- 把 ESP32 离 Mac 更近
- 检查 USB 供电是否稳定（用充电头而不是 hub）
- 重启 ESP32

## 7. 长任务误判

`sleep >60s 占 turn` 是禁止的。但有些操作（烧录固件、长时间训练）本身就要 >60s。

**正确做法**：
- 立即 `nohup` + `register`
- 轮询 log（间隔 ≤60s）
- 不要在 Agent turn 里 `sleep` 等待

## 8. Git 操作红线

| 禁止 | 原因 |
|------|------|
| `git push --force` 到 master | 会覆盖他人工作 |
| `git reset --hard` 已 push 的提交 | 同上 |
| 提交 `~/.ssh/` 任何东西 | 私钥泄露 |
| 提交 `.env` 含密钥 | 同上 |
| 提交 `*.venv/` | 体积爆炸，机器相关 |
| 提交 `*.log` | 体积 + 隐私 |
| 提交运行时 `releases/**/JOB.json` | 机器相关 |

## 9. 烧录固件风险

ESP32 烧录**不会**变砖（除非物理损坏），但要注意：

- 烧录时不要拔 USB
- 用正确的 chip type（`--chip esp32c3`）
- 错的 flash size 可能损坏分区

**回滚**：固件全在 git 里（`firmware/esp32_c3_traffic_light.ino`），重烧即可。
