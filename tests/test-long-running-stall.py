#!/usr/bin/env python3
"""复现「Agent 长时间思考后被当僵尸清掉」的 bug"""
import json, time, urllib.request, sys

API = "http://127.0.0.1:8787"
SID = "long-running-agent"

def post(event, tool=None, status=None):
    payload = {"hook_event_name": event, "conversation_id": SID, "session_id": SID, "source": "local"}
    if tool: payload["tool_name"] = tool
    if status: payload["status"] = status
    req = urllib.request.Request(f"{API}/hook?agent=cursor",
        data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"}, method="POST")
    urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=3)

def status():
    req = urllib.request.Request(f"{API}/api/status")
    return json.loads(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=3).read().decode())

def show(label):
    s = status()
    sessions = s.get("sessions", [])
    mine = [x for x in sessions if x["sid"] == SID]
    if mine:
        m = mine[0]
        print(f"  [{label:8}] winner={s['device_status']:14} my session: event={m['event']:18} age={m['age_s']}s")
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
    # 清掉旧状态
    cleanup()

    print("=== 模拟 Agent 长时间连续运行 ===\n")

    print("[1] 发消息 → 思考")
    post("beforeSubmitPrompt")
    time.sleep(0.5); show("0.5s")

    print("\n[2] 调工具 #1 → busy")
    post("preToolUse", tool="Shell")
    time.sleep(0.5); show("1s")

    print("\n[3] 工具完成 → 思考（PostToolUse）")
    post("postToolUse", tool="Shell")
    time.sleep(0.5); show("1.5s")

    print("\n[4] Agent 在长时间思考（每 5 秒查一次，到 40 秒）")
    for t in [5, 10, 15, 20, 25, 28, 31, 34, 37, 40]:
        time.sleep(5)
        show(f"{t}s")

    print("\n[5] Agent 终于调下一个工具")
    post("preToolUse", tool="Shell")
    time.sleep(0.5); show("恢复")

    print("\n=== 诊断 ===")
    print("如果在 30s 左右看到「❌ 丢失」+ winner=off，就是 bug 复现了")
    print("正确行为：会话一直在，winner 持续 thinking")
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
