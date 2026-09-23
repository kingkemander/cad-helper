#!/usr/bin/env python3
"""CAD 助手（AutoCAD 助手）安装引导器。

做四件事，顺序固定：
  1. 从 GitHub 获取 envcad 源码（默认 git clone，保留提交历史以便一键升级）；
  2. 在安装目录建独立 venv、装依赖（不碰系统 Python）；
  3. 生成 envcad 命令并实测出图，校验通过才算成功；
  4. 在当前工作区注册「AutoCAD 助手」智能体，并写 current.json 作为绑定指针。

本脚本会真实改动用户电脑：下载、建目录、建 venv、装依赖、写一个命令到 ~/.local/bin。
这些都在安装目录与当前工作区内完成。改 PATH、注册每日定时任务都必须先拿到用户同意
（--add-to-path / --autoupdate 显式开启，默认不做）。

同一个命令重复运行 = 升级：源码目录用 git pull --ff-only 原地更新，保留本地配置。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

# ---------------------------------------------------------------- 固定事实

PLUGIN_VERSION = "1.5.14"
PLUGIN_ID = "cad-assistant"
AGENT_NAME = "AutoCAD助手"
AGENT_MARKER = "managed-by-spaceagents-cad-assistant"

REPO_WEB = "https://github.com/kingkemander/cad-helper"
REPO_URL = REPO_WEB + ".git"
REPO_ZIP_URL = REPO_WEB + "/archive/refs/heads/main.zip"

DEFAULT_DIR_NAME = "凹凸CAD助手1.5"
EXPECTED_DOMAINS = 52
MIN_PYTHON = (3, 10)
MAX_PYTHON = (3, 14)

# macOS / Linux 绝不能用 [all]：其中 pywin32 只有 Windows 发行版，必然报
# "No matching distribution found for pywin32"。已实测复现。
EXTRAS_POSIX = "doc"
EXTRAS_WINDOWS = "all"


class Fail(Exception):
    """可预期的失败：打印人话后退出，不抛栈。"""


def say(message: str = "") -> None:
    print(message, flush=True)


def step(index: int, title: str) -> None:
    say()
    say(f"[{index}/6] {title}")


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 1800,
        quiet: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, timeout=timeout,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace",
    )
    if not quiet and result.returncode != 0:
        say(result.stdout.strip()[-3000:])
    return result


# ---------------------------------------------------------------- 1. 前置检查


def pick_python() -> Path:
    """挑一个 >=3.10 的解释器。找不到就报错，绝不代为安装系统 Python。"""
    candidates: list[str] = []
    for minor in range(MAX_PYTHON[1], MIN_PYTHON[1] - 1, -1):
        candidates.append(f"python3.{minor}")
    candidates += ["python3", "python"]

    seen: set[str] = set()
    for name in candidates:
        path = shutil.which(name)
        if not path or path in seen:
            continue
        seen.add(path)
        probe = subprocess.run(
            [path, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        if probe.returncode != 0:
            continue
        major, minor = (int(part) for part in probe.stdout.strip().split("."))
        if (major, minor) >= MIN_PYTHON:
            say(f"    Python 解释器：{path}（{major}.{minor}）")
            return Path(path)

    raise Fail(
        f"没有找到 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 或更高版本。\n"
        "  请先自行安装 Python 3.10+（macOS：brew install python@3.12；"
        "Windows：官网下载并勾选 Add to PATH），然后重新运行。\n"
        "  本引导器不会替你安装系统级 Python。"
    )


def preflight(install_dir: Path) -> Path:
    step(1, "前置检查")
    say(f"    平台：{platform.system()} {platform.machine()}")

    python = pick_python()

    probe = install_dir
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    free_gb = usage.free / (1024 ** 3)
    say(f"    磁盘余量：{free_gb:.1f} GB（{probe}）")
    # 实测（macOS arm64 / Python 3.14 / [doc]，APFS）：app 6.1M + .venv 108M ≈ 114M。
    # 取 1GB 门槛 = 实际占用的约 10 倍，留给 pip 下载与构建 wheel 的临时峰值空间。
    # 注意：exFAT 上 du 会报 ~2.7G，那是 128KB 簇把大量小文件放大 20 倍的假象，
    # 不是真实数据量——不要拿它当依据抬高门槛（本仓库曾据此误改过一次）。
    if free_gb < 1:
        raise Fail(f"磁盘余量不足 1 GB（当前 {free_gb:.1f} GB，实测需要约 114 MB），请先清理后再装。")

    return python


# ---------------------------------------------------------------- 2. 获取源码


def choose_install_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if platform.system() == "Windows":
        return Path.home() / DEFAULT_DIR_NAME
    return Path.home() / DEFAULT_DIR_NAME


def fetch_source(app: Path, use_zip: bool) -> str:
    """返回 'git' 或 'zip'。已有目录则原地升级，不重新下载。"""
    if (app / "pyproject.toml").is_file() and (app / ".git").is_dir():
        say("    检测到已有源码目录，原地升级（git pull --ff-only）")
        result = run(["git", "-C", str(app), "pull", "--ff-only"], quiet=True)
        if result.returncode == 0:
            say("    " + (result.stdout.strip().splitlines() or ["已是最新版本"])[-1])
            return "git"
        say("    ⚠ git pull 未成功，继续使用当前版本：")
        say("    " + result.stdout.strip()[-500:])
        return "git"

    app.parent.mkdir(parents=True, exist_ok=True)

    if not use_zip and shutil.which("git"):
        say(f"    git clone {REPO_URL}")
        result = run(["git", "clone", REPO_URL, str(app)], quiet=True)
        if result.returncode == 0:
            return "git"
        say("    ⚠ git clone 失败，改用 zip 降级方案")
        say("    " + result.stdout.strip()[-500:])
    elif not shutil.which("git"):
        say("    本机没有 git，改用 zip 降级方案")

    if app.exists():
        backup = app.with_name(app.name + ".bak." + time.strftime("%Y%m%d-%H%M%S"))
        app.rename(backup)
        say(f"    已备份旧目录 -> {backup.name}")

    archive = app.parent / "cad-helper-main.zip"
    say(f"    下载 {REPO_ZIP_URL}")
    download_zip(REPO_ZIP_URL, archive)
    extract_zip(archive, app.parent)

    extracted = app.parent / "cad-helper-main"
    if not extracted.is_dir():
        raise Fail("解压后没找到 cad-helper-main 目录，下载可能不完整。")
    if app.exists():
        shutil.rmtree(app)
    extracted.rename(app)
    archive.unlink(missing_ok=True)
    say("    ⚠ 本次为 zip 降级安装：目录不是 git 检出，无法自动更新")
    return "zip"


def download_zip(url: str, target: Path) -> None:
    import urllib.error
    import urllib.request

    last: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "envcad-bootstrap/1"})
            with urllib.request.urlopen(request, timeout=600) as response:
                payload = response.read()
            if len(payload) < 100_000:
                raise Fail("下载内容过小，可能是错误页而非源码包。")
            target.write_bytes(payload)
            return
        except Exception as exc:  # noqa: BLE001 - 网络异常种类多，统一重试
            last = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise Fail(f"源码下载失败（已重试 3 次）：{last}")


def extract_zip(archive: Path, destination: Path) -> None:
    """macOS 必须 ditto：Info-ZIP 的 unzip 不认 UTF-8 文件名标记，
    会把 examples/01_填埋场剖面图.dxf 这类中文名写坏。Linux/Windows 用 zipfile。"""
    if platform.system() == "Darwin" and shutil.which("ditto"):
        if run(["ditto", "-x", "-k", str(archive), str(destination)]).returncode == 0:
            return
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(destination)


# ---------------------------------------------------------------- 3. 环境与依赖


def make_venv(python: Path, venv_dir: Path) -> Path:
    bin_dir = "Scripts" if platform.system() == "Windows" else "bin"
    if not (venv_dir / bin_dir / "python").exists() and not (venv_dir / bin_dir / "python.exe").exists():
        say(f"    建独立环境：{venv_dir}")
        if run([str(python), "-m", "venv", str(venv_dir)]).returncode != 0:
            raise Fail("创建虚拟环境失败。请确认该 Python 自带 venv 模块（Debian/Ubuntu 需装 python3-venv）。")
    exe = "python.exe" if platform.system() == "Windows" else "python"
    return venv_dir / bin_dir / exe


def install_deps(venv_python: Path, app: Path, extras: str) -> None:
    say(f"    安装依赖：pip install -e \".[{extras}]\"（首次约 100-200 MB，请稍候）")
    run([str(venv_python), "-m", "pip", "install", "-q", "-U", "pip"], timeout=900)
    result = run(
        [str(venv_python), "-m", "pip", "install", "-e", f".[{extras}]"],
        cwd=app, timeout=1800,
    )
    if result.returncode != 0:
        raise Fail(
            f"依赖安装失败（extras=[{extras}]）。\n"
            "  macOS / Linux 请勿使用 [all]：其中 pywin32 仅 Windows 有发行版。\n"
            "  需要 STEP 3D 就单独装：pip install -e \".[doc,step]\"。"
        )


def app_executable(venv_dir: Path, name: str) -> Path:
    if platform.system() == "Windows":
        return venv_dir / "Scripts" / f"{name}.exe"
    return venv_dir / "bin" / name


# ---------------------------------------------------------------- 4. 验证出图


def verify(app: Path, venv_dir: Path, out_dir: Path) -> None:
    envcad = app_executable(venv_dir, "envcad")
    if not envcad.exists():
        raise Fail(f"没找到 envcad 可执行文件：{envcad}")

    listing = run([str(envcad), "list"], cwd=app, timeout=300, quiet=True)
    if listing.returncode != 0:
        raise Fail("envcad list 执行失败：\n" + listing.stdout.strip()[-2000:])
    domains = re.findall(r"^\s*\[([A-Za-z_][\w]*)\]\s*\(\d+\s*个函数\)", listing.stdout, re.M)
    say(f"    envcad list 正常，识别到 {len(domains)} 个领域模块（基准 {EXPECTED_DOMAINS}）")
    if len(domains) < EXPECTED_DOMAINS:
        raise Fail(
            f"只识别到 {len(domains)} 个领域模块，低于基准 {EXPECTED_DOMAINS}，"
            "源码或依赖可能不完整。\n" + listing.stdout.strip()[-2000:]
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    result = run([str(envcad), "test", "t2", "--out", str(out_dir)], cwd=app, timeout=600, quiet=True)
    produced = sorted(out_dir.glob("*.dxf"))
    if result.returncode != 0 or not produced:
        raise Fail("出图验证失败：\n" + result.stdout.strip()[-2000:])

    check = run([str(app_executable(venv_dir, "python")), "-c",
                 "import ezdxf,glob,sys,os;"
                 "fs=sorted(glob.glob(os.path.join(sys.argv[1],'*.dxf')));"
                 "[print(os.path.basename(f), ezdxf.readfile(f).dxfversion,"
                 " len(ezdxf.readfile(f).modelspace())) for f in fs]",
                 str(out_dir)], cwd=app, timeout=300, quiet=True)
    if check.returncode != 0:
        raise Fail("生成的 DXF 无法用 ezdxf 读回，文件可能是半截的：\n" + check.stdout.strip()[-2000:])
    for line in check.stdout.strip().splitlines():
        say("    " + line)

    newest = max(produced, key=lambda p: p.stat().st_mtime)
    say(f"    ✓ 验证图纸：{newest}（{newest.stat().st_size / 1024:.1f} KB）")


# ---------------------------------------------------------------- 5. 注册智能体


def write_agent(workspace: Path, app: Path) -> Path:
    template = app / "agent-template.md"
    if not template.is_file():
        raise Fail(f"源码里缺少智能体模板：{template}")

    content = template.read_text(encoding="utf-8")
    agents = workspace / ".opencode" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    target = agents / f"{AGENT_NAME}.md"

    if target.exists() and AGENT_MARKER not in target.read_text(encoding="utf-8", errors="ignore"):
        target = agents / f"{AGENT_NAME}（SpaceAgents）.md"
        say(f"    ⚠ 已有同名智能体且非本插件管理，改写为：{target.name}")

    target.write_text(content, encoding="utf-8")
    return target


def write_state(workspace: Path, app: Path, source: str, extras: str) -> Path:
    venv_dir = app.parent / ".venv"
    state = {
        "plugin_version": PLUGIN_VERSION,
        "agent_name": AGENT_NAME,
        "install_dir": str(app.parent),
        "app": str(app),
        "python": str(app_executable(venv_dir, "python")),
        "envcad": str(app_executable(venv_dir, "envcad")),
        "source": source,
        "extras": extras,
        "auto_update": False,
        "installed_at": int(time.time()),
        "last_update_check": None,
    }
    root = workspace / ".spaceagents" / "plugins" / PLUGIN_ID
    root.mkdir(parents=True, exist_ok=True)
    current = root / "current.json"

    if current.is_file():
        try:
            previous = json.loads(current.read_text(encoding="utf-8"))
            state["installed_at"] = previous.get("installed_at", state["installed_at"])
            state["auto_update"] = previous.get("auto_update", False)
        except (OSError, ValueError):
            pass

    current.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return current


def write_launcher(install_dir: Path, venv_dir: Path) -> Path | None:
    """在 ~/.local/bin 放一个 shim。只创建文件，不改 PATH。"""
    if platform.system() == "Windows":
        cmd = install_dir / "envcad.cmd"
        cmd.write_text(f'@echo off\r\n"{app_executable(venv_dir, "envcad")}" %*\r\n', encoding="ascii")
        return cmd

    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "envcad"
    shim.write_text(f'#!/bin/sh\nexec "{app_executable(venv_dir, "envcad")}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)

    # 只创建文件，绝不擅自改 PATH；但要说清用户在终端里能不能直接用。
    if str(bin_dir) not in os.environ.get("PATH", "").split(os.pathsep):
        say(f"    ⚠ {bin_dir} 不在 PATH 里，终端直接敲 envcad 会找不到")
        say(f"      要用的话自行在 shell 配置里加：export PATH=\"{bin_dir}:$PATH\"")
        say(f"      或直接用完整路径：{app_executable(venv_dir, 'envcad')}")
        shim_usable = False
    else:
        shim_usable = True
    return shim if shim_usable else None


def register_autoupdate(app: Path, venv_python: Path, hour: int, minute: int, proxy: str | None) -> bool:
    script = app / "tools" / "setup_autoupdate.py"
    if not script.is_file():
        return False
    cmd = [str(venv_python), str(script), "install", "--hour", str(hour), "--minute", str(minute)]
    if proxy:
        cmd += ["--proxy", proxy]
    return run(cmd, cwd=app, timeout=300, quiet=True).returncode == 0


# ---------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(
        description="安装 CAD 助手（envcad）并在当前工作区注册「AutoCAD 助手」智能体。")
    parser.add_argument("--workspace", default=".", help="目标工作区根目录（智能体写到这里）")
    parser.add_argument("--install-dir", default=None, help=f"安装目录，默认 ~/{DEFAULT_DIR_NAME}")
    parser.add_argument("--extras", default=None, help="依赖组，默认 macOS/Linux=doc，Windows=all")
    parser.add_argument("--zip", action="store_true", help="强制用 zip 下载（无 git 时自动降级）")
    parser.add_argument("--autoupdate", action="store_true", help="注册每日自动更新（需用户已同意）")
    parser.add_argument("--autoupdate-hour", type=int, default=10)
    parser.add_argument("--autoupdate-minute", type=int, default=30)
    parser.add_argument("--proxy", default=None, help="本机访问 GitHub 所用代理，仅写入本机配置")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不做任何改动")
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise Fail(f"工作区不存在：{workspace}")

    install_dir = choose_install_dir(args.install_dir)
    app = install_dir / "app"
    venv_dir = install_dir / ".venv"
    extras = args.extras or (
        EXTRAS_WINDOWS if platform.system() == "Windows" else EXTRAS_POSIX
    )

    say("CAD 助手 安装引导")
    say(f"  工作区   : {workspace}")
    say(f"  安装目录 : {install_dir}")
    say(f"  源码来源 : {REPO_WEB}")
    say(f"  依赖组   : [{extras}]")

    if args.dry_run:
        say()
        say("--dry-run：只预览，不做任何改动。将执行：")
        say("  1. 检查 Python >= 3.10、磁盘余量、网络")
        say(f"  2. git clone / zip 下载源码 -> {app}")
        say(f"  3. 建 venv -> {venv_dir}，pip install -e \".[{extras}]\"")
        say(f"  4. 生成 envcad 命令 + 实测出图到 {install_dir}/out/verify/")
        say(f"  5. 写智能体 -> {workspace}/.opencode/agents/{AGENT_NAME}.md")
        say(f"  6. 写绑定指针 -> {workspace}/.spaceagents/plugins/{PLUGIN_ID}/current.json")
        if args.autoupdate:
            say("  7. 注册每日自动更新")
        return 0

    python = preflight(install_dir)

    step(2, "获取源码")
    source = fetch_source(app, use_zip=args.zip)
    if not (app / "pyproject.toml").is_file():
        raise Fail(f"源码目录不完整：{app} 下没有 pyproject.toml")

    step(3, "建独立环境并安装依赖")
    venv_python = make_venv(python, venv_dir)
    install_deps(venv_python, app, extras)

    step(4, "验证出图能力")
    verify(app, venv_dir, install_dir / "out" / "verify")

    step(5, "注册智能体")
    agent_file = write_agent(workspace, app)
    say(f"    智能体文件：{agent_file}")
    shim = write_launcher(install_dir, venv_dir)
    if shim:
        say(f"    命令：{shim}")
    state_file = write_state(workspace, app, source, extras)
    say(f"    绑定指针：{state_file}")

    step(6, "自动更新（可选）")
    if args.autoupdate:
        if source != "git":
            say("    ⚠ 当前为 zip 降级安装，不是 git 检出，无法自动更新")
        elif register_autoupdate(app, venv_python, args.autoupdate_hour, args.autoupdate_minute, args.proxy):
            say(f"    ✓ 已注册，每天 {args.autoupdate_hour:02d}:{args.autoupdate_minute:02d} 检查更新")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            state["auto_update"] = True
            state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            say("    ⚠ 注册失败，可按安装目录下 tools/setup_autoupdate.py 手动重试")
    else:
        say("    未开启（需要时在对话里说“开启自动更新”）")

    say()
    say("✔ AutoCAD 助手 安装成功")
    say(f"  插件版本 : {PLUGIN_VERSION}")
    say(f"  安装目录 : {install_dir}")
    say(f"  运行环境 : {venv_dir}（独立，不影响系统 Python）")
    say(f"  安装来源 : {'git clone（可自动更新）' if source == 'git' else 'zip 降级（不可自动更新）'}")
    say(f"  智能体   : {agent_file}")
    say(f"  验证图纸 : {install_dir}/out/verify/")
    say(f"  平台说明 : {platform.system()} —— "
        + ("支持 COM 推送 AutoCAD" if platform.system() == "Windows"
           else "不支持 COM 推送 AutoCAD，交付 DXF/DWG 文件"))
    say()
    say("请新建会话，在智能体下拉列表选择「AutoCAD 助手」开始使用。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Fail as failure:
        say()
        say("安装未完成：")
        say(str(failure))
        raise SystemExit(1)
    except KeyboardInterrupt:
        say()
        say("已中断。重新运行同一命令会从断点继续（源码与 venv 会复用）。")
        raise SystemExit(130)
