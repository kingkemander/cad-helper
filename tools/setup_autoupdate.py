#!/usr/bin/env python3
"""envcad 自动更新 —— 注册/卸载「每日定时拉取」。

它做两件事：
    1. 写更新配置到「安装根目录/.envcad-update.json」（仓库之外，不会被提交；
       proxy 可能含凭据，故权限设为 600）；
    2. 按平台注册每日定时任务，调用 tools/auto_update.py。

    macOS  → launchd LaunchAgent（~/Library/LaunchAgents/com.envcad.autoupdate.plist）
    Linux  → crontab（带 # envcad-autoupdate 标记，便于幂等替换）
    Windows→ 任务计划程序（schtasks，任务名 envcad-autoupdate）

不需要管理员权限；不改系统 Python；卸载会干净移除。

用法：
    python3 tools/setup_autoupdate.py install                 # 每天 10:30 拉取
    python3 tools/setup_autoupdate.py install --hour 3 --minute 0
    python3 tools/setup_autoupdate.py install --proxy http://127.0.0.1:8899
    python3 tools/setup_autoupdate.py install --extras doc
    python3 tools/setup_autoupdate.py install --dry-run       # 只预览，不落盘
    python3 tools/setup_autoupdate.py status
    python3 tools/setup_autoupdate.py uninstall

退出码：0 成功，1 失败/不支持的平台，2 参数错误
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
INSTALL_ROOT = REPO_DIR.parent
CONFIG_PATH = INSTALL_ROOT / ".envcad-update.json"
UPDATER = REPO_DIR / "tools" / "auto_update.py"

LABEL = "com.envcad.autoupdate"
CRON_MARK = "# envcad-autoupdate"
WIN_TASK = "envcad-autoupdate"

DEFAULT_REPO = "https://github.com/kingkemander/cad-helper.git"


def sh(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def os_name() -> str:
    p = sys.platform
    if p.startswith("darwin"):
        return "darwin"
    if p.startswith("win"):
        return "windows"
    if p.startswith("linux"):
        return "linux"
    return "unknown"


# ══════════════════════════════════════════════════════════
#  配置
# ══════════════════════════════════════════════════════════

def write_config(repo: str, branch: str, extras: str, proxy: str, dry: bool) -> None:
    cfg = {"repo": repo, "branch": branch, "extras": extras,
           "auto_install_deps": True}
    if proxy:
        cfg["proxy"] = proxy
    text = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    if dry:
        print(f"[dry-run] 将写入 {CONFIG_PATH}（权限 600）：")
        shown = dict(cfg)
        if "proxy" in shown:
            shown["proxy"] = "<已设置·不显示>"
        print(json.dumps(shown, ensure_ascii=False, indent=2))
        return
    CONFIG_PATH.write_text(text, encoding="utf-8")
    # proxy 可能含账号密码，收紧权限
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    print(f"✅ 配置已写入 {CONFIG_PATH}")


# ══════════════════════════════════════════════════════════
#  macOS — launchd
# ══════════════════════════════════════════════════════════

def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def plist_content(python: str, hour: int, minute: int, logs: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{xml_escape(LABEL)}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{xml_escape(python)}</string>
        <string>{xml_escape(str(UPDATER))}</string>
        <string>--quiet</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{xml_escape(str(REPO_DIR))}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{xml_escape(str(logs / 'launchd.out.log'))}</string>
    <key>StandardErrorPath</key>
    <string>{xml_escape(str(logs / 'launchd.err.log'))}</string>
</dict>
</plist>
"""


def macos_install(python: str, hour: int, minute: int, dry: bool) -> int:
    pl = plist_path()
    logs = INSTALL_ROOT / "logs"
    content = plist_content(python, hour, minute, logs)
    if dry:
        print(f"[dry-run] 将写入 {pl}")
        print(content)
        return 0

    logs.mkdir(parents=True, exist_ok=True)
    pl.parent.mkdir(parents=True, exist_ok=True)
    pl.write_text(content, encoding="utf-8")
    print(f"✅ LaunchAgent 已写入 {pl}")

    sh(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
    r = sh(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(pl)])
    if r.returncode != 0:
        r = sh(["launchctl", "load", "-w", str(pl)])
    if r.returncode == 0:
        print(f"✅ 已加载，每天 {hour:02d}:{minute:02d} 自动拉取")
        print(f"   手动触发一次： launchctl kickstart -k gui/{os.getuid()}/{LABEL}")
    else:
        print(f"⚠️ 加载失败：{(r.stderr or r.stdout).strip()[:300]}")
        print(f"   可手动加载： launchctl load -w {pl}")
        return 1
    return 0


