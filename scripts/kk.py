#!/usr/bin/env python3
"""kk — Kaggle 任务编排器（单文件，零第三方依赖）。

设计原则
--------
1. 包里不含任何项目信息：所有项目相关值来自命令行参数或 git/Kaggle 自动推断。
2. Kaggle script kernel 只上传 code_file 一个文件，因此 driver 在本地临时生成，
   内容只随 REPO/ENTRY/ENV 变化；真正的代码留在 GitHub 仓库里。
3. driver 在 kernel 内把仓库 clone 到 /tmp（不是 /kaggle/working），
   避免 `kaggle kernels output` 把整份代码一起打包下载。
4. 默认 enable_gpu=False，不会误占 GPU 配额。

常用
----
    kk run --entry orderbook/fusion_kl.py --env KAGGLE_HORIZON=3 \
           --dataset-in byh850515/btcusdt-orderbook-features \
           --dataset-out byh850515/btcusdt-fusion-pred

    kk push --entry a.py --dry-run     # 只生成 driver，不推送
    kk logs --grep "AUC|Traceback"
    kk pull && kk publish --dataset me/xxx
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DRIVER_NAME = "kk_driver.py"
DIRECT_NAME = "kernel_main.py"
META_NAME = "kernel-metadata.json"
STATE_DIR = ".kk"
# 这些名字的 env 值不打印，避免凭据进日志
SECRET_HINT = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD)", re.I)

try:  # Windows 控制台默认 GBK，中文日志会乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


# ---------------------------------------------------------------- 基础工具

def log(msg: str = "") -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    log(f"[kk] 错误: {msg}")
    raise SystemExit(code)


def sh(cmd: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    # Windows 上 kaggle(CLI 本身是 Python) 默认用 cp936 输出，中文日志会变乱码；
    # 强制 UTF-8 模式后，stdout/stderr 一律按 UTF-8 解码即可。
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    kw = dict(text=True, encoding="utf-8", errors="replace", env=env)
    if capture:
        kw.update(capture_output=True)
    return subprocess.run(cmd, check=check, **kw)


def kaggle_bin() -> str:
    exe = shutil.which("kaggle")
    if not exe:
        die("找不到 kaggle CLI，请先 pip install kaggle 并配置 ~/.kaggle/kaggle.json")
    return exe


def git(*args: str, check: bool = True) -> str:
    r = sh(["git", *args], check=check)
    return (r.stdout or "").strip()


# ---------------------------------------------------------------- 自动推断

def kaggle_username() -> str:
    env = os.environ.get("KAGGLE_USERNAME")
    if env:
        return env
    cfg = Path.home() / ".kaggle" / "kaggle.json"
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")).get("username", "")
        except Exception:  # noqa: BLE001
            pass
    r = sh([kaggle_bin(), "config", "view"], check=False)
    m = re.search(r"username\s*:\s*(\S+)", r.stdout or "")
    return m.group(1) if m else ""


def repo_url(explicit: str | None) -> str:
    if explicit:
        return explicit
    url = git("remote", "get-url", "origin", check=False)
    if not url:
        die("当前目录不是 git 仓库或无 origin，请用 --repo 指定")
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url.split(":", 1)[1]
    return url


def repo_name(url: str) -> str:
    name = url.rstrip("/").split("/")[-1]
    return name[:-4] if name.endswith(".git") else name


def sanitize(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:limit].strip("-")


def entry_module(entry: str) -> str:
    p = entry.replace("\\", "/")
    return p[:-3].replace("/", ".") if p.endswith(".py") else p


def default_title(entry: str, repo_label: str) -> str:
    """标题同时是 slug 的来源，别写中文——Kaggle 会把非 ASCII 全变成连字符。"""
    return f"{repo_label} {entry.replace(chr(92), '/')}"[:50]


def use_module(local: Path, entry: str, forced: str | None) -> bool:
    """默认按模块运行：`python -m a.b` 的 sys.path[0] 是仓库根，包内导入才不会崩。

    子目录没有 __init__.py 也没关系（Python 3 命名空间包），所以不能靠它判断。
    只有文件名不是合法标识符（含 - 或空格）时才退回 `python file`。
    """
    if forced == "module":
        return True
    if forced == "file":
        return False
    return Path(entry).stem.isidentifier()


# ---------------------------------------------------------------- 状态记忆

def state_path() -> Path:
    """按 cwd 记忆最近一次 kernel，存在用户目录，绝不污染项目仓库。"""
    key = sanitize(str(Path.cwd().resolve()).replace("\\", "-").replace(":", ""), 60)
    return Path.home() / STATE_DIR / f"{key}.json"


def save_state(**kw) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
    data.update({k: v for k, v in kw.items() if v})
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state() -> dict:
    p = state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def need_slug(arg: str | None) -> str:
    if arg:
        return arg
    slug = load_state().get("slug")
    if slug:
        log(f"[kk] 使用上次的 kernel: {slug}")
        return slug
    die("未指定 --slug，且本目录没有上次推送记录")
    raise AssertionError


# ---------------------------------------------------------------- driver

DRIVER_TMPL = '''#!/usr/bin/env python3
"""kk driver —— 由 kk 生成并上传，不是项目文件。"""
import os
import pathlib
import shutil
import subprocess
import sys

REPO = {repo!r}
REF = {ref!r}
ENTRY = {entry!r}
USE_MODULE = {use_module!r}
TOKEN_SECRET = {token_secret!r}
ENV = {env!r}


def log(msg):
    print("[kk] " + str(msg), flush=True)


for k, v in ENV.items():
    os.environ[str(k)] = str(v)

url = REPO
if TOKEN_SECRET:
    try:
        from kaggle_secrets import UserSecretsClient
        tok = UserSecretsClient().get_secret(TOKEN_SECRET)
    except Exception as exc:  # noqa: BLE001
        tok = None
        log("secret unavailable: %r" % (exc,))
    if tok:
        url = REPO.replace("https://", "https://%s:x-oauth-basic@" % tok, 1)

ROOT = pathlib.Path("/tmp/kk_repo")
shutil.rmtree(ROOT, ignore_errors=True)

cmd = ["git", "clone", "--depth=1"]
if REF:
    cmd += ["--branch", REF]
cmd += [url, str(ROOT)]
log("clone %s -> %s" % (REPO, ROOT))
subprocess.run(cmd, check=True)

os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")

req = ROOT / "requirements.txt"
if req.exists():
    log("pip install -r requirements.txt")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)], check=True)

run_cmd = [sys.executable, "-m", ENTRY] if USE_MODULE else [sys.executable, ENTRY]
log("run: " + " ".join(run_cmd))
subprocess.run(run_cmd, check=True, cwd=str(ROOT))

out = pathlib.Path("/kaggle/working")
log("--- /kaggle/working ---")
for p in sorted(out.rglob("*")):
    if p.is_file():
        log("  %s  %.2f MB" % (p.relative_to(out), p.stat().st_size / 1048576))
'''


def build_driver(url: str, entry: str, ref: str, module: bool,
                 env: dict, token_secret: str | None) -> str:
    return DRIVER_TMPL.format(
        repo=url,
        ref=ref,
        entry=(entry_module(entry) if module else entry),
        use_module=module,
        token_secret=token_secret,
        env=env,
    )


def write_meta(d: Path, code_file: str, args, slug: str, title: str,
               datasets: list[str]) -> None:
    meta = {
        "id": slug,
        "title": title,
        "code_file": code_file,
        "language": "python",
        "kernel_type": "script",
        "is_private": not args.public,
        "enable_gpu": bool(args.gpu),
        "enable_internet": not args.no_internet,
        "dataset_sources": datasets,
        "competition_sources": [],
        "kernel_sources": [],
    }
    (d / META_NAME).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def stage_kernel(args, entry: str, url: str, slug: str, title: str,
                 env: dict, module: bool, datasets: list[str]) -> Path:
    """clone 模式：上传一个 driver，kernel 内 clone 仓库再跑入口。"""
    d = Path(tempfile.mkdtemp(prefix="kk_push_"))
    (d / DRIVER_NAME).write_text(
        build_driver(url, entry, args.ref, module, env, args.github_token_secret),
        encoding="utf-8",
    )
    write_meta(d, DRIVER_NAME, args, slug, title, datasets)
    return d


def inject_env(src: str, env: dict) -> str:
    """把 env 注入到脚本开头；若首部有 from __future__ 则插在它之后（否则语法错误）。"""
    if not env:
        return src
    head = "import os as _os\n" + "".join(
        f"_os.environ[{k!r}] = {v!r}\n" for k, v in env.items())
    lines = src.splitlines(keepends=True)
    idx = 1 if lines and lines[0].startswith("#!") else 0   # 保留 shebang 在首行
    for i, ln in enumerate(lines[:10]):
        if ln.strip().startswith("from __future__"):
            idx = i + 1
            break
    return "".join(lines[:idx]) + head + "".join(lines[idx:])


def stage_direct(args, entry: str, slug: str, title: str,
                 env: dict, datasets: list[str]) -> Path:
    """direct 模式：入口脚本自包含（自己 clone/pip），直接当 code_file 上传。"""
    d = Path(tempfile.mkdtemp(prefix="kk_direct_"))
    src = Path(entry).read_text(encoding="utf-8")
    (d / DIRECT_NAME).write_text(inject_env(src, env), encoding="utf-8")
    write_meta(d, DIRECT_NAME, args, slug, title, datasets)
    return d


# ---------------------------------------------------------------- 子命令

def cmd_push(args) -> None:
    local = Path.cwd()
    entry = args.entry.replace("\\", "/")
    if not (local / entry).exists():
        log(f"[kk] 警告: 本地找不到 {entry}，仍按给定路径生成 driver")

    direct = args.mode == "direct"
    url = "" if direct else repo_url(args.repo)
    repo_label = repo_name(url) if url else local.name
    title = args.title or default_title(entry, repo_label)
    # Kaggle 会用 title 反推 slug（不一致时直接改写 id），所以默认让两者同源
    slug = args.slug or f"{kaggle_username() or 'unknown'}/{sanitize(title)}"
    env = parse_env(args.env, args.env_from)
    module = False if direct else use_module(local, entry, args.run_as)
    datasets = args.dataset_in or []

    if direct:
        if not (local / entry).exists():
            die(f"direct 模式要求本地存在入口文件: {entry}")
        d = stage_direct(args, entry, slug, title, env, datasets)
        code_file = DIRECT_NAME
    else:
        d = stage_kernel(args, entry, url, slug, title, env, module, datasets)
        code_file = DRIVER_NAME

    log(f"[kk] kernel : {slug}")
    log(f"[kk] 模式   : {'direct 单文件直传' if direct else 'clone 仓库 driver'}")
    log(f"[kk] 标题   : {title}")
    log(f"[kk] 入口   : {entry}" + ("" if direct else f"  ({'python -m' if module else 'python file'})"))
    log(f"[kk] 暂存   : {d}")
    if env:
        log(f"[kk] env    : {mask_env(env)}")
    if datasets:
        log(f"[kk] 数据   : {datasets}")
    if not args.public and env:
        log("[kk] 私有 kernel：凭据只落在该 code_file 内，push 不会丢（这点比网页 attach Secret 可靠）")

    if args.dry_run:
        save_state(slug=slug, entry=entry, repo=url)
        log("[kk] --dry-run，跳过 push")
        log("-" * 60)
        text = (d / code_file).read_text(encoding="utf-8")
        for k, v in env.items():                      # dry-run 也要打码，别把凭据打到终端
            if v and SECRET_HINT.search(k):
                text = text.replace(v, "***")
        log(text)
        return

    r = sh([kaggle_bin(), "kernels", "push", "-p", str(d)], check=False)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    log(out)
    if r.returncode != 0:
        die(f"push 失败，退出码 {r.returncode}", code=1)

    real = URL_RE.search(out)
    if real and real.group(1) != slug:
        log(f"[kk] Kaggle 把 slug 改写为 {real.group(1)}（title 与 id 不同源时会发生）")
        slug = real.group(1)
    save_state(slug=slug, entry=entry, repo=url)
    log("[kk] 已推送，用 `kk wait` / `kk logs` 跟进")


def parse_env(pairs: list[str] | None, from_names: list[str] | None = None) -> dict:
    """--env K=V 直接给值；--env-from NAME 从本地环境变量取值（不进命令行/日志）。"""
    out = {}
    for item in pairs or []:
        k, _, v = item.partition("=")
        if not k:
            die(f"--env 需要 K=V 形式: {item!r}")
        out[k] = v
    for name in from_names or []:
        val = os.environ.get(name)
        if not val:
            die(f"--env-from {name}: 本地环境变量未设置")
        out[name] = val
    return out


def mask_env(env: dict) -> dict:
    return {k: ("***" if SECRET_HINT.search(k) else v) for k, v in env.items()}


STATUS_RE = re.compile(r'has status "?([^"\n]+)"?', re.I)
URL_RE = re.compile(r"https://www\.kaggle\.com/code/([\w.-]+/[\w.-]+)")


def kernel_status(slug: str) -> str:
    """返回 complete / running / error ...；兼容 `KernelWorkerStatus.RUNNING` 形态。"""
    r = sh([kaggle_bin(), "kernels", "status", slug], check=False)
    text = ((r.stdout or "") + (r.stderr or "")).strip()
    if "Permission 'kernels.get' was denied" in text or r.returncode != 0:
        return "not_found"
    m = STATUS_RE.search(text)
    if m:
        return m.group(1).strip().split(".")[-1].lower()
    return text.splitlines()[-1] if text else "unknown"


def cmd_status(args) -> None:
    log(kernel_status(need_slug(args.slug)))


def cmd_wait(args) -> None:
    slug = need_slug(args.slug)
    deadline = time.time() + args.timeout
    log(f"[kk] 等待 {slug}（上限 {args.timeout}s，每 {args.interval}s）")
    while True:
        st = kernel_status(slug)
        log(f"    {time.strftime('%H:%M:%S')}  {st}")
        if st.startswith("complete"):
            return
        if st.startswith("error") or st.startswith("cancel"):
            die(f"kernel 终止: {st}", code=1)
        if time.time() > deadline:
            die(f"超时（最后状态 {st}），kernel 仍在跑，可稍后 `kk logs`", code=1)
        time.sleep(args.interval)


def decode_logs(raw: str) -> str:
    """kaggle kernels logs 可能返回 JSON 数组 / 带前导逗号 / 逐行 JSON。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    for cand in (raw, raw.lstrip(",")):
        try:
            arr = json.loads(cand)
            if isinstance(arr, list):
                return "".join(str(i.get("data", "")) for i in arr if isinstance(i, dict))
        except Exception:  # noqa: BLE001
            pass
    parts = []
    for line in raw.splitlines():
        line = line.strip().lstrip(",").rstrip(",")
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict) and "data" in obj:
            parts.append(str(obj["data"]))
    return "".join(parts) if parts else raw


