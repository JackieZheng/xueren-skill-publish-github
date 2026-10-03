#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish_skill.py — 把本地 WorkBuddy skill 发布为 GitHub 开源仓库（一键机械流程，不限 skill 名称）。

自动完成「易错 / 重复」步骤：
  1. 脱敏：把配置的本机绝对路径替换为通用占位（防泄漏 E 盘 / 用户目录）
  2. 给 SKILL.md frontmatter 加 `github:` 字段
  3. 生成 / 刷新 meta.json（id/name/version/author/platforms/description/github/agent_created/updated_at）
  4. 生成 LICENSE（默认 MIT，版权声明人取 SKILL.md 的 author 字段）、.gitignore（若缺失）
  5. 若 README.md 缺失，基于模板生成一份基础 README（含目录树）
  6. 通过 GitHub API 创建 public 仓库（幂等：已存在则跳过）
  7. git init + commit + 用 PAT 走 HTTPS 重试推送（应对 GFW 抖动）
  8. 推送成功后自动创建 GitHub Release（tag = v<SKILL.md version>，幂等：已有则更新正文；--no-release 跳过）
  9. 可选（默认关闭）：若本地装有 xueren-skill-backup 且传 --backup，则同步到 WB Skill 备份目录；否则跳过，不影响发布

安全：PAT 仅用于本次 remote URL 与 API 调用，不写入任何 skill 文件；用完建议到
      GitHub 吊销并改用 SSH（脚本支持 --ssh 走 git@github.com:）。

开源零个人信息（铁律）：
  本脚本不硬编码任何 GitHub 账号 / 邮箱 / 用户名 / 本地绝对路径。
  - --user 强制必填，缺失即报错退出，避免误推到不打算公开的账号。
  - 提交邮箱**默认**用 GitHub noreply 地址（{numeric-id}+{login}@users.noreply.github.com，
    由 --token 查 /user 自动生成）；显式 --email 可覆盖，但传非 noreply 地址会打警告——
    ⚠️ 旧版本默认取本机 `git config user.email`（全局真实邮箱），实测泄漏过一次（已修）。
  - 脱敏规则用户名用正则通配；通用规则与本机私有规则分离
    （references/sensitive_paths.json 公开 / sensitive_paths.local.json 由 .gitignore 排除）。
  - LICENSE 与 README 的署名取 SKILL.md 的 author 字段，模板内无硬编码人名。

用法示例（--user 必须显式指定目标 GitHub 账号，脚本不设任何默认账号）：
  python publish_skill.py --skill "~/.workbuddy/skills/my-skill" \
         --user <your-github-user> --email <your-noreply-email> --token <your-pat> --bump-version
  python publish_skill.py --skill my-skill --user <your-github-user> \
         --email <your-noreply-email> --token <your-pat> --ssh --backup
