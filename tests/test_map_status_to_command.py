# tests/test_map_status_to_command.py
"""
回归测试：bridges/codex_status_bridge.map_status_to_command()

背景：2026-07-04 发现 bridge 把 device_status=off 翻译成 "idle"，
导致 ESP32 收到 idle 后持续绿灯呼吸，永远不灭。
违反状态-灯效双向对应铁律（off = 灯灭）。

本测试直接覆盖映射函数，确保 off / SessionEnd / effect_id=off 三条路径
都返回 "off"，而不是 "idle"。
"""

import os
import sys

# 让测试能 import 工程模块
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BRIDGES = os.path.join(ROOT, "bridges")
sys.path.insert(0, BRIDGES)

from codex_status_bridge import map_status_to_command  # noqa: E402


def case(payload, expected, why):
    got = map_status_to_command(payload)
    assert got == expected, (
        f"\n  payload={payload}\n  expected={expected!r}\n  got={got!r}\n  why={why}"
    )
    print(f"  ok: {payload} -> {got!r}  ({why})")


def test_device_status_off_must_return_off():
    """server 10min TTL 后 device_status=off —— 必须灭灯，不能绿灯呼吸"""
    print("\n[T1] device_status=off → 'off'（不是 'idle'）")
    case({"device_status": "off", "winner_event": "off", "effect_id": "off"},
         "off",
         "10min TTL 后灯必须灭（双向对应铁律）")
    case({"device_status": "off"},
         "off",
         "仅 device_status 字段时也必须 off")
    case({"display_state": "off"},
         "off",
         "display_state 兜底也要 off")


def test_session_end_must_return_off():
    """SessionEnd = 窗口关闭/用户切走 → 灯灭"""
    print("\n[T2] winner_event=SessionEnd → 'off'")
    case({"winner_event": "SessionEnd"},
         "off",
         "SessionEnd 语义 = 没活跃 agent，灯应灭")
    case({"winner_event": "SessionEnd", "effect_id": "idle_green"},
         "off",
         "SessionEnd 优先级高于 effect_id")


def test_effect_id_off_must_return_off():
    """effect_id=off 也是 off 语义"""
    print("\n[T3] effect_id=off → 'off'")
    case({"effect_id": "off"},
         "off",
         "effect_id=off 也是灭灯语义")


def test_session_start_still_idle():
    """SessionStart 应保持绿灯呼吸"""
    print("\n[T4] winner_event=SessionStart → 'idle'（回归保护）")
    case({"winner_event": "SessionStart"},
         "idle",
         "SessionStart = agent 干完，绿灯呼吸")
    case({"winner_event": "SessionStart", "effect_id": "idle_green"},
         "idle",
         "组合也要 idle")


def test_working_states_unchanged():
    """工作状态不变"""
    print("\n[T5] 工作状态映射不变（回归保护）")
    case({"device_status": "thinking"}, "thinking", "thinking")
    case({"device_status": "busy"}, "busy", "busy")
    case({"winner_event": "PreToolUse"}, "busy", "PreToolUse=busy")
    case({"winner_event": "PostToolUse"}, "thinking", "PostToolUse=thinking")
    case({"winner_event": "UserPromptSubmit"}, "thinking", "UserPromptSubmit=thinking")
    case({"winner_event": "Stop"}, "success", "Stop=success")
    case({"winner_event": "StopFailure"}, "error", "StopFailure=error")
    case({"effect_id": "working_yellow"}, "thinking", "黄灯=thinking")
    case({"effect_id": "error_red"}, "error", "红灯=error")
    case({"device_status": "unknown"}, "unknown", "unknown 三色慢闪")


def test_fallback_still_idle():
    """无任何线索时 idle（保守默认）"""
    print("\n[T6] 空 payload 兜底 → 'idle'")
    case({}, "idle", "无字段时保守默认 idle")


def main():
    test_device_status_off_must_return_off()
    test_session_end_must_return_off()
    test_effect_id_off_must_return_off()
    test_session_start_still_idle()
    test_working_states_unchanged()
    test_fallback_still_idle()
    print("\n✅ 全部通过")


if __name__ == "__main__":
    main()