def cmd_logs(args) -> None:
    slug = need_slug(args.slug)
    r = sh([kaggle_bin(), "kernels", "logs", slug], check=False)
    text = decode_logs((r.stdout or "") + (r.stderr or ""))
    if args.save:
        Path(args.save).write_text(text, encoding="utf-8")
        log(f"[kk] 已保存日志 -> {args.save}")
    lines = text.splitlines()
    if args.grep:
        pat = re.compile(args.grep, re.I)
        lines = [ln for ln in lines if pat.search(ln)]
        log(f"[kk] 匹配 {len(lines)} 行（/{args.grep}/）")
    if args.tail:
        lines = lines[-args.tail:]
    log("\n".join(lines))


def cmd_pull(args) -> None:
    slug = need_slug(args.slug)
    dest = Path(args.dest or (Path(STATE_DIR) / sanitize(slug.replace("/", "-")))).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [kaggle_bin(), "kernels", "output", slug, "-p", str(dest)]
    if args.pattern:
        # kernel 常把中间缓存写进 /kaggle/working（如 75MB 的 K 线缓存），
        # 用正则只拉真正要的产物，别把缓存也拖回本地。
        cmd += ["--file-pattern", args.pattern]
        log(f"[kk] 只下载匹配 /{args.pattern}/ 的文件")
    sh(cmd, capture=False)
    save_state(dest=str(dest))
    files = [p for p in dest.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    log(f"[kk] 产物 -> {dest}  ({len(files)} 个文件 / {total / 1048576:.2f} MB)")


def dataset_exists(ds: str) -> bool:
    r = sh([kaggle_bin(), "datasets", "files", ds], check=False)
    return r.returncode == 0 and "404" not in (r.stderr or "")


def cmd_publish(args) -> None:
    src = Path(args.from_dir or load_state().get("dest") or (Path(STATE_DIR) / "out")).resolve()
    if not src.is_dir():
        die(f"产物目录不存在: {src}，先 `kk pull`")

    staging = src / "_publish"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    globs = args.artifact or ["*"]
    picked = []
    for p in src.rglob("*"):
        if not p.is_file() or staging in p.parents:
            continue
        rel = p.relative_to(src).as_posix()
        if any(fnmatch.fnmatch(p.name, g) or fnmatch.fnmatch(rel, g) for g in globs):
            target = staging / p.name
            shutil.copy2(p, target)
            picked.append(target)
    if not picked:
        die(f"{src} 下没有匹配 {globs} 的文件")

    ds = args.dataset
    meta = {
        "title": args.title or ds.split("/")[-1].replace("-", " ")[:50],
        "id": ds,
        "licenses": [{"name": "CC0-1.0"}],
    }
    (staging / "dataset-metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    total = sum(p.stat().st_size for p in picked) / 1048576
    log(f"[kk] 待发布 {len(picked)} 个文件 / {total:.2f} MB -> {ds}")

    version = args.version_msg or f"from {load_state().get('slug', 'kk')}"
    if dataset_exists(ds) and not args.create:
        sh([kaggle_bin(), "datasets", "version", "-p", str(staging), "-m", version], capture=False)
        log(f"[kk] dataset 新版本已发布: https://www.kaggle.com/datasets/{ds}")
    else:
        sh([kaggle_bin(), "datasets", "create", "-p", str(staging)], capture=False)
        log(f"[kk] dataset 已创建: https://www.kaggle.com/datasets/{ds}")


def cmd_run(args) -> None:
    cmd_push(args)
    if args.dry_run:
        return
    wait_args = argparse.Namespace(slug=args.slug, timeout=args.timeout, interval=args.interval)
    cmd_wait(wait_args)
    cmd_logs(argparse.Namespace(slug=args.slug, grep=args.grep, tail=args.tail, save=None))
    if args.skip_pull:
        return
    cmd_pull(argparse.Namespace(slug=args.slug, dest=args.dest, pattern=args.pattern))
    if args.dataset_out and not args.skip_publish:
        cmd_publish(argparse.Namespace(
            dataset=args.dataset_out, from_dir=args.dest,
            artifact=args.artifact, title=None, version_msg=None, create=False))


def cmd_install_skill(args) -> None:
    """把本仓库整个复制到 ~/.codebuddy/skills/kaggle-runner（一次性拷贝，可选）。"""
    root = Path(__file__).resolve().parent.parent
    dest = Path.home() / ".codebuddy" / "skills" / "kaggle-runner"
    if root.resolve() == dest.resolve():
        log("[kk] 当前已在 skills 目录内，无需安装")
        return
    if dest.exists():
        if not args.force:
            die(f"已存在: {dest}（加 --force 覆盖）")
        shutil.rmtree(dest)
    shutil.copytree(root, dest,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "_patch.py"))
    log(f"[kk] 已安装到 {dest}")
    log("[kk] 更推荐：把仓库 clone 到该路径，或用 mklink /J 联接，避免两份代码漂移")
    log("[kk] 重启 CodeBuddy 或新建会话后生效")


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kk", description="Kaggle 任务编排器（零配置）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_push_flags(sp, entry_required=True):
        sp.add_argument("--entry", required=entry_required, help="仓库内 py 相对路径")
        sp.add_argument("--repo", help="GitHub 仓库 URL，默认取 git remote origin")
        sp.add_argument("--ref", default="", help="分支/标签，默认仓库默认分支")
        sp.add_argument("--slug", help="username/kernel-slug，默认自动推断")
        sp.add_argument("--title", help="kernel 标题")
        sp.add_argument("--env", action="append", help="K=V，可重复")
        sp.add_argument("--env-from", action="append",
                        help="从本地环境变量取值注入（凭据走这个，值不进命令行/日志），可重复")
        sp.add_argument("--mode", choices=["clone", "direct"], default="clone",
                        help="clone=仓库 driver（默认）；direct=入口自包含，整文件直传")
        sp.add_argument("--dataset-in", action="append", help="挂载的 dataset，可重复")
        sp.add_argument("--run-as", choices=["auto", "module", "file"], default="auto")
        sp.add_argument("--gpu", action="store_true", help="显式申请 GPU（默认关闭）")
        sp.add_argument("--no-internet", action="store_true")
        sp.add_argument("--public", action="store_true", help="公开 kernel（默认私有）")
        sp.add_argument("--github-token-secret", help="私有仓库用的 Kaggle Secret 名")
        sp.add_argument("--dry-run", action="store_true")

    sp = sub.add_parser("push", help="生成 driver 并推送 kernel")
    add_push_flags(sp)
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("status", help="查询 kernel 状态")
    sp.add_argument("--slug")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("wait", help="轮询直到完成")
    sp.add_argument("--slug")
    sp.add_argument("--timeout", type=int, default=3600)
    sp.add_argument("--interval", type=int, default=30)
    sp.set_defaults(func=cmd_wait)

    sp = sub.add_parser("logs", help="拉日志")
    sp.add_argument("--slug")
    sp.add_argument("--grep", help="正则过滤")
    sp.add_argument("--tail", type=int)
    sp.add_argument("--save")
    sp.set_defaults(func=cmd_logs)

    sp = sub.add_parser("pull", help="下载 kernel 产物")
    sp.add_argument("--slug")
    sp.add_argument("--dest")
    sp.add_argument("--pattern", help="正则，只下载匹配的文件（避免把中间缓存拉回本地）")
    sp.set_defaults(func=cmd_pull)

    sp = sub.add_parser("publish", help="把产物发布为 dataset")
    sp.add_argument("--dataset", required=True, help="username/dataset-slug")
    sp.add_argument("--from", dest="from_dir", help="产物目录，默认上次 pull 的目录")
    sp.add_argument("--artifact", action="append", help="文件名 glob，可重复")
    sp.add_argument("--title")
    sp.add_argument("--version-msg")
    sp.add_argument("--create", action="store_true", help="强制走 create")
    sp.set_defaults(func=cmd_publish)

    sp = sub.add_parser("run", help="一条龙：push → wait → logs → pull → publish")
    add_push_flags(sp)
    sp.add_argument("--timeout", type=int, default=7200)
    sp.add_argument("--interval", type=int, default=30)
    sp.add_argument("--grep")
    sp.add_argument("--tail", type=int, default=80)
    sp.add_argument("--dest")
    sp.add_argument("--pattern", help="正则，只下载匹配的产物文件")
    sp.add_argument("--dataset-out")
    sp.add_argument("--artifact", action="append")
    sp.add_argument("--skip-pull", action="store_true")
    sp.add_argument("--skip-publish", action="store_true")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("install-skill", help="把 kaggle-runner 技能装到 ~/.codebuddy/skills")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_install_skill)

    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except subprocess.CalledProcessError as exc:
        die(f"命令失败({exc.returncode}): {' '.join(map(str, exc.cmd))}", code=1)
    except KeyboardInterrupt:
        log("\n[kk] 中断")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
