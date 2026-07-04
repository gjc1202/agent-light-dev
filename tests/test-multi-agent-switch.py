#!/usr/bin/env python3
"""
压力测试：模拟本地 / SSH 两边会话快速交替，复现「跑马灯不触发、卡绿灯」的边界 case。

用法：
  python3 test-multi-agent-switch.py [--api http://127.0.0.1:8787] [--hook ~/.cursor/hooks/agentcore-light.sh]

测试策略：
  1. 用真实 hook 脚本（本地路径）发事件，模拟两个会话快速交替
  2. 每个场景后查询 /api/status，记录 winner、sessions、reason
  3. 找出「期望 thinking/busy 但实际是 idle/success」的失败用例
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time, urllib.request

# 所有可能用到的 fake sid 集中管理，确保测试结束（包括异常退出）时全部清理干净。
# 历史教训（2026-07-04）：早期清理只在 main 末尾、且只清部分 sid；脚本中途失败退出
# 会让 zombie-A / fresh-A 等 fake session 残留在 server 里，priority 40 的陈旧 thinking
# 会压住真实 cursor session 的 success（priority 20），导致用户看到「跑马灯不切 success」。
ALL_FAKE_SIDS = [
    "ssh-A", "ssh-B", "ssh-C",
    "local-A", "local-B", "local-C", "local-D",
    "zombie-A", "fresh-A",
    # 兼容未来扩展
    "ssh-D", "local-E",
]

def post_hook(hook_path: str, event: str, sid: str, tool: str | None = None, status: str | None = None):
    payload = {
        "hook_event_name": event,
        "conversation_id": sid,
        "session_id": sid,
    }
    if tool:
        payload["tool_name"] = tool
    if status:
        payload["status"] = status
    raw = json.dumps(payload)
    # hook 脚本读 stdin 写文件，worker 异步转发
    subprocess.run(
        ["sh", hook_path],
        input=raw,
        text=True,
        capture_output=True,
        timeout=5,
    )

def get_status(api: str) -> dict:
    req = urllib.request.Request(f"{api}/api/status")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    resp = opener.open(req, timeout=3)
    return json.loads(resp.read().decode("utf-8"))

def fmt_sessions(sessions: list) -> str:
    return "\n".join(
        f"    - {s['sid'][:24]:24} {s['event']:18} -> {s['device_status']:14} age={s['age_s']}s"
        for s in sessions
    ) or "    (none)"

def expect(label: str, status: dict, expected_status: str, *, note: str = "") -> bool:
    actual = status["device_status"]
    sessions = status.get("sessions", [])
    ok = actual == expected_status
    marker = "✅" if ok else "❌"
    print(f"[{marker}] {label}: expected={expected_status}, actual={actual} {note}")
    if not ok:
        print(f"    sessions:")
        print(fmt_sessions(sessions))
    return ok

def reset_state(api: str, hook: str):
    """场景之间清空所有已知测试会话，并等待真实会话自然过期"""
    for sid in ALL_FAKE_SIDS:
        post_hook(hook, "sessionEnd", sid)
    time.sleep(0.6)

def cleanup_fake_sids(hook: str):
    """无论测试怎么结束（正常 / 失败 / Ctrl+C / 异常），都把所有 fake sid 清干净。
    必须无副作用、可重入——已删除的 sid 再发 sessionEnd 也无害。"""
    for sid in ALL_FAKE_SIDS:
        try:
            post_hook(hook, "sessionEnd", sid)
        except Exception as e:
            print(f"  [warn] cleanup {sid} failed: {e}", file=sys.stderr)
    # 等 worker 转发 + server 处理 sessionEnd
    time.sleep(0.8)

def run_scenario(api: str, hook: str, name: str, steps: list[tuple[str, str, str, str]]) -> int:
    """每步：(action_desc, event, sid, expected_status_after)"""
    print(f"\n=== 场景：{name} ===")
    failures = 0
    for desc, event, sid, expected in steps:
        post_hook(hook, event, sid, tool="Shell", status="completed" if event == "stop" else None)
        time.sleep(0.4)  # 等 worker 转发 + server 处理
        status = get_status(api)
        if not expect(desc, status, expected):
            failures += 1
    return failures

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8787")
    p.add_argument("--hook", default=f"{__import__('os').path.expanduser('~/.cursor/hooks/agentcore-light.sh')}")
    args = p.parse_args()

    print(f"API:  {args.api}")
    print(f"Hook: {args.hook}")

    total_failures = 0
    try:
        # 先清空所有可能残留的 fake sid（上次跑挂了留下的）
        cleanup_fake_sids(args.hook)

        # 场景 1：SSH 完成后，本地立刻发消息（你描述的 case）
        # 关键点：新事件来时清掉旧 success，所以本地思考应能盖过 SSH 的 success
        reset_state(args.api, args.hook)
        total_failures += run_scenario(args.api, args.hook, "SSH 完成后本地立刻发消息", [
            ("SSH 思考",   "beforeSubmitPrompt", "ssh-A",   "thinking"),
            ("SSH 调工具", "preToolUse",         "ssh-A",   "busy"),
            ("SSH 完成",   "stop",               "ssh-A",   "success"),
            ("本地思考",   "beforeSubmitPrompt", "local-A", "thinking"),  # 应立即盖过 success
            ("本地调工具", "preToolUse",         "local-A", "busy"),
        ])

        # 场景 2：两边同时活跃（验证优先级合并，不要求严格顺序）
        print("\n=== 场景：两边同时调工具 ===")
        reset_state(args.api, args.hook)
        post_hook(args.hook, "beforeSubmitPrompt", "ssh-B")
        time.sleep(0.3)
        if not expect("SSH 思考", get_status(args.api), "thinking"):
            total_failures += 1
        post_hook(args.hook, "preToolUse", "ssh-B", tool="Shell")
        time.sleep(0.3)
        if not expect("SSH 调工具", get_status(args.api), "busy"):
            total_failures += 1
        post_hook(args.hook, "preToolUse", "local-B", tool="Shell")
        time.sleep(0.3)
        if not expect("本地也调工具（应仍 busy）", get_status(args.api), "busy"):
            total_failures += 1
        post_hook(args.hook, "stop", "ssh-B", status="completed")
        time.sleep(0.3)
        # 本地还在 busy，所以 winner 应仍是 busy（不能被 ssh 的 success 盖住）
        if not expect("SSH 完成（本地仍 busy，不应被 success 盖）", get_status(args.api), "busy"):
            total_failures += 1
        post_hook(args.hook, "stop", "local-B", status="completed")
        time.sleep(0.3)
        # 两边都完成了，应是 success
        if not expect("本地也完成（两边都完成，应 success）", get_status(args.api), "success"):
            total_failures += 1

        # 场景 3：快速交替（最容易复现边界）
        print("\n=== 场景：快速交替（10 轮） ===")
        reset_state(args.api, args.hook)
        for i in range(10):
            ssh_first = i % 2 == 0
            order = [("ssh",  "ssh-C"),  ("local", "local-C")] if ssh_first else [("local", "local-C"), ("ssh", "ssh-C")]
            for label, sid in order:
                post_hook(args.hook, "preToolUse", sid, tool="Shell")
                time.sleep(0.05)
            time.sleep(0.3)
            status = get_status(args.api)
            # 期望：两边都在 busy
            if not expect(f"轮 {i+1} ({label})", status, "busy"):
                total_failures += 1

        # 场景 4：僵尸 busy 不应卡住新会话（核心修复点）
        print("\n=== 场景：僵尸 busy 不应卡住新会话 ===")
        reset_state(args.api, args.hook)
        # 制造一个 65 秒前的 PreToolUse（v1.1 后阈值是 60s，所以等 65s）
        post_hook(args.hook, "preToolUse", "zombie-A", tool="Shell")
        print("  等待 65 秒，让 zombie-A 超过 ZOMBIE_PRE_TOOL_MS（60s）阈值...")
        time.sleep(65)
        # 现在发新事件，zombie-A 应被清掉
        post_hook(args.hook, "beforeSubmitPrompt", "fresh-A")
        time.sleep(0.3)
        status = get_status(args.api)
        if not expect("新会话发消息（僵尸 busy 应被清，winner=thinking）", status, "thinking"):
            total_failures += 1
        # 检查 zombie-A 是否还在 sessions 里
        zombie_still_there = any(s["sid"] == "zombie-A" for s in status.get("sessions", []))
        if zombie_still_there:
            print("  ❌ zombie-A 仍在 sessions 列表里（应当被清掉）")
            total_failures += 1
        else:
            print("  ✅ zombie-A 已被清除")

        print(f"\n=== 总结 ===")
        print(f"失败用例：{total_failures}")
        return 1 if total_failures > 0 else 0
    except KeyboardInterrupt:
        print("\n[test] 收到 Ctrl+C，中断测试，进入清理...", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"\n[test] 异常退出: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    finally:
        # 无论怎么退出，都清掉所有 fake sid，避免污染生产 server
        print("\n[test] 清理 fake sid...")
        cleanup_fake_sids(args.hook)
        # 验证清理结果（只报告，不影响 exit code）
        try:
            remaining = [s for s in get_status(args.api).get("sessions", []) if s["sid"] in ALL_FAKE_SIDS]
            if remaining:
                print(f"[test] ⚠️ 清理后仍有 {len(remaining)} 个 fake sid 残留:", file=sys.stderr)
                for s in remaining:
                    print(f"  {s['sid']}: event={s['event']} status={s['device_status']}", file=sys.stderr)
            else:
                print("[test] ✅ 所有 fake sid 已清理干净")
        except Exception as e:
            print(f"[test] 验证清理失败（不影响测试结论）: {e}", file=sys.stderr)

if __name__ == "__main__":
    sys.exit(main())