def macos_uninstall(dry: bool) -> int:
    pl = plist_path()
    if dry:
        print(f"[dry-run] 将卸载 {pl}")
        return 0
    sh(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
    sh(["launchctl", "unload", "-w", str(pl)])
    if pl.exists():
        pl.unlink()
        print(f"✅ 已移除 {pl}")
    else:
        print("（未发现已注册的 LaunchAgent）")
    return 0


def macos_status() -> int:
    pl = plist_path()
    print(f"plist 文件 : {pl} {'（存在）' if pl.exists() else '（不存在）'}")
    r = sh(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            s = line.strip()
            if any(k in s for k in ("state =", "program =", "runs =", "last exit")):
                print(f"  {s}")
        print("  状态：已加载 ✅")
    else:
        print("  状态：未加载 ❌")
    return 0


# ══════════════════════════════════════════════════════════
#  Linux — crontab
# ══════════════════════════════════════════════════════════

def cron_line(python: str, hour: int, minute: int) -> str:
    logs = INSTALL_ROOT / "logs"
    return (f"{minute} {hour} * * * cd {REPO_DIR} && {python} {UPDATER} --quiet "
            f">> {logs}/cron.log 2>&1 {CRON_MARK}")


def cron_read() -> list[str]:
    r = sh(["crontab", "-l"])
    if r.returncode != 0:
        return []
    return r.stdout.splitlines()


def linux_install(python: str, hour: int, minute: int, dry: bool) -> int:
    line = cron_line(python, hour, minute)
    kept = [l for l in cron_read() if CRON_MARK not in l]
    new = kept + [line]
    if dry:
        print("[dry-run] 将写入 crontab（保留其他行）：")
        print("  " + line)
        return 0
    (INSTALL_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["crontab", "-"], input="\n".join(new) + "\n",
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"❌ crontab 写入失败：{(r.stderr or r.stdout).strip()[:300]}")
        return 1
    print(f"✅ crontab 已更新，每天 {hour:02d}:{minute:02d} 自动拉取")
    return 0


def linux_uninstall(dry: bool) -> int:
    kept = [l for l in cron_read() if CRON_MARK not in l]
    if dry:
        print("[dry-run] 将从 crontab 移除 envcad-autoupdate 行")
        return 0
    subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n",
                   capture_output=True, text=True)
    print("✅ 已从 crontab 移除")
    return 0


def linux_status() -> int:
    hits = [l for l in cron_read() if CRON_MARK in l]
    if hits:
        print("状态：已注册 ✅")
        for h in hits:
            print(f"  {h}")
    else:
        print("状态：未注册 ❌")
    return 0


# ══════════════════════════════════════════════════════════
#  Windows — 任务计划程序
# ══════════════════════════════════════════════════════════

def win_install(python: str, hour: int, minute: int, dry: bool) -> int:
    tr = f'"{python}" "{UPDATER}" --quiet'
    st = f"{hour:02d}:{minute:02d}"
    cmd = ["schtasks", "/Create", "/F", "/TN", WIN_TASK, "/SC", "DAILY",
           "/ST", st, "/TR", tr]
    if dry:
        print("[dry-run] 将执行：")
        print("  " + " ".join(cmd))
        return 0
    r = sh(cmd)
    if r.returncode != 0:
        print(f"❌ 创建计划任务失败：{(r.stderr or r.stdout).strip()[:300]}")
        return 1
    print(f"✅ 计划任务 {WIN_TASK} 已创建，每天 {st} 自动拉取")
    return 0


def win_uninstall(dry: bool) -> int:
    cmd = ["schtasks", "/Delete", "/F", "/TN", WIN_TASK]
    if dry:
        print("[dry-run] 将执行： " + " ".join(cmd))
        return 0
    r = sh(cmd)
    print("✅ 已删除计划任务" if r.returncode == 0
          else f"（未发现该任务或删除失败：{(r.stderr or r.stdout).strip()[:200]}）")
    return 0


def win_status() -> int:
    r = sh(["schtasks", "/Query", "/TN", WIN_TASK])
    print("状态：" + ("已注册 ✅" if r.returncode == 0 else "未注册 ❌"))
    if r.returncode == 0:
        print("  " + (r.stdout or "").strip()[:300])
    return 0


# ══════════════════════════════════════════════════════════
#  main
# ══════════════════════════════════════════════════════════

def show_log() -> None:
    log = INSTALL_ROOT / "logs" / "auto_update.log"
    print()
    print(f"最近更新日志 ({log}):")
    if not log.is_file():
        print("  （还没有日志——定时任务尚未运行过）")
        return
    try:
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
        for line in tail:
            print(f"  {line}")
    except OSError:
        print("  （日志读取失败）")


def main() -> int:
    ap = argparse.ArgumentParser(description="注册/卸载 envcad 每日自动更新")
    ap.add_argument("action", choices=["install", "uninstall", "status"])
    ap.add_argument("--hour", type=int, default=10, help="每天几点（0-23，默认 10）")
    ap.add_argument("--minute", type=int, default=30, help="几分（0-59，默认 30）")
    ap.add_argument("--python", default=sys.executable, help="用哪个 Python 跑更新器")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="上游仓库地址")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--extras", default="all" if os.name == "nt" else "doc",
                    help="依赖组：doc / all / doc,step")
    ap.add_argument("--proxy", default="", help="git 代理（本机需翻墙时用）")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不改动系统")
    args = ap.parse_args()

    if not 0 <= args.hour <= 23 or not 0 <= args.minute <= 59:
        print("❌ --hour 需 0-23，--minute 需 0-59")
        return 2

    plat = os_name()
    dry = args.dry_run

    print(f"平台      : {plat} ({platform.platform()})")
    print(f"仓库目录  : {REPO_DIR}")
    print(f"更新脚本  : {UPDATER} {'✅' if UPDATER.is_file() else '❌ 缺失'}")
    print(f"Python    : {args.python}")
    if args.action == "install":
        print(f"计划时间  : 每天 {args.hour:02d}:{args.minute:02d}")
    print()

    if args.action == "install":
        if not UPDATER.is_file():
            print("❌ 找不到 tools/auto_update.py，无法注册")
            return 1
        write_config(args.repo, args.branch, args.extras, args.proxy, dry)
        print()
        if plat == "darwin":
            code = macos_install(args.python, args.hour, args.minute, dry)
        elif plat == "linux":
            code = linux_install(args.python, args.hour, args.minute, dry)
        elif plat == "windows":
            code = win_install(args.python, args.hour, args.minute, dry)
        else:
            print(f"❌ 不支持的平台：{plat}，请手动把 auto_update.py 加进计划任务")
            return 1
        if code == 0:
            print()
            if not dry:
                print("提示：定时任务只在「仓库有更新时」才改动，"
                      "若本地有未提交改动会自动跳过，不会覆盖你的修改。")
                show_log()
        return code

    if args.action == "uninstall":
        if plat == "darwin":
            return macos_uninstall(dry)
        if plat == "linux":
            return linux_uninstall(dry)
        if plat == "windows":
            return win_uninstall(dry)
        print(f"❌ 不支持的平台：{plat}")
        return 1

    # status
    if plat == "darwin":
        code = macos_status()
    elif plat == "linux":
        code = linux_status()
    elif plat == "windows":
        code = win_status()
    else:
        print(f"❌ 不支持的平台：{plat}")
        code = 1
    print(f"配置文件  : {CONFIG_PATH} {'（存在）' if CONFIG_PATH.is_file() else '（不存在）'}")
    if CONFIG_PATH.is_file():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if "proxy" in cfg and cfg["proxy"]:
                cfg["proxy"] = "<已设置·不显示>"
            print(f"  内容：{json.dumps(cfg, ensure_ascii=False)}")
        except (OSError, ValueError):
            print("  内容：读取失败")
    show_log()
    return code


if __name__ == "__main__":
    sys.exit(main())
