#!/usr/bin/env python3
"""envcad 自动更新器 —— 从 GitHub 拉取最新版本。

为什么放在仓库里而不是塞进 Skill：
    脚本本身会随 `git pull` 一起更新，所以**更新逻辑自己也能被更新**。
    如果写死在 Skill 或系统任务里，改一次更新逻辑就得让所有用户重装。

行为（默认安全优先）：
    1. 检查本地是否有未提交改动 —— 有则**跳过本次更新**，绝不覆盖用户改动；
    2. `git fetch` 远端，比较 HEAD 与 origin/<branch>；
    3. 落后则 `git merge --ff-only`（只允许快进，不产生合并提交）；
    4. 若 pyproject.toml / requirements.txt 有变化，自动重装依赖；
    5. 全程写日志，日志文件超限自动截断。

绝不做的事：不 force push、不删除用户文件、不自动提交、不动系统 Python。

用法：
    python3 tools/auto_update.py                 # 拉取更新（默认）
    python3 tools/auto_update.py --check         # 只检查，不改动任何东西
    python3 tools/auto_update.py --json          # 机器可读输出（给 Agent 用）
    python3 tools/auto_update.py --reset         # 丢弃本地改动，强制对齐远端
    python3 tools/auto_update.py --quiet         # 静默（定时任务用）

退出码：0 成功/已最新/无需动作，1 网络或 git 失败，3 不是 git 检出，4 有本地改动被跳过
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# tools/auto_update.py → parents[0]=tools, parents[1]=仓库根
REPO_DIR = Path(__file__).resolve().parents[1]
INSTALL_ROOT = REPO_DIR.parent          # 形如 .../凹凸CAD助手1.5
CONFIG_NAME = ".envcad-update.json"
STATE_NAME = ".envcad-deps-state.json"
LOG_NAME = "auto_update.log"

DEFAULT_REPO = "https://github.com/kingkemander/cad-helper.git"
DEFAULT_BRANCH = "main"
DEP_FILES = ("pyproject.toml", "requirements.txt")

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_NOT_GIT = 3
EXIT_DIRTY = 4


# ══════════════════════════════════════════════════════════
#  配置与日志
# ══════════════════════════════════════════════════════════

def load_config() -> dict:
    """默认配置 ← 安装根目录的配置文件覆盖。"""
    cfg = {
        "repo": DEFAULT_REPO,
        "branch": DEFAULT_BRANCH,
        # macOS/Linux 不能用 [all]（含仅 Windows 的 pywin32）
        "extras": "all" if os.name == "nt" else "doc",
        "proxy": os.environ.get("ENVCAD_UPDATE_PROXY", "") or "",
        "auto_install_deps": True,
        "max_log_kb": 512,
        "version_cmd": "",
    }
    for cand in (INSTALL_ROOT / CONFIG_NAME, REPO_DIR / CONFIG_NAME):
        if cand.is_file():
            try:
                cfg.update(json.loads(cand.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
            break
    return cfg


class Logger:
    def __init__(self, quiet: bool, max_kb: int):
        self.quiet = quiet
        self.lines: list[str] = []
        self.path = INSTALL_ROOT / "logs" / LOG_NAME
        self.max_kb = max_kb
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.path = None  # 允许写入失败时降级为仅控制台
        self._rotate()

    def _rotate(self) -> None:
        """日志超过上限时截断，只保留尾部。"""
        if self.path is None:
            return
        try:
            if self.path.is_file() and self.path.stat().st_size > self.max_kb * 1024:
                tail = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
                keep = tail[-(self.max_kb * 1024 // 120):]
                self.path.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except OSError:
            pass

    def __call__(self, msg: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {msg}"
        self.lines.append(line)
        if not self.quiet:
            print(line, flush=True)
        # 逐行立刻落盘，而不是攒到最后再写：重装依赖等步骤可能耗时数十秒，
        # 期间若进程被中断（机器睡眠/关机、手动卸载任务、强杀），
        # 攒着的日志会整段丢失，用户就再也看不到「发生过什么」。
        if self.path is None:
            return
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            self.path = None

    def flush(self) -> None:
        """日志已逐行写入，此处只做容量轮转。"""
        self._rotate()


# ══════════════════════════════════════════════════════════
#  git 与辅助
# ══════════════════════════════════════════════════════════

def run(cmd: list[str], timeout: int = 240) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, cwd=str(REPO_DIR))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def git(args: list[str], cfg: dict, timeout: int = 240) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(REPO_DIR)]
    proxy = (cfg.get("proxy") or "").strip()
    if proxy:
        # proxy 只从本机配置文件读取，不写进任何被提交的文件
        cmd += ["-c", f"http.proxy={proxy}", "-c", "http.version=HTTP/1.1"]
    cmd += args
    return run(cmd, timeout=timeout)


def git_out(args: list[str], cfg: dict) -> str:
    r = git(args, cfg, timeout=60)
    return r.stdout.strip() if r.returncode == 0 else ""


def hash_deps() -> str:
    h = hashlib.sha256()
    for name in DEP_FILES:
        p = REPO_DIR / name
        if p.is_file():
            h.update(name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def read_version() -> str:
    """从 pyproject.toml 读 version。tomllib 需 3.11+，故保留正则回退。"""
    p = REPO_DIR / "pyproject.toml"
    if not p.is_file():
        return "?"
    text = p.read_text(encoding="utf-8", errors="replace")
    try:
        import tomllib  # type: ignore
        return str(tomllib.loads(text).get("project", {}).get("version", "?"))
    except Exception:
        m = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', text, re.M)
        return m.group(1) if m else "?"


def pip_error_tail(text: str, lines: int = 4) -> str:
    """从 pip 输出里挑出真正有用的报错行。

    pip 结尾会刷一堆 [notice]（新版本提示、升级建议），
    直接取末尾几行会把真正的 ERROR 原因挤出可视范围，
    用户和排查者都看不到为什么失败。
    """
    keep = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("[notice]"):
            continue
        if "pip install --upgrade pip" in s:
            continue
        keep.append(s)
    return " | ".join(keep[-lines:])[:600]


def reinstall_deps(cfg: dict, log: Logger) -> tuple[bool, str]:
    extras = (cfg.get("extras") or "").strip()
    spec = f".[{extras}]" if extras else "."
    cmd = [sys.executable, "-m", "pip", "install", "-e", spec]
    log(f"重装依赖：pip install -e \"{spec}\"")
    r = run(cmd, timeout=1800)
    if r.returncode != 0:
        return False, pip_error_tail(r.stderr or r.stdout)
    return True, ""


def read_state() -> dict:
    """读取安装状态（记录依赖是跟哪个提交配套的）。"""
    p = INSTALL_ROOT / STATE_NAME
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(commit: str) -> None:
    """记录「当前 venv 的依赖与哪个提交配套」。写在仓库之外，不进版本库。"""
    p = INSTALL_ROOT / STATE_NAME
    try:
        p.write_text(
            json.dumps({"deps_synced_commit": commit}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    except OSError:
        pass


def sync_deps(head: str, cfg: dict, log: Logger, deps_changed: bool) -> tuple[bool, bool, str]:
    """保证 venv 里的依赖与 head 这份代码配套。返回 (成功, 是否重装, 错误)。

    两种情况都要装：
      1. 本次更新动了依赖清单（deps_changed）；
      2. 代码已经到 head，但上次重装依赖没跑完——记录里的提交 ≠ head。
    第 2 种是真实场景：笔记本在重装依赖的过程中睡眠/关机/被中断，
    此时代码已是新版、依赖还是旧的；若不补装，之后每次运行都只会看到
    「已是最新」而永远不会重装，环境就永久不一致。

    首次运行（没有状态记录）不算第 2 种：安装时依赖已由安装器装好，
    这里只登记当前提交，不重复安装——否则每天第一次检查都会白跑一次 pip。
    """
    if not cfg.get("auto_install_deps", True):
        return True, False, ""
    recorded = read_state().get("deps_synced_commit")
    if not deps_changed:
        if recorded is None:
            write_state(head)
            return True, False, ""
        if recorded == head:
            return True, False, ""
        log(f"依赖未与 {head[:8]} 配套（上次重装可能被中断），补装")
    ok, err = reinstall_deps(cfg, log)
    if not ok:
        return False, True, err
    write_state(head)
    return True, True, ""


# ══════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(description="envcad 自动更新器")
    ap.add_argument("--check", action="store_true", help="只检查，不做任何改动")
    ap.add_argument("--reset", action="store_true", help="丢弃本地改动，强制对齐远端")
    ap.add_argument("--quiet", action="store_true", help="静默模式（定时任务用）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--proxy", default=None, help="覆盖配置文件里的代理")
    ap.add_argument("--extras", default=None, help="依赖组，如 doc / all / doc,step")
    ap.add_argument("--branch", default=None, help="分支，默认 main")
    args = ap.parse_args()

    cfg = load_config()
    for key in ("proxy", "extras", "branch"):
        val = getattr(args, key)
        if val is not None:
            cfg[key] = val

    log = Logger(quiet=args.quiet or args.json, max_kb=int(cfg.get("max_log_kb", 512)))
    branch = cfg["branch"]
    result: dict = {
        "ok": True, "action": "none", "dirty": False, "untracked_count": 0,
        "before": read_version(), "after": read_version(),
        "commits_behind": 0, "deps_reinstalled": False, "message": "",
    }

    # ── 必须是 git 检出 ──────────────────────────────────
    if not (REPO_DIR / ".git").is_dir():
        msg = f"{REPO_DIR} 不是 git 检出，无法自动更新（安装时应使用 git clone）"
        log(msg)
        result.update(ok=False, message=msg)
        return finish(result, args, log, EXIT_NOT_GIT)

    local_before = git_out(["rev-parse", "HEAD"], cfg)

    # ── 本地改动 ────────────────────────────────────────
    # 只把「已跟踪文件的改动」视为需要拦截：只有它会与快进拉取冲突。
    # 未跟踪文件（porcelain 里 ?? 开头，例如用户自己导出的图纸、日志、草稿）
    # 不影响 --ff-only 快进，也绝不能被删除——否则既会丢用户数据，
    # 又会让仓库永久卡在 dirty 状态、此后再也更新不了。
    porcelain = git_out(["status", "--porcelain"], cfg)
    lines = [ln for ln in porcelain.splitlines() if ln.strip()]
    tracked_changes = [ln for ln in lines if not ln.startswith("??")]
    untracked = [ln for ln in lines if ln.startswith("??")]
    dirty = bool(tracked_changes)
    result["dirty"] = dirty
    result["untracked_count"] = len(untracked)

    # ── fetch ───────────────────────────────────────────
    rf = git(["fetch", "--quiet", "origin", branch], cfg)
    if rf.returncode != 0:
        msg = f"fetch 失败：{(rf.stderr or rf.stdout).strip().splitlines()[-1][:300]}"
        log(msg)
        result.update(ok=False, action="fetch_failed", message=msg)
        return finish(result, args, log, EXIT_FAIL)

    remote = git_out(["rev-parse", f"origin/{branch}"], cfg)
    if not remote:
        msg = f"取不到 origin/{branch}"
        log(msg)
        result.update(ok=False, action="no_remote_ref", message=msg)
        return finish(result, args, log, EXIT_FAIL)

    if local_before == remote:
        log(f"已是最新（{read_version()}，{local_before[:8]}）")
        result["message"] = "已是最新"
        if not args.check:
            # 代码已最新，仍要确认依赖配套：上次重装若被中断，这里补上
            ok, installed, err = sync_deps(local_before, cfg, log, False)
            result["deps_reinstalled"] = installed
            if not ok:
                log(f"依赖补装失败：{err}")
                result.update(ok=False, message=f"依赖补装失败：{err}")
                return finish(result, args, log, EXIT_FAIL)
        return finish(result, args, log, EXIT_OK)

    behind = git_out(["rev-list", "--count", f"HEAD..origin/{branch}"], cfg) or "?"
    result["commits_behind"] = int(behind) if behind.isdigit() else 0

    if args.check:
        msg = f"可更新：{local_before[:8]} → {remote[:8]}，落后 {behind} 个提交"
        log(msg)
        result.update(action="available", message=msg)
        return finish(result, args, log, EXIT_OK)

    # ── 有本地改动：默认跳过，绝不覆盖 ───────────────────
    if dirty and not args.reset:
        extra = f"（另有 {len(untracked)} 个未跟踪文件，不影响更新，已保留）" if untracked else ""
        msg = (f"检测到 {len(tracked_changes)} 个已跟踪文件有未提交改动，跳过本次更新以免覆盖"
               f"（落后 {behind} 个提交）{extra}。如确认丢弃这些改动可运行 --reset")
        log(msg)
        result.update(action="skipped_dirty", message=msg)
        return finish(result, args, log, EXIT_DIRTY)

    # ── 更新 ────────────────────────────────────────────
    deps_before = hash_deps()
    if args.reset:
        r = git(["reset", "--hard", f"origin/{branch}"], cfg)
        action = "reset"
    else:
        r = git(["merge", "--ff-only", f"origin/{branch}"], cfg)
        action = "pulled"

    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1][:300]
        log(f"更新失败：{tail}")
        result.update(ok=False, action="failed", message=tail)
        return finish(result, args, log, EXIT_FAIL)

    local_after = git_out(["rev-parse", "HEAD"], cfg)
    result["after"] = read_version()
    result["action"] = action
    log(f"更新完成：{local_before[:8]} → {local_after[:8]}（{result['before']} → {result['after']}）")

    # ── 依赖与代码保持一致（含被中断后的补装）───────────
    deps_changed = hash_deps() != deps_before
    ok, installed, err = sync_deps(local_after, cfg, log, deps_changed)
    result["deps_reinstalled"] = installed
    if not ok:
        result.update(ok=False, message=f"依赖重装失败：{err}")
        log(f"依赖重装失败：{err}（下次运行会自动重试）")
        return finish(result, args, log, EXIT_FAIL)

    result["message"] = f"已更新至 {result['after']}（{local_after[:8]}）"
    return finish(result, args, log, EXIT_OK)


def finish(result: dict, args, log: Logger, code: int) -> int:
    log.flush()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
