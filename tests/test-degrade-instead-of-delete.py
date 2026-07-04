#!/usr/bin/env python3
"""验证长任务降级：思考超过阈值 → 降级为 unknown（三色慢闪），不删会话"""
import json, sys, time, urllib.request

API = "http://127.0.0.1:8787"
SID = "long-task-test"

def post(event, tool=None):
    p = {"hook_event_name": event, "conversation_id": SID, "session_id": SID, "source": "local"}
    if tool: p["tool_name"] = tool
    req = urllib.request.Request(f"{API}/hook?agent=cursor",
        data=json.dumps(p).encode(), headers={"Content-Type":"application/json"}, method="POST")
    urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=3)

def status():
    req = urllib.request.Request(f"{API}/api/status")
    return json.loads(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=3).read().decode())

def show(label):
    s = status()
    mine = [x for x in s.get("sessions", []) if x["sid"] == SID]
    if mine:
        m = mine[0]
        deg = " [degraded]" if m.get("degraded") else ""
        print(f"  [{label:8}] winner={s['device_status']:14} my session: event={m['event']:18} status={m['device_status']:14} age={m['age_s']}s{deg}")
    else:
        print(f"  [{label:8}] winner={s['device_status']:14} my session: ❌ 丢失")

def cleanup():
    """无论测试怎么结束都清掉 fake sid，避免污染生产 server。"""
    try:
        post("sessionEnd")
        time.sleep(0.5)
    except Exception as e:
        print(f"[cleanup] 失败: {e}", file=sys.stderr)

def main():
    # 清掉上次的残留
    cleanup()

    print("=== 长任务降级测试（v1.2 思路 D）===\n")
    print("模拟 Agent 思考 4 分钟，每分钟看一次状态：\n")

    # 1. 发消息
    post("beforeSubmitPrompt")
    time.sleep(0.3); show("0s")

    # 2. 跑工具
    post("preToolUse", tool="Shell")
    time.sleep(0.3); show("0.3s")
    post("postToolUse", tool="Shell")
    time.sleep(0.3); show("0.6s")

    # 3. 思考 4 分钟（应该 3 分钟时降级，但不删会话）
    print("\n  [等待 4 分钟，每分钟采样一次...]")
    for m in [1, 2, 3, 4]:
        time.sleep(60)
        show(f"{m}min")

    # 4. Agent 终于调下一个工具（应该恢复 busy）
    print("\n  [Agent 终于发新事件]")
    post("preToolUse", tool="Shell")
    time.sleep(0.3); show("恢复")

    print("\n=== 诊断 ===")
    print("期望：")
    print("  - 0-3min: thinking（跑马灯）")
    print("  - 3min+: unknown（三色慢闪），但会话仍在")
    print("  - 恢复: busy（黄灯闪）")
    print("关键：会话不能消失（不像 v1.1 那样被删）")
    return 0

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
        cleanup()
