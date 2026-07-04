#!/usr/bin/env python3
"""
备份 ESP32-C3 当前固件到 .state/firmware-backup/。

备份内容：
- 完整 4MB flash dump（可用于恢复）
- 应用层 .bin / .elf / factory.bin（来自 PIO build）
- 当前源码 snapshot（.ino + platformio.ini）
- INFO.txt（git commit + 时间戳 + 恢复命令）

保留策略：默认保留最近 5 份，更老的自动清理（避免硬盘积累）。

用法：
  ./scripts/firmware-backup.py                # 备份 + 清理旧备份
  ./scripts/firmware-backup.py --keep 10      # 保留最近 10 份
  ./scripts/firmware-backup.py --no-flash     # 跳过 flash dump（只备份编译产物）
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / ".state"
BACKUP_DIR = STATE_DIR / "firmware-backup"
FIRMWARE_DIR = ROOT / "firmware"
BUILD_DIR = FIRMWARE_DIR / ".pio" / "build" / "esp32c3"
PYTHON = ROOT / ".venv" / "bin" / "python"

DEFAULT_KEEP = 5
ESP32_PORT_GLOB = "/dev/cu.usbmodem*"
FLASH_SIZE = 0x400000  # 4MB


def run(cmd, **kw) -> subprocess.CompletedProcess:
    """Run a command, return CompletedProcess."""
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def git_info() -> dict:
    info = {}
    for key, cmd in [
        ("commit", ["git", "rev-parse", "HEAD"]),
        ("short", ["git", "rev-parse", "--short", "HEAD"]),
        ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        ("dirty?", ["git", "status", "--porcelain"]),
    ]:
        r = run(cmd, cwd=ROOT)
        info[key] = r.stdout.strip() if r.returncode == 0 else "?"
    info["dirty"] = bool(info.get("dirty?"))
    return info


def find_esp32_port() -> str | None:
    import glob
    ports = glob.glob(ESP32_PORT_GLOB)
    return ports[0] if ports else None


def dump_flash(out_path: Path, port: str) -> tuple[bool, str]:
    """Dump full flash via esptool."""
    cmd = [
        str(PYTHON), "-m", "esptool",
        "--port", port, "--baud", "460800", "--chip", "esp32c3",
        "read_flash", "0x0", str(FLASH_SIZE), str(out_path),
    ]
    r = run(cmd)
    return r.returncode == 0, r.stderr + r.stdout


def write_info(out_dir: Path, ts: str, ginfo: dict, port: str | None, with_flash: bool):
    rel_root = ROOT
    info = f"""ESP32-C3 firmware backup
========================

Backup time:    {datetime.now().isoformat()}
Timestamp:      {ts}
Git commit:     {ginfo['commit']}
Git branch:     {ginfo['branch']}
Working tree:   {'dirty (uncommitted changes)' if ginfo['dirty'] else 'clean'}
ESP32 port:     {port or '(not connected — flash dump skipped)'}

Files in this directory:
  *.bin            full {FLASH_SIZE // 1024 // 1024}MB flash dump (bootloader + partition + app + NVS)
  *-app.bin        application partition only (burn to 0x10000)
  *-app.elf        ELF with debug symbols (for gdb / objdump)
  *-factory.bin    combined image: bootloader + partition + app (burn to 0x0)
  *-source.ino     firmware source snapshot
  *-platformio.ini PlatformIO config snapshot
  *-INFO.txt       this file

Restore commands:
  # Full restore (bootloader + partition + app, erases everything else):
  esptool.py --port /dev/cu.usbmodem* --chip esp32c3 write_flash 0x0 {out_dir.name}.bin

  # App-only restore (preserves bootloader and partition table):
  esptool.py --port /dev/cu.usbmodem* --chip esp32c3 write_flash 0x10000 {out_dir.name}-app.bin

  # Or use the safety-wrapped uploader (with USB check):
  ./scripts/firmware-upload.sh  # then manually upload the bin via esptool
"""
    (out_dir / f"{out_dir.name}-INFO.txt").write_text(info)


def main() -> int:
    ap = argparse.ArgumentParser(description="Backup ESP32-C3 firmware.")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                    help=f"Number of recent backups to keep (default {DEFAULT_KEEP})")
    ap.add_argument("--no-flash", action="store_true",
                    help="Skip flash dump (only backup PIO build artifacts)")
    args = ap.parse_args()

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = BACKUP_DIR / f"esp32-firmware-{ts}"
    out_dir.mkdir()

    print(f"=== firmware-backup: {out_dir.name} ===")

    # 1. Flash dump (optional)
    port = find_esp32_port()
    with_flash = not args.no_flash
    if with_flash:
        if port is None:
            print("[WARN] 没有 /dev/cu.usbmodem* — 跳过 flash dump（只备份编译产物）")
            with_flash = False
        else:
            print(f"[1/4] 读 ESP32 flash ({FLASH_SIZE // 1024 // 1024}MB)...")
            ok, log = dump_flash(out_dir / f"{out_dir.name}.bin", port)
            if ok:
                size = (out_dir / f"{out_dir.name}.bin").stat().st_size
                print(f"      ✓ {size} bytes")
            else:
                print(f"      ✗ flash dump 失败:")
                print(log[-500:])
                print("      （继续备份编译产物）")
                with_flash = False

    # 2. PIO build artifacts
    print(f"[2/4] 复制 PIO 编译产物...")
    copied = []
    if BUILD_DIR.exists():
        for src_name, dst_suffix in [
            ("firmware.bin",        "-app.bin"),
            ("firmware.elf",        "-app.elf"),
            ("firmware.factory.bin","-factory.bin"),
        ]:
            src = BUILD_DIR / src_name
            if src.exists():
                shutil.copy2(src, out_dir / f"{out_dir.name}{dst_suffix}")
                copied.append(src_name)
    print(f"      ✓ copied: {', '.join(copied) or '(none — 没编译过?)'}")

    # 3. Source snapshot
    print(f"[3/4] 复制源码 snapshot...")
    for src, dst_suffix in [
        (FIRMWARE_DIR / "esp32_c3_traffic_light.ino", "-source.ino"),
        (FIRMWARE_DIR / "platformio.ini",             "-platformio.ini"),
    ]:
        if src.exists():
            shutil.copy2(src, out_dir / f"{out_dir.name}{dst_suffix}")

    # 4. INFO.txt
    print(f"[4/4] 写 INFO.txt (git info + restore commands)...")
    ginfo = git_info()
    write_info(out_dir, ts, ginfo, port, with_flash)

    # Cleanup old backups (按目录修改时间排序，保留最新 N 份)
    print(f"\n=== cleanup: keep最近 {args.keep} 份 ===")
    all_backups = sorted(
        [p for p in BACKUP_DIR.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if len(all_backups) > args.keep:
        for old in all_backups[args.keep:]:
            print(f"  删除旧备份: {old.name}")
            shutil.rmtree(old)
    else:
        print(f"  当前 {len(all_backups)} 份，不超过 keep={args.keep}，不清理")

    print(f"\n✓ 备份完成: {out_dir}")
    print(f"  查看: ls -la {out_dir}")
    print(f"  恢复说明: cat {out_dir}/{out_dir.name}-INFO.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
