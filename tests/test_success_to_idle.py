# tests/test_success_to_idle.py
"""
回归测试：Stop（success）6s 后应转 idle，不应直接 off。

背景：2026-07-04 发现 server.js sweep() 在 success 6s 后直接 delete(sid)，
导致 visibleSessions 为空、server 返回 device_status=off、灯灭。
违反 AGENTS.md §状态-灯效双向对应铁律：绿灯呼吸（idle）= agent 干完了，
success 之后应当显示 idle，让用户知道 agent 仍在但空闲。

修复：sweep() 中 success 6s 后改成 entry.event="SessionStart"; device_status="idle";
lastSeen 重置；不再 delete(sid)。

测试方法：用 hook 脚本发 stop 事件（status=completed），等 7 秒后查询 server 状态。
应当：device_status=idle，winner_event=SessionStart。
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

API = os.environ.get("AGENT_LIGHT_API", "http://127.0.0.1:8787")
HOOK = os.path.expanduser("~/.cursor/hooks/agentcore-light.sh")
TEST_SID = "test-success-idle-sid"


def post_hook(event, sid, status=None):
    payload = {
        "hook_event_name": event,
        "session_id": sid,
        "conversation_id": sid,
    }
    if status:
        payload["status"] = status
    subprocess.run(
        ["sh", HOOK],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=5,
    )


def get_status():
    req = urllib.request.Request(f"{API}/api/status")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    resp = opener.open(req, timeout=3)
    return json.loads(resp.read().decode("utf-8"))


def find_session(status, sid):
    for s in status.get("sessions", []):
        if s.get("sid") == sid:
            return s
    return None


def expect(label, cond, why):
    mark = "[PASS]" if cond else "[FAIL]"
    print(f"  {mark} {label}: {why}")
    return cond


def main():
    print("\n=== 准备：清掉测试 session ===")
    post_hook("sessionEnd", TEST_SID)
    time.sleep(1.5)

    print("\n=== T1: 发 Stop（status=completed）→ 立即状态 ===")
    post_hook("stop", TEST_SID, status="completed")
    time.sleep(1.5)
    status = get_status()
    s = find_session(status, TEST_SID)
    ok1 = expect(
        "Stop 后立即状态",
        s is not None and s.get("device_status") == "success",
        f"期望 success（绿灯常亮），实际 {s.get('device_status') if s else '(session 不存在)'}",
    )

    print("\n=== T2: 等 4s（success 还在显示中）===")
    time.sleep(4)
    status = get_status()
    s = find_session(status, TEST_SID)
    ok2 = expect(
        "Stop + 4s 后",
        s is not None and s.get("device_status") == "success",
        f"期望还是 success（绿灯常亮 6s 阈值内），实际 {s.get('device_status') if s else '(session 不存在)'}",
    )

    print("\n=== T3: 再等 4s（共 8s，已过 6s 阈值，应转 idle 不应 off）===")
    time.sleep(4)
    status = get_status()
    s = find_session(status, TEST_SID)
    if s is None:
        ok3 = expect(
            "Stop + 8s 后",
            False,
            "session 已被删除（不应发生；应当转 idle 保留 session）",
        )
    else:
        ds = s.get("device_status")
        ev = s.get("event")
        ok3 = expect(
            "Stop + 8s 后",
            ds == "idle",
            f"期望 idle（绿灯呼吸），实际 device_status={ds} event={ev}",
        )
        if ds == "off":
            print("       ❌ 这是修复前的 bug 行为：success → off 跳过了 idle")
        elif ds == "idle":
            print("       ✅ 修复后正确：success 6s 后转 idle，session 仍在")

    print("\n=== T4: 再等 5s（共 13s），idle 应仍在（10min TTL 内）===")
    time.sleep(5)
    status = get_status()
    s = find_session(status, TEST_SID)
    ok4 = expect(
        "Stop + 13s 后",
        s is not None and s.get("device_status") == "idle",
        f"期望还是 idle（绿灯呼吸持续），实际 {s.get('device_status') if s else '(session 已删)'}",
    )

    print("\n=== 清理：发 SessionEnd 删测试 session ===")
    post_hook("sessionEnd", TEST_SID)
    time.sleep(1)

    total = sum([ok1, ok2, ok3, ok4])
    print(f"\n=== 总结：{total}/4 通过 ===")
    if total == 4:
        print("✅ 全部通过")
        return 0
    else:
        print("❌ 有失败用例")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[test] 收到 Ctrl+C，进入清理...", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\n[test] 异常退出: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # 无论测试怎么结束都清掉 fake sid
        try:
            post_hook("sessionEnd", TEST_SID)
            time.sleep(0.5)
        except Exception as e:
            print(f"[cleanup] 失败: {e}", file=sys.stderr)
