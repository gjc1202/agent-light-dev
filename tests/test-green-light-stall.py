#!/usr/bin/env python3
"""
复现「绿灯常亮时本地不触发跑马灯」的边界 case。

用户报告：
  当会话在本地和 ssh 服务器之间来回交替的时候，如果绿灯还在常亮状态，
  本地端有概率不触发跑马灯而一直处于绿灯状态。

假设 1：success 优先级虽低，但「同会话」的旧 success 还在
假设 2：本地新事件来时，由于会话池里有 ssh 的旧 PreToolUse（busy, 高优先级），
        被它压住，所以 winner 是 busy 而不是 thinking；但用户感受是「卡在绿灯」，
        说明可能 success 没被清掉，或者 winner 计算出了边界 case
假设 3：Cursor 取消 hook（canceled by signal abort）→ 本地事件根本没发到服务
        → 服务停在 SSH 留下的最后一个状态
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time, urllib.request

def post_hook(hook: str, event: str, sid: str, source: str = "local", tool=None, status=None):
    payload = {
        "hook_event_name": event,
        "conversation_id": sid,
        "session_id": sid,
        "agent_signal_source": "cursor",
        "source": source,  # 标记 local / ssh，用于诊断
    }
    if tool: payload["tool_name"] = tool
    if status: payload["status"] = status
    subprocess.run(["sh", hook], input=json.dumps(payload), text=True, capture_output=True, timeout=5)

def post_direct(api: str, event: str, sid: str, source: str = "local", tool=None, status=None):
    """直接 POST 到 /hook，绕过 hook 脚本和 queue worker，验证是否是事件丢失问题"""
    payload = {
        "hook_event_name": event,
        "conversation_id": sid,
        "session_id": sid,
        "agent_signal_source": "cursor",
        "source": source,
    }
    if tool: payload["tool_name"] = tool
    if status: payload["status"] = status
    req = urllib.request.Request(
        f"{api}/hook?agent=cursor",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    resp = opener.open(req, timeout=3)
    return json.loads(resp.read().decode("utf-8"))

def get_status(api: str) -> dict:
    req = urllib.request.Request(f"{api}/api/status")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    resp = opener.open(req, timeout=3)
    return json.loads(resp.read().decode("utf-8"))

# 所有可能用到的 fake sid 集中管理，确保测试结束（包括异常退出）时全部清理干净。
ALL_FAKE_SIDS = ["ssh-A", "local-A", "real-1", "real-2"]

def reset(api: str, hook: str):
    for sid in ALL_FAKE_SIDS:
        post_hook(hook, "sessionEnd", sid)
    time.sleep(0.6)

def cleanup(hook: str):
    """无论测试怎么结束都清掉所有 fake sid，避免污染生产 server。"""
    for sid in ALL_FAKE_SIDS:
        try:
            post_hook(hook, "sessionEnd", sid)
        except Exception as e:
            print(f"[cleanup] {sid} 失败: {e}", file=sys.stderr)
    time.sleep(0.6)

def show(label, status):
    w = status["device_status"]
    sessions = status.get("sessions", [])
    print(f"  {label}: winner={w} event={status['winner_event']}")
    for s in sessions:
        sid = s['sid'][:18]
        print(f"    - {sid:18} agent={s['agent']:6} event={s['event']:18} status={s['device_status']:14} age={s['age_s']}s")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8787")
    p.add_argument("--hook", default=f"{__import__('os').path.expanduser('~/.cursor/hooks/agentcore-light.sh')}")
    p.add_argument("--direct", action="store_true", help="直接 POST 到 /hook，绕过 hook 脚本")
    args = p.parse_args()

    post = lambda event, sid, source, tool=None, status=None: (
        post_direct(args.api, event, sid, source, tool, status) if args.direct
        else post_hook(args.hook, event, sid, source, tool, status)
    )

    print(f"=== 复现「绿灯常亮时本地不触发跑马灯」 ===")
    print(f"模式: {'直接 POST' if args.direct else 'hook 脚本'}")

    failures = 0

    try:
        # 先清掉可能残留的 fake sid
        cleanup(args.hook)

        # 场景 A：SSH 完成（绿灯常亮）后，本地立刻发消息（through hook script）
        print("\n--- 场景 A：SSH success → 本地思考 ---")
        reset(args.api, args.hook)
        post("beforeSubmitPrompt", "ssh-A", "ssh")    # ssh 思考
        time.sleep(0.3); show("ssh 思考", get_status(args.api))
        post("stop", "ssh-A", "ssh", status="completed")  # ssh 完成 → 绿灯常亮
        time.sleep(0.3); s = get_status(args.api); show("ssh 完成 (期望 success)", s)
        if s["device_status"] != "success":
            print("  ❌ ssh 完成后不是 success"); failures += 1

        post("beforeSubmitPrompt", "local-A", "local")  # 本地立刻发消息
        time.sleep(0.3); s = get_status(args.api); show("本地思考 (期望 thinking)", s)
        if s["device_status"] != "thinking":
            print("  ❌ 本地思考被卡住，没触发跑马灯！"); failures += 1
        else:
            print("  ✅ 本地思考正确触发")

        # 场景 B：SSH 在本地干活时突然插进来发 Stop（模拟 ssh 窗口残留 hook）
        print("\n--- 场景 B：本地 busy → ssh 残留 Stop ---")
        reset(args.api, args.hook)
        post("beforeSubmitPrompt", "local-A", "local")
        time.sleep(0.2)
        post("preToolUse", "local-A", "local", tool="Shell")  # 本地调工具 → busy
        time.sleep(0.3); s = get_status(args.api); show("本地 busy", s)
        if s["device_status"] != "busy":
            print("  ❌ 本地没进 busy"); failures += 1

        # ssh 残留一个 Stop 事件（模拟 ssh 窗口的 hook 慢半拍到达）
        post("stop", "ssh-A", "ssh", status="completed")
        time.sleep(0.3); s = get_status(args.api); show("ssh 残留 Stop (期望仍 busy)", s)
        # 关键：本地还在 busy，ssh 的 success 不能盖住
        if s["device_status"] != "busy":
            print(f"  ❌ ssh 残留 Stop 把本地 busy 盖住了！winner={s['device_status']}")
            failures += 1
        else:
            print("  ✅ ssh 残留 Stop 没有盖住本地 busy")

        # 场景 C：本地新会话，但 ssh 留下 success 没清
        print("\n--- 场景 C：ssh 完成 → 本地 SessionStart（模拟新窗口） ---")
        reset(args.api, args.hook)
        post("stop", "ssh-A", "ssh", status="completed")
        time.sleep(0.3); show("ssh 完成", get_status(args.api))
        # 本地新窗口打开，发 SessionStart
        post("sessionStart", "local-A", "local")
        time.sleep(0.3); s = get_status(args.api); show("本地 sessionStart (期望 idle 或 success 已清)", s)
        # 旧 ssh 的 success 应当被新事件清掉
        ssh_sessions = [x for x in s.get("sessions", []) if x["sid"].startswith("ssh-A")]
        if ssh_sessions:
            print(f"  ❌ ssh-A 残留未清: {ssh_sessions[0]}"); failures += 1
        else:
            print("  ✅ ssh-A 已被清")

        # 场景 D：快速乒乓（最容易暴露并发竞态）
        print("\n--- 场景 D：乒乓 5 轮（ssh/local 间隔 50ms） ---")
        reset(args.api, args.hook)
        for i in range(5):
            post("preToolUse", "ssh-A", "ssh", tool="Shell")
            time.sleep(0.05)
            post("preToolUse", "local-A", "local", tool="Shell")
            time.sleep(0.3)
            s = get_status(args.api)
            # 两边都在调工具，winner 必须是 busy
            if s["device_status"] != "busy":
                print(f"  ❌ 轮 {i+1}: winner={s['device_status']} (期望 busy)")
                show(f"轮 {i+1}", s)
                failures += 1
            else:
                print(f"  ✅ 轮 {i+1}: busy")
        # 最后一轮让本地发新消息，必须能回到 thinking
        post("beforeSubmitPrompt", "local-A", "local")
        time.sleep(0.3); s = get_status(args.api)
        # 注意：本地之前是 PreToolUse，发新消息后是 UserPromptSubmit，winner 优先级
        # busy(55) > thinking(40)，但本地自己的 session 从 PreToolUse 改成 UserPromptSubmit
        # ssh-A 还是 PreToolUse，所以 winner 仍应是 busy
        # 但用户感受是「回不到跑马灯」—— 这是真问题：只要 ssh 还在 busy，本地就回不到跑马灯
        show("乒乓后本地发消息", s)
        if s["device_status"] != "busy":
            print(f"  ⚠️  乒乓后 winner={s['device_status']} (实际是 ssh busy 在压住)")
        # 这里我们不判定 fail，只是记录现象

        print(f"\n=== 总结 ===")
        print(f"失败用例：{failures}")
        return 1 if failures > 0 else 0
    except KeyboardInterrupt:
        print("\n[test] 收到 Ctrl+C，进入清理...", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"\n[test] 异常退出: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    finally:
        print("\n[test] 清理 fake sid...")
        cleanup(args.hook)
        # 验证清理
        try:
            remaining = [s for s in get_status(args.api).get("sessions", []) if s["sid"] in ALL_FAKE_SIDS]
            if remaining:
                print(f"[test] ⚠️ 清理后仍有 {len(remaining)} 个 fake sid 残留:", file=sys.stderr)
                for s in remaining:
                    print(f"  {s['sid']}: event={s['event']}", file=sys.stderr)
            else:
                print("[test] ✅ 所有 fake sid 已清理干净")
        except Exception as e:
            print(f"[test] 验证清理失败: {e}", file=sys.stderr)

if __name__ == "__main__":
    sys.exit(main())