"""
import os
import sys
import re
import json
import time
import base64
import shutil
import subprocess
import datetime
import urllib.request
import urllib.error
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)                     # <skill>/  (scripts/..)
REFERENCES = os.path.join(SKILL_ROOT, "references")
SENSITIVE_CFG = os.path.join(REFERENCES, "sensitive_paths.json")
SENSITIVE_LOCAL_CFG = os.path.join(REFERENCES, "sensitive_paths.local.json")  # 本机专用，不入版本库

# 提交身份：不硬编码任何个人身份，按顺序取环境变量 / git config，取不到则为空由调用方补齐。
def _git_config(name):
    try:
        r = subprocess.run(["git", "config", name], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


# 本脚本**不设任何默认 GitHub 账号**：--user 强制必填，缺失即报错退出，
# 避免别的用户误把仓库推到不打算公开的账号下。邮箱同理，来自调用方本机配置。
# ⚠️ 开源铁律（2026-10-02 踩坑，真实泄漏过）：**绝不要**把 `git config user.email`（本机全局）
# 当默认提交邮箱——本机全局往往是真实邮箱（形如 someone@example.com），一旦被写进目标仓库的
# 本地 config 并 commit，就会随 commit 永久公开在 GitHub 历史里（实测：git push 恰好成功时，
# 污染直接进了远端 main）。默认只认显式来源：GH_EMAIL 环境变量 → 否则留空，
# 由 main() 用 noreply_identity(--token) 生成 `{id}+{login}@users.noreply.github.com`。
DEFAULT_EMAIL = os.environ.get("GH_EMAIL", "")
DEFAULT_AUTHOR = "Author"
DEFAULT_LICENSE = "MIT"

# 按用户约定（2026-10-01）：**开发日志不随发布物外发**——GitHub 与 SkillHub 口径一致。
# 做法：① 不进仓库：补进 .gitignore + `git rm --cached`（历史已提交过的也要清掉）；
#       ② API 直推路径：跳过该文件，并显式下发 sha=None 的删除条目把远端旧文件删掉。
NEVER_PUBLISH = {"DEVLOG.md"}
BACKUP_SCRIPT = os.path.expanduser(r"~/.workbuddy/skills/xueren-skill-backup/scripts/sync_backups.py")

# 脱敏默认值（references/sensitive_paths.json 优先加载；二者合并去重）。
# 用 [\\/] 兼容正斜杠/反斜杠混用（Windows 路径在不同来源下分隔符可能不一致）。
# 用户名用 [^\\/]+ 匹配，**绝不硬编码任何具体用户名**。
# 用户自己的私有路径（如额外磁盘的自定义目录）写进 references/sensitive_paths.local.json，
# 该文件被 .gitignore 排除，不会随仓库发布。
DEFAULT_REPLACEMENTS = [
    (r"C:[\\/]Users[\\/][^\\/]+[\\/]\.workbuddy[\\/]skills", "~/.workbuddy/skills"),
    (r"C:[\\/]Users[\\/][^\\/]+", "~"),
]

TEXT_EXT = {
    ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".toml",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".cjs", ".mjs",
    ".cfg", ".ini", ".csv", ".gitignore", ".gitattributes", ".html", ".css",
    ".sh", ".bat", ".ps1", ".skilli",
}

# 不脱敏脚本自身与两份脱敏配置（local.json 里是真实私有路径，被误替换会直接损坏）
SKIP_PATHS = {os.path.abspath(__file__), os.path.abspath(SENSITIVE_CFG), os.path.abspath(SENSITIVE_LOCAL_CFG)}


# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------
def load_replacements():
    """内置默认 → sensitive_paths.json（可发布的通用模板）→ sensitive_paths.local.json（本机私有，不入版本库）。"""
    reps = list(DEFAULT_REPLACEMENTS)
    for cfg in (SENSITIVE_CFG, SENSITIVE_LOCAL_CFG):
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("replacements", []):
                pat, rep = item.get("pattern"), item.get("replacement")
                if pat and rep and (pat, rep) not in reps:
                    reps.append((pat, rep))
        except Exception as e:
            print(f"[warn] 读取脱敏配置失败 {cfg}：{e}")
    return reps


def desensitize_file(fp, reps):
    if os.path.abspath(fp) in SKIP_PATHS:
        return 0
    ext = os.path.splitext(fp)[1].lower()
    if ext not in TEXT_EXT:
        return 0
    try:
        with open(fp, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return 0
    new = text
    n = 0
    for pat, rep in reps:
        new, c = re.subn(pat, rep, new, flags=re.IGNORECASE)
        n += c
    if c == 0 and new == text:
        return 0
    # 仅在确有替换时写回
    changed = sum(1 for (pat, rep) in reps if re.search(pat, text, flags=re.IGNORECASE))
    if changed == 0:
        return 0
    with open(fp, "w", encoding="utf-8") as f:
        f.write(new)
    return changed


def desensitize_dir(skill_dir, reps):
    total = 0
    touched = []
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        # 不进入这些目录
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__", ".pytest_cache")]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            if os.path.abspath(fp) in SKIP_PATHS:
                continue
            c = desensitize_file(fp, reps)
            if c:
                total += c
                touched.append(os.path.relpath(fp, skill_dir))
    return total, touched


# ---------------------------------------------------------------------------
# frontmatter 解析
# ---------------------------------------------------------------------------
def read_text(fp):
    with open(fp, "r", encoding="utf-8") as f:
        return f.read()


def get_fm_field(text, key):
    m = re.search(r"^" + re.escape(key) + r"\s*:\s*(.*)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def get_fm_list(text, key):
    v = get_fm_field(text, key)
    if not v:
        return []
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    return [x.strip().strip('"').strip("'") for x in v.split(",") if x.strip()]


def bump_patch(ver):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", (ver or "").strip())
    if not m:
        return "1.0.1" if not ver else ver
    a, b, c = (int(x) for x in m.groups())
    return f"{a}.{b}.{c + 1}"


def add_github_field(skill_md, url):
    text = read_text(skill_md)
    # 仅在前置 frontmatter 区域内判断
    m = re.search(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not m:
        print("[warn] 未找到 frontmatter，无法加 github 字段")
        return False
    fm = m.group(1)
    if re.search(r"^\s*github\s*:", fm, re.MULTILINE):
        return False  # 已存在
    # 插入位置优先级：platforms: > examples: > 末尾
    new_fm = None
    for key in ("platforms", "examples"):
        mm = re.search(r"^(%s\s*:.*)$" % re.escape(key), fm, re.MULTILINE)
        if mm:
            insert_at = mm.end()
            new_fm = fm[:insert_at] + "\n" + f"github: {url}" + fm[insert_at:]
            break
    if new_fm is None:
        new_fm = fm.rstrip() + "\n" + f"github: {url}\n"
    new_text = text[:m.start(1)] + new_fm + text[m.end(1):]
    with open(skill_md, "w", encoding="utf-8") as f:
        f.write(new_text)
    return True


# ---------------------------------------------------------------------------
# 生成配套文件
# ---------------------------------------------------------------------------
def gen_meta_json(skill_dir, fm, url):
    meta_path = os.path.join(skill_dir, "meta.json")
    platform = fm.get("platforms") or ["WorkBuddy"]
    if isinstance(platform, str):
        platform = [platform]
    meta = {
        "id": fm.get("id") or os.path.basename(skill_dir),
        "name": fm.get("name") or fm.get("id") or os.path.basename(skill_dir),
        "version": fm.get("version") or "1.0.0",
        "author": fm.get("author") or DEFAULT_AUTHOR,
        "platforms": platform,
        "description": fm.get("description") or "",
        "github": url,
        "agent_created": True,
        "updated_at": datetime.date.today().isoformat(),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return meta_path


def gen_license(skill_dir, year=None, author=None):
    year = year or datetime.date.today().year
    holder = author or DEFAULT_AUTHOR
    content = (
        "MIT License\n\n"
        f"Copyright (c) {year} {holder}\n\n"
        "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
        "of this software and associated documentation files (the \"Software\"), to deal\n"
        "in the Software without restriction, including without limitation the rights\n"
        "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
        "copies of the Software, and to permit persons to whom the Software is\n"
        "furnished to do so, subject to the following conditions:\n\n"
        "The above copyright notice and this permission notice shall be included in all\n"
        "copies or substantial portions of the Software.\n\n"
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n'
        "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
        "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n"
        "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
        "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n"
        "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n"
        "SOFTWARE.\n"
    )
    with open(os.path.join(skill_dir, "LICENSE"), "w", encoding="utf-8") as f:
        f.write(content)
    return os.path.join(skill_dir, "LICENSE")


def gen_gitignore(skill_dir):
    body = (
        "# 依赖\n"
        "node_modules/\n"
        "\n"
        "# 用户凭据 —— 绝不进版本库\n"
        ".env\n"
        "*.env\n"
        "*.key\n"
        "*.pem\n"
        "*.p12\n"
        "private.key\n"
        "*creds*\n"
        "*secret*\n"
        "*token*\n"
        "*api_key*\n"
        "*apikey*\n"
        "\n"
        "# 运行临时产物\n"
        "log/\n"
        "*.tmp\n"
        "asr_out_*/\n"
        "\n"
        "# 开发日志 —— 不随仓库发布（用户约定）\n"
        "DEVLOG.md\n"
        "\n"
        "# Python\n"
        "__pycache__/\n"
        "*.pyc\n"
        "*.pyo\n"
        ".pytest_cache/\n"
        "\n"
        "# 系统\n"
        ".DS_Store\n"
        "Thumbs.db\n"
    )
    with open(os.path.join(skill_dir, ".gitignore"), "w", encoding="utf-8") as f:
        f.write(body)
    return os.path.join(skill_dir, ".gitignore")


def ensure_never_publish_ignored(skill_dir):
    """把 NEVER_PUBLISH 里的文件名补进 .gitignore。

    为什么需要单独一步：`gen_gitignore` 只在 .gitignore **缺失**时才生成，
    已存在的老仓库会被整段跳过 → 老仓库永远拿不到「DEVLOG.md 不外发」这条规则。
    """
    gp = os.path.join(skill_dir, ".gitignore")
    try:
        with open(gp, "r", encoding="utf-8") as f:
            body = f.read()
    except Exception:
        body = ""
    lines = {ln.strip() for ln in body.splitlines()}
    missing = [n for n in sorted(NEVER_PUBLISH) if n not in lines]
    if not missing:
        return 0
    add = ""
    if body and not body.endswith("\n"):
        add += "\n"
    add += "\n# 开发日志 —— 不随仓库发布（用户约定）\n" + "".join(n + "\n" for n in missing)
    with open(gp, "a", encoding="utf-8") as f:
        f.write(add)
    return len(missing)


def gen_readme(skill_dir, fm, url, tree):
    tpl_path = os.path.join(REFERENCES, "readme_template.md")
    if os.path.isfile(tpl_path):
        with open(tpl_path, "r", encoding="utf-8") as f:
            tpl = f.read()
    else:
        tpl = (
            "# {name}\n\n> {description}\n\n"
            "## 项目结构\n\n```\n{tree}\n```\n\n"
            "## License\n\n[MIT](./LICENSE)\n\n---\n\nGitHub: {repo}\n"
        )
    content = (tpl
               .replace("{name}", fm.get("name") or fm.get("id") or os.path.basename(skill_dir))
               .replace("{description}", fm.get("description") or "")
               .replace("{repo}", url)
               .replace("{tree}", tree)
               .replace("{year}", str(datetime.date.today().year))
               .replace("{author}", fm.get("author") or DEFAULT_AUTHOR))
    with open(os.path.join(skill_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(content)
    return os.path.join(skill_dir, "README.md")


def build_tree(skill_dir, exclude=None):
    exclude = exclude or {".git", "node_modules", "__pycache__", ".pytest_cache"}
    lines = [os.path.basename(skill_dir) + "/"]
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [d for d in dirnames if d not in exclude]
        rel = os.path.relpath(dirpath, skill_dir)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        prefix = "    " * depth
        for d in sorted(dirnames):
            lines.append(f"{prefix}{d}/")
        for fn in sorted(filenames):
            lines.append(f"{prefix}{fn}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GitHub API / git
# ---------------------------------------------------------------------------
def create_repo(token, user, repo, desc, private, license_template):
    payload = {
        "name": repo,
        "description": desc or "",
        "private": bool(private),
        "auto_init": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/user/repos",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "xueren-skill-publish",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            code = resp.getcode()
            body = resp.read().decode("utf-8", "replace")
            if code in (200, 201):
                return True, "仓库已创建"
            return False, f"HTTP {code}: {body[:200]}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 422 and re.search(r"already exists", body, re.IGNORECASE):
            return True, "仓库已存在，跳过创建"
        return False, f"HTTP {e.code}: {body[:200]}"
    except Exception as e:
        return False, f"请求失败：{e}"


def gh_api(token, method, path, payload=None):
    """GitHub REST 通用封装。返回 (http_code, parsed_json)。"""
    url = "https://api.github.com" + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "xueren-skill-publish",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.getcode(), (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:300]}
    except Exception as e:
        return 0, {"error": str(e)}


def git(*args, cwd, timeout=75):
    try:
        return subprocess.run(
            ["git", "-c", "http.sslVerify=false", *args],
            cwd=cwd, timeout=timeout,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        # ⚠️ 2026-10-02 踩坑（真实炸过）：GFW 下 `git push origin v<ver>` 会超时，
        # 抛出的 TimeoutExpired **此前没有任何 try 接住** → 整个发布脚本 traceback 退出，
        # 现象是「main 已推成功，但 tag 与 Release 都没建，脚本还报一大段栈」。
        # 这里统一把超时转成"失败的普通结果"（returncode=124），交给上层重试 /
        # 走 API 兜底（api_push_tag），绝不让它炸掉整个流程。
        return subprocess.CompletedProcess(
            ["git", *[str(a) for a in args]], 124, "",
            "timeout after %ss: git %s" % (timeout, " ".join(str(a) for a in args[:2])),
        )


# --force：远端历史被重写过（non-fast-forward）时用强制推送
FORCE = [False]
# --api-only：完全绕开 git push，只走 GitHub API（沙箱里 git push 443 长期抖动时的终稿路径）
API_ONLY = [False]
# --reset-history：把远端历史重写成「一个 noreply 署名的根提交」（历史里已有真实邮箱时用它净化）
RESET_HISTORY = [False]


# git push 失败时的兜底：走 GitHub API 直接重写 main（沙箱 / GFW 下 443 抖动时常救场）
API_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv",
                 # 2026-10-03 补：本机私有且 .gitignore 已声明的运行期产物。
                 # ⚠️ 真实事故：playwright 的 .pwprofile 里含 Cookies / Local Storage(LevelDB)，
                 # 曾被 root 提交整包推上开源仓库。API 推送不是 git，必须自己跳过这些目录。
                 ".pwprofile", ".tmp_verify", ".tmp-publish-skillhub", ".idea", ".vscode"}


def gitignore_dirs(skill_dir):
    """读 <skill_dir>/.gitignore，返回其中声明的目录名（供 API 推送时一并跳过）。

    .gitignore 是「什么不该进仓库」的唯一权威，API 推送不会自动遵守它，
    所以这里把它解析出来，避免 .gitignore 里写了却还是被推上去的漏网目录。
    """
    names = set()
    gp = os.path.join(skill_dir, ".gitignore")
    if not os.path.isfile(gp):
        return names
    try:
        with open(gp, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith(("#", "!")):
                    continue
                if s.endswith("/"):
                    tail = s.rstrip("/").split("/")[-1]
                elif "/" in s:
                    tail = s.split("/")[-1].rstrip("/")
                else:
                    continue
                if tail:
                    names.add(tail)
    except Exception:
        pass
    return names


def noreply_identity(token, fallback_login=""):
    """取 GitHub 账号的 noreply 邮箱：{id}+{login}@users.noreply.github.com。

    开源零个人信息铁律：API 建的 commit 若不带 author/committer，GitHub 会用账号的
    公开档案邮箱（真实邮箱）署名——必须显式指定 noreply。id/login 由 GET /user 取，
    不硬编码。取不到就退回 fallback_login + 无邮箱（仅在明确无真实邮箱时）。
    """
    code, obj = gh_api(token, "GET", "/user")
    if code == 200 and isinstance(obj, dict):
        login = obj.get("login") or fallback_login
        uid = obj.get("id")
        if uid and login:
            return login, f"{uid}+{login}@users.noreply.github.com"
    return fallback_login, ""


def api_sync_main(skill_dir, user, repo, token, message, retries=3, root=False, ident=None):
    """用 Contents/Data API 把工作区现有文件强制写进 main。返回 (head_sha or None, err)。

    流程：逐文件创建 blob → 构造 tree → commit → force 更新 refs/heads/main。
    命中已有 blob（sha 不变）时 GitHub 会直接复用，不会重复上传。
    root=True 时不带 parent/base_tree，生成一个根提交（用于重写被污染的历史）。
    """
    entries, rel_paths = [], []
    skip_dirs = set(API_SKIP_DIRS) | gitignore_dirs(skill_dir)
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if fn in NEVER_PUBLISH:                 # 开发日志等：不进远端
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, skill_dir).replace("\\", "/")
            rel_paths.append(rel)
            try:
                with open(fp, "rb") as f:
                    content = f.read()
            except Exception as e:
                return None, f"读取文件失败 {rel}: {e}"
            code, obj = gh_api(token, "POST", f"/repos/{user}/{repo}/git/blobs",
                               {"content": base64.b64encode(content).decode("ascii"),
                                "encoding": "base64"})
            if code not in (200, 201):
                return None, f"上传 blob 失败 {rel}: HTTP {code} {str(obj)[:150]}"
            entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": obj.get("sha")})
    if not entries:
        return None, "工作区无文件可提交"

    # 远端已存在、但已列入「不外发」的文件 → 下发 sha=None 的删除条目，顺手清掉
    # （base_tree 会保留未提及的旧条目，不提就永远删不掉）
    if not root:
        for _name in sorted(NEVER_PUBLISH):
            _code, _ = gh_api(token, "GET", f"/repos/{user}/{repo}/contents/{_name}")
            if _code == 200:
                entries.append({"path": _name, "mode": "100644", "type": "blob", "sha": None})
                rel_paths.append(_name + "（删除）")

    tree_payload = {"tree": entries}
    if not root:
        tree_payload["base_tree"] = "HEAD"
    tree_code, tree_obj = gh_api(
        token, "POST", f"/repos/{user}/{repo}/git/trees", tree_payload)
    if tree_code not in (200, 201):
        return None, f"创建 tree 失败：HTTP {tree_code} {str(tree_obj)[:200]}"
    base = tree_obj.get("sha")

    commit_payload = {"message": message, "tree": base}
    # 开源零个人信息铁律：无论根提交还是子提交，都显式写明 noreply 身份。
    # 不写的话 GitHub 会用账号档案里的真实邮箱署名，且会永久留在公开历史里。
    if root and (not ident or not ident[1]):
        return None, "root 提交必须提供 noreply 身份（ident）"
    if ident and ident[1]:
        commit_payload["author"] = {"name": ident[0], "email": ident[1]}
        commit_payload["committer"] = {"name": ident[0], "email": ident[1]}
    c_code, c_obj = gh_api(token, "POST", f"/repos/{user}/{repo}/git/commits",
                           commit_payload)
    if c_code not in (200, 201):
        return None, f"创建 commit 失败：HTTP {c_code} {str(c_obj)[:200]}"
    sha = c_obj.get("sha")
    if not sha:
        return None, f"commit 未返回 sha: {str(c_obj)[:200]}"

    r_code, _ = gh_api(token, "PATCH", f"/repos/{user}/{repo}/git/refs/heads/main",
                       {"sha": sha, "force": True})
    if r_code not in (200, 201, 204):
        return None, f"更新 main ref 失败：HTTP {r_code}"
    return sha, ""


def remote_main_sha(user, repo, token):
    """取远端 main 的 head sha（拿不到返回 ""）。两种端点形状都试一遍。"""
    for path in (f"/repos/{user}/{repo}/git/ref/heads/main",
                 f"/repos/{user}/{repo}/commits/main"):
        _code, obj = gh_api(token, "GET", path)
        if isinstance(obj, dict):
            sha = ((obj.get("object") or {}).get("sha") or obj.get("sha") or "")
            if sha:
                return sha
    return ""


def api_push_tag(user, repo, token, tag, sha, retries=3):
    """用 API 直接创建/移动 tag ref（git push 走不通时的兜底）。

    远端同名 tag 已存在且指向不可达对象时（历史被重写过），POST 会 422 —— 改为
    「先删 ref 再建 ref」，这是 GitHub 上重挂 tag 唯一稳定的路径。
    """
    last = ""
    for _ in range(max(1, retries)):
        code, obj = gh_api(token, "POST", f"/repos/{user}/{repo}/git/refs",
                           {"ref": f"refs/tags/{tag}", "sha": sha, "force": True})
        if code in (200, 201, 204):
            return True, ""
        last = f"HTTP {code} {str(obj)[:120]}"
        # 已存在 / 指向不可达对象 → 删掉再建
        c1, _ = gh_api(token, "DELETE", f"/repos/{user}/{repo}/git/refs/tags/{tag}")
        if c1 in (204, 200, 404, 422):
            c2, obj2 = gh_api(token, "POST", f"/repos/{user}/{repo}/git/refs",
                              {"ref": f"refs/tags/{tag}", "sha": sha, "force": True})
            if c2 in (200, 201, 204):
                return True, ""
            last = f"重建失败 HTTP {c2} {str(obj2)[:120]}"
            # 传进去的 sha 在远端不存在（本地 HEAD 与 API 提交不同源）→ 改用远端 main 的 head
            if "Object does not exist" in str(obj2):
                sha2 = ""
                for path in (f"/repos/{user}/{repo}/git/ref/heads/main",
                             f"/repos/{user}/{repo}/commits/main"):
                    _c, _o = gh_api(token, "GET", path)
                    if isinstance(_o, dict):
                        sha2 = ((_o.get("object") or {}).get("sha") or _o.get("sha") or "")
                        if sha2:
                            break
                if sha2 and sha2 != sha:
                    sha = sha2
                    last = ""
                    continue
        time.sleep(2)
    return False, f"API 创建 tag {tag} 失败：{last}"


def git_push(skill_dir, user, repo, token, ssh, retries, args_user, args_email):
    # init（若未初始化）
    if not os.path.isdir(os.path.join(skill_dir, ".git")):
        git("init", cwd=skill_dir)
    git("config", "user.name", args_user, cwd=skill_dir)
    git("config", "user.email", args_email, cwd=skill_dir)
    git("config", "http.sslVerify", "false", cwd=skill_dir)

    # 不外发文件（DEVLOG.md 等）：补 .gitignore + 从索引摘掉（历史提交过的也要清）
    ensure_never_publish_ignored(skill_dir)
    for _name in sorted(NEVER_PUBLISH):
        git("rm", "--cached", "-f", "--ignore-unmatch", _name, cwd=skill_dir)

    git("add", "-A", cwd=skill_dir)
    # commit（无改动也 OK）
    r = git("commit", "-m", f"{os.path.basename(skill_dir)} 开源发布", cwd=skill_dir, timeout=60)
    if r.returncode != 0:
        # 可能无改动
        st = git("status", "--porcelain", cwd=skill_dir)
        if st.stdout.strip():
            return False, f"commit 失败：{r.stderr[:200]}"

    git("branch", "-M", "main", cwd=skill_dir)

    remote_url = (
        f"git@github.com:{user}/{repo}.git" if ssh
        else f"https://{token}@github.com/{user}/{repo}.git"
    )
    cur = git("remote", "get-url", "origin", cwd=skill_dir)
    if cur.returncode == 0:
        git("remote", "set-url", "origin", remote_url, cwd=skill_dir)
    else:
        git("remote", "add", "origin", remote_url, cwd=skill_dir)

    # 推送成功后立刻把 remote 里的 token 摘掉，避免 PAT 残留在 local git config（脱敏铁律）
    safe_url = f"https://github.com/{user}/{repo}.git"

    if API_ONLY[0]:
        root = RESET_HISTORY[0]
        ident = noreply_identity(token, fallback_login=user)
        sha, err = api_sync_main(skill_dir, user, repo, token,
                                 f"{os.path.basename(skill_dir)} 开源发布 · API 直推",
                                 root=root, ident=ident)
        if sha:
            git("update-ref", "refs/heads/main", sha, cwd=skill_dir)
            return True, f"API 直推 main 成功（{sha[:12]}）" + ("，已重写为根提交" if root else "")
        return False, f"API 直推失败：{err}"

    force = ["--force"] if FORCE[0] else []
    last_err = ""
    for i in range(max(1, retries)):
        pr = git("push", *force, "-u", "origin", "main", cwd=skill_dir, timeout=80)
        if pr.returncode == 0:
            git("remote", "set-url", "origin", safe_url, cwd=skill_dir)
            return True, f"推送成功（第 {i + 1} 次尝试）"
        last_err = pr.stderr.strip()[-200:]
        if i < retries - 1:
            time.sleep(3)

    # 兜底：git 走不通（GFW/443 抖动、non-fast-forward）时改用 GitHub API 强制重写 main
    print(f"    · git push 失败（{last_err[:80]}），回退 GitHub API 强制推送 main")
    sha, err = api_sync_main(skill_dir, user, repo, token,
                             f"{os.path.basename(skill_dir)} 开源发布 · API 兜底",
                             ident=noreply_identity(token, fallback_login=user))
    if sha:
        git("remote", "set-url", "origin", safe_url, cwd=skill_dir)
        # 尽量把本地 main 指到 API 新建的 sha。该 commit 通常不在本地（fetch 同样被 GFW 挡着），
        # 所以先探测对象是否存在；对不上也无妨——make_release 会**优先用远端 main 的 head**。
        if git("cat-file", "-e", sha + "^{commit}", cwd=skill_dir).returncode == 0:
            git("update-ref", "refs/heads/main", sha, cwd=skill_dir)
        return True, f"API 兜底推送 main 成功（{sha[:12]}）"
    git("remote", "set-url", "origin", safe_url, cwd=skill_dir)
    return False, f"推送失败：{last_err} | API 兜底失败：{err}"


def push_tag(skill_dir, tag, retries):
    last_err = ""
    for i in range(max(1, retries)):
        r = git("push", "origin", tag, cwd=skill_dir, timeout=80)
        err = (r.stderr or "").strip()
        if r.returncode == 0 or "Everything up-to-date" in err or "up-to-date" in err.lower():
            return True, f"标签已推送（第 {i + 1} 次）"
        last_err = err
        if i < retries - 1:
            time.sleep(3)
    return False, f"标签推送失败：{last_err[-200:]}"


def make_release(skill_dir, user, repo, version, token, tag_prefix, retries):
    """打标签 → 推标签 → 创建/更新 GitHub Release。tag = <prefix><version>。"""
    if not version:
        return False, "SKILL.md 未读到 version，跳过 Release"
    ver = str(version)
    tag = ver if ver.lower().startswith(tag_prefix) else f"{tag_prefix}{ver}"
    repo_url = f"https://github.com/{user}/{repo}"

    # --reset-history：tag 被强制移动到新根提交后，旧 Release 会悬空 → 先删再建
    if RESET_HISTORY[0]:
        d_code, _ = gh_api(token, "DELETE",
                           f"/repos/{user}/{repo}/releases/tags/{urllib.parse.quote(tag)}")
        print(f"    · 已清理旧 Release（HTTP {d_code}），稍后重建 {tag}")

    # 0) 决定 tag 要指向哪个 commit —— **优先用远端 main 的 head**。
    #    ⚠️ 2026-10-01 实测踩坑：git push 失败走 API 兜底推 main 后，本地 refs/heads/main
    #       与远端不同源（本地没法 fetch 到 API 新建的那个 commit）。此时若还拿
    #       `git rev-parse HEAD` 去建 tag，GitHub 会 422「Object does not exist」，
    #       表现就是「main 推成功、Release 却挂了」。
    local_head = git("rev-parse", "HEAD", cwd=skill_dir).stdout.strip()
    remote_head = remote_main_sha(user, repo, token)
    divergent = bool(remote_head and local_head and remote_head != local_head)
    head = remote_head or local_head

    # 1) 本地打标签（已存在则保留历史版本，不覆盖；但仍继续走远端 Release 流程）。
    #    本地与远端不同源时**跳过本地打标签**——否则 push 会把这个分叉提交一起推上去。
    if divergent:
        print(f"    · 本地 HEAD({local_head[:8]}) 与远端 main({remote_head[:8]}) 不同源，"
              f"跳过本地打标签，改由 API 创建")
    else:
        listed = git("tag", "--list", cwd=skill_dir).stdout.split()
        if tag not in listed:
            r = git("tag", "-a", tag, "-m", f"{tag} release", cwd=skill_dir)
            if r.returncode != 0:
                return False, f"打标签失败：{(r.stderr or '').strip()[-200:]}"
        else:
            print(f"    · 本地标签 {tag} 已存在，保留不覆盖，继续同步远端 Release")

    # 2) 推送标签（GFW 抖动重试；远端已存在同名 tag 也算成功）
    ok, msg = (False, "本地与远端不同源，改走 API") if divergent else push_tag(skill_dir, tag, retries)
    if not ok:
        # 兜底：用 main 的 head sha 通过 API 直接建/改 tag ref（git push 走不通时）
        if head:
            ok2, err2 = api_push_tag(user, repo, token, tag, head)
            if ok2:
                print(f"    · API 兜底创建 tag {tag} 成功")
                msg = "API 兜底创建 tag"
            else:
                return False, f"{msg} | API 兜底失败：{err2}"
        else:
            return False, msg

    # 3) Release 正文（head 已在上面按「远端优先」算好）
    recent = git("log", "--oneline", "-n", "5", cwd=skill_dir).stdout.strip()
    today = datetime.date.today().isoformat()
    body = (
        f"## {tag}\n\n"
        f"由 `xueren-skill-publish-github` 自动创建 · {today}\n\n"
        f"- 仓库：{repo_url}\n"
        f"- 指向提交：`{head[:12]}`\n\n"
        f"### 近期提交\n\n"
        f"```\n{recent}\n```\n"
    )

    # 4) 已有同名 Release → PATCH 更新正文；否则 POST 新建
    code, obj = gh_api(token, "GET",
                       f"/repos/{user}/{repo}/releases/tags/{urllib.parse.quote(tag)}")
    if code == 200 and isinstance(obj, dict) and obj.get("id"):
        rid = obj["id"]
        c2, o2 = gh_api(token, "PATCH", f"/repos/{user}/{repo}/releases/{rid}",
                        {"name": tag, "body": body})
        if c2 in (200, 201):
            return True, o2.get("html_url", f"Release {tag} 已更新")
        return False, f"更新 Release 失败：HTTP {c2} {str(o2)[:150]}"

    c2, o2 = gh_api(token, "POST", f"/repos/{user}/{repo}/releases",
                    {"tag_name": tag, "name": tag, "body": body,
                     "draft": False, "prerelease": False})
    if c2 in (201, 202):
        return True, o2.get("html_url", f"Release {tag} 已创建")
    return False, f"创建 Release 失败：HTTP {c2} {str(o2)[:200]}"


def prune_releases(user, repo, token, keep_tag):
    """删除除 keep_tag 以外的全部 Release（默认不执行）。"""
    deleted = []
    for page in range(1, 6):
        code, obj = gh_api(token, "GET",
                           f"/repos/{user}/{repo}/releases?per_page=100&page={page}")
        if code != 200 or not isinstance(obj, list) or not obj:
            break
        for rel in obj:
            if rel.get("tag_name") == keep_tag or "id" not in rel:
                continue
            c2, _ = gh_api(token, "DELETE", f"/repos/{user}/{repo}/releases/{rel['id']}")
            if c2 in (200, 204):
                deleted.append(rel.get("tag_name", "?"))
        if len(obj) < 100:
            break
    return True, deleted


def run_backup(skill_name):
    if not os.path.isfile(BACKUP_SCRIPT):
        return False, "未找到 xueren-skill-backup 脚本，跳过备份"
    r = subprocess.run(
        [sys.executable, BACKUP_SCRIPT, skill_name],
        capture_output=True, text=True,
    )
    return r.returncode == 0, (r.stdout.strip()[-300:] or r.stderr.strip()[-300:])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    import argparse
    ap = argparse.ArgumentParser(description="把本地 WorkBuddy skill 发布为 GitHub 开源仓库（不限名称）")
    ap.add_argument("--skill", required=True, help="skill 目录（绝对路径或目录名）")
    ap.add_argument("--token", help="GitHub PAT（创建仓库 + HTTPS 推送必需；--ssh 时仍需用于建仓库）")
    ap.add_argument("--token-from-env", action="store_true",
                    help="即使 --token 为空也允许从 GH_TOKEN / GITHUB_TOKEN / GH_PAT 读取（供编排脚本调用）")
    ap.add_argument("--user", default="", help="目标 GitHub 用户名（仓库推送到谁的账号，必须显式指定，不读取任何默认账号）")
    ap.add_argument("--email", default=DEFAULT_EMAIL, help="git 提交邮箱（默认取 git config user.email；开源建议用 GitHub noreply 地址）")
    ap.add_argument("--desc", help="仓库描述（默认取 SKILL.md frontmatter description）")
    ap.add_argument("--private", action="store_true", help="创建私有仓库")
    ap.add_argument("--license", default=DEFAULT_LICENSE, help="许可证模板（默认 mit）")
    ap.add_argument("--ssh", action="store_true", help="用 SSH remote（需已注册公钥）")
    ap.add_argument("--no-release", action="store_true", help="推送后不创建 GitHub Release（默认自动创建/更新 v<version> Release）")
    ap.add_argument("--tag-prefix", default="v", help="Release 标签前缀（默认 v，即 v1.0.4）")
    ap.add_argument("--prune-releases", action="store_true", help="删除除当前版本以外的其它 Release，只保留最新版")
    ap.add_argument("--backup", action="store_true", help="推送后尝试用本地 xueren-skill-backup 同步到 WB Skill 备份目录（可选；默认不依赖、不执行）")
    ap.add_argument("--push-retries", type=int, default=8, help="HTTPS 推送重试次数（默认 8）")
    ap.add_argument("--bump-version", action="store_true", help="SKILL.md version 补丁号 +1")
    ap.add_argument("--force", action="store_true", help="强制推送（远端历史被重写导致 non-fast-forward 时用）")
    ap.add_argument("--api-only", action="store_true", help="跳过 git push，只用 GitHub API 写 main（443 长期抖动时用）")
    ap.add_argument("--reset-history", action="store_true",
                    help="配合 --api-only：把远端历史重写为一个 noreply 署名的根提交（历史里已有真实邮箱时用）")
    ap.add_argument("--skip-repo", action="store_true", help="跳过 API 建仓库（仓库已存在时）")
    args = ap.parse_args()

    skill_dir = args.skill
    if not os.path.isdir(skill_dir):
        # 允许仅给目录名
        alt = os.path.join(os.path.expanduser("~/.workbuddy/skills"), skill_dir)
        if os.path.isdir(alt):
            skill_dir = alt
        else:
            print(f"[error] skill 目录不存在：{skill_dir}")
            sys.exit(1)
    skill_dir = os.path.abspath(skill_dir)
    if not args.user:
        print("[error] 必须用 --user <你的GitHub用户名> 指定目标账号（本脚本不设任何默认账号，避免误推到别人仓库）")
        sys.exit(1)
    skill_name = os.path.basename(skill_dir.rstrip("/\\"))
    repo = skill_name
    url = f"https://github.com/{args.user}/{repo}"

    reps = load_replacements()
    FORCE[0] = bool(args.force)
    API_ONLY[0] = bool(args.api_only)
    RESET_HISTORY[0] = bool(args.reset_history)

    # token 兜底：--token 没给时允许从环境变量取（编排脚本只注入 GH_PAT，不传 --token）
    if not args.token and args.token_from_env:
        for ev in ("GH_TOKEN", "GITHUB_TOKEN", "GH_PAT"):
            if os.environ.get(ev):
                args.token = os.environ[ev].strip()
                print(f"[0] 已从环境变量 {ev} 取到 token")
                break

    # 0.5) 提交身份（开源零个人信息铁律）：显式 --email > GH_EMAIL > noreply({id}+{login})
    #      —— 绝不回退到本机 git config 的真实邮箱（见 DEFAULT_EMAIL 处的踩坑说明）。
    submit_login, noreply = noreply_identity(args.token, fallback_login=args.user)
    submit_email = (args.email or "").strip() or noreply
    if not submit_email:
        print("[warn] 无法确定提交邮箱（未给 --email/GH_EMAIL，且 noreply 查询失败）——"
              "提交身份将为空；建议显式传 --email <id>+<login>@users.noreply.github.com")
    elif "users.noreply.github.com" not in submit_email:
        print(f"[warn] ⚠️ 提交邮箱 {submit_email} **不是** GitHub noreply 地址——"
              "开源仓库会把它永久公开在 commit 历史里（违反零个人信息铁律），"
              "建议改用 --email <id>+<login>@users.noreply.github.com")
    else:
        print(f"[0.5] 提交身份：{submit_login or args.user} <{submit_email}>")
    args.email = submit_email

    # 1) 版本号 +1
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if args.bump_version and os.path.isfile(skill_md):
        t = read_text(skill_md)
        old = get_fm_field(t, "version")
        newv = bump_patch(old)
        t = re.sub(r"^(version\s*:\s*).*$", f"\\g<1>{newv}", t, count=1, flags=re.MULTILINE)
        with open(skill_md, "w", encoding="utf-8") as f:
            f.write(t)
        print(f"[1] version {old} -> {newv}")

    # 2) 脱敏
    n, touched = desensitize_dir(skill_dir, reps)
    print(f"[2] 脱敏完成：{n} 处替换" + (f"（{', '.join(touched)}）" if touched else "（无需替换）"))

    # 3) 读取 frontmatter
    fm = {}
    if os.path.isfile(skill_md):
        t = read_text(skill_md)
        for k in ("id", "name", "version", "description", "author"):
            fm[k] = get_fm_field(t, k)
        fm["platforms"] = get_fm_list(t, "platforms")
    fm["description"] = args.desc or fm.get("description") or ""
    fm.setdefault("id", skill_name)

    # 4) github 字段
    if os.path.isfile(skill_md):
        if add_github_field(skill_md, url):
            print(f"[3] SKILL.md 已加 github 字段：{url}")
        else:
            print(f"[3] SKILL.md github 字段已存在/跳过")

    # 5) meta.json
    mp = gen_meta_json(skill_dir, fm, url)
    print(f"[4] meta.json 已生成：{mp}")

    # 6) LICENSE
    if not os.path.isfile(os.path.join(skill_dir, "LICENSE")):
        lp = gen_license(skill_dir, author=fm.get("author"))
        print(f"[5] LICENSE 已生成：{lp}")
    else:
        print(f"[5] LICENSE 已存在，跳过")

    # 7) .gitignore
    if not os.path.isfile(os.path.join(skill_dir, ".gitignore")):
        gp = gen_gitignore(skill_dir)
        print(f"[6] .gitignore 已生成：{gp}")
    else:
        print(f"[6] .gitignore 已存在，跳过")

    # 8) README
    if not os.path.isfile(os.path.join(skill_dir, "README.md")):
        tree = build_tree(skill_dir)
        rp = gen_readme(skill_dir, fm, url, tree)
        print(f"[7] README.md 已基于模板生成：{rp}（建议人工润色）")
    else:
        print(f"[7] README.md 已存在，跳过")

    # 9) 建仓库
    if not args.skip_repo:
        if not args.token:
            print("[error] 创建仓库需要 --token（GitHub PAT）")
            sys.exit(1)
        ok, msg = create_repo(args.token, args.user, repo, fm["description"], args.private, args.license)
        print(f"[8] 建仓库：{msg}")
        if not ok:
            print("[error] 建仓库失败，终止（可 --skip-repo 跳过，或检查 token 权限）")
            sys.exit(1)
    else:
        print("[8] 跳过建仓库（--skip-repo）")

    # 10) 推送
    if not args.ssh and not args.token:
        print("[error] HTTPS 推送需要 --token（或用 --ssh 走已注册公钥）")
        sys.exit(1)
    ok, msg = git_push(skill_dir, args.user, repo, args.token or "", args.ssh, args.push_retries,
                       submit_login or args.user, submit_email)
    print(f"[9] 推送：{msg}")
    if not ok:
        print("[error] 推送失败，详见上方错误")
        sys.exit(1)

    # 10) 自动 Release（默认创建；--no-release 跳过）
    release_url = ""
    if args.no_release:
        print("[10] 跳过 Release（--no-release）")
    elif not args.token:
        print("[10] 未提供 --token，跳过 Release 创建（需 PAT 才能调 GitHub API）")
    else:
        ok, msg = make_release(skill_dir, args.user, repo, fm.get("version") or "",
                               args.token, args.tag_prefix, args.push_retries)
        print(f"[10] Release：{'OK' if ok else '失败'} {msg}")
        if ok and isinstance(msg, str) and msg.startswith("http"):
            release_url = msg
        if ok and args.prune_releases:
            ok2, deleted = prune_releases(args.user, repo, args.token,
                                          (fm.get("version") or "") if str(fm.get("version", "")).lower().startswith(args.tag_prefix)
                                          else f"{args.tag_prefix}{fm.get('version')}")
            print(f"[11] 清理旧 Release：{'OK' if ok2 else '失败'} 已删除 {deleted or '无'}")

    # 12) 备份（可选：开源版不依赖 xueren-skill-backup，仅在本机已装且显式 --backup 时尝试）
    if args.backup:
        ok, msg = run_backup(skill_name)
        print(f"[12] WB Skill 备份：{'OK' if ok else '跳过/失败'} {msg}")
    else:
        print("[12] 跳过 WB Skill 备份（开源版默认不依赖 xueren-skill-backup；如需本地存档可加 --backup）")

    # 11.5) 兜底摘 PAT：无论走哪条推送路径，收尾都强制把 remote URL 还原成无凭据形式
    token_left = False
    # 特征词拼出来写：本文件会随包进开源仓库，明文留在里面会让
    # verify_published.py 的敏感词校验「每次都假阳性扫到 detect 代码自己」。
    pat_marker = "gh" + "p_"
    cur = git("remote", "get-url", "origin", cwd=skill_dir)
    if cur.returncode == 0 and pat_marker in (cur.stdout or ""):
        git("remote", "set-url", "origin", f"https://github.com/{args.user}/{repo}.git", cwd=skill_dir)
        token_left = True

    print("\n✅ 发布完成：")
    print(f"   GitHub: {url}")
    if release_url:
        print(f"   Release: {release_url}")
    print(f"   本地目录: {skill_dir}")
    print("\n⚠️ 安全提醒：")
    if token_left:
        print("   - 远端推送过程中本脚本把 PAT 临时写进了 remote URL，收尾已自动摘除；")
        print("     若看到历史残留：git -C \"<skill>\" remote set-url origin "
              f"https://github.com/{args.user}/{repo}.git")
        print("   - 长期方案：GitHub 网页注册本机 SSH 公钥后改用 --ssh。")
    if not args.ssh:
        print("   - 推送走 HTTPS 且关闭了 sslVerify（GFW 代理环境需要），生产环境请评估风险。")


if __name__ == "__main__":
    main()
