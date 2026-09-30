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
  - --email 建议显式传 GitHub noreply 地址（{numeric-id}+{login}@users.noreply.github.com）；
    默认取本机 git config user.email，真实邮箱会随 commit 历史永久公开。
  - 脱敏规则用户名用正则通配；通用规则与本机私有规则分离
    （references/sensitive_paths.json 公开 / sensitive_paths.local.json 由 .gitignore 排除）。
  - LICENSE 与 README 的署名取 SKILL.md 的 author 字段，模板内无硬编码人名。

用法示例（--user 必须显式指定目标 GitHub 账号，脚本不设任何默认账号）：
  python publish_skill.py --skill "C:/Users/<you>/.workbuddy/skills/my-skill" \
         --user <your-github-user> --email <your-noreply-email> --token ghp_xxx --bump-version
  python publish_skill.py --skill my-skill --user <your-github-user> \
         --email <your-noreply-email> --token ghp_xxx --ssh --backup
"""
import os
import sys
import re
import json
import time
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
DEFAULT_EMAIL = os.environ.get("GH_EMAIL") or _git_config("user.email")
DEFAULT_AUTHOR = "Author"
DEFAULT_LICENSE = "MIT"
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
    return subprocess.run(
        ["git", "-c", "http.sslVerify=false", *args],
        cwd=cwd, timeout=timeout,
        capture_output=True, text=True,
    )


def git_push(skill_dir, user, repo, token, ssh, retries, args_user, args_email):
    # init（若未初始化）
    if not os.path.isdir(os.path.join(skill_dir, ".git")):
        git("init", cwd=skill_dir)
    git("config", "user.name", args_user, cwd=skill_dir)
    git("config", "user.email", args_email, cwd=skill_dir)
    git("config", "http.sslVerify", "false", cwd=skill_dir)

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

    last_err = ""
    for i in range(max(1, retries)):
        pr = git("push", "-u", "origin", "main", cwd=skill_dir, timeout=80)
        if pr.returncode == 0:
            git("remote", "set-url", "origin", safe_url, cwd=skill_dir)
            return True, f"推送成功（第 {i + 1} 次尝试）"
        last_err = pr.stderr.strip()[-200:]
        if i < retries - 1:
            time.sleep(3)
    git("remote", "set-url", "origin", safe_url, cwd=skill_dir)
    return False, f"推送失败：{last_err}"


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

    # 1) 本地打标签（已存在则保留历史版本，不覆盖；但仍继续走远端 Release 流程）
    listed = git("tag", "--list", cwd=skill_dir).stdout.split()
    if tag not in listed:
        r = git("tag", "-a", tag, "-m", f"{tag} release", cwd=skill_dir)
        if r.returncode != 0:
            return False, f"打标签失败：{(r.stderr or '').strip()[-200:]}"
    else:
        print(f"    · 本地标签 {tag} 已存在，保留不覆盖，继续同步远端 Release")

    # 2) 推送标签（GFW 抖动重试；远端已存在同名 tag 也算成功）
    ok, msg = push_tag(skill_dir, tag, retries)
    if not ok:
        return False, msg

    # 3) Release 正文
    head = git("rev-parse", "HEAD", cwd=skill_dir).stdout.strip()
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
                       _git_config("user.name") or args.user, args.email or _git_config("user.email"))
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

    print("\n✅ 发布完成：")
    print(f"   GitHub: {url}")
    if release_url:
        print(f"   Release: {release_url}")
    print(f"   本地目录: {skill_dir}")
    print("\n⚠️ 安全提醒：")
    if not args.ssh:
        print("   - PAT 已嵌入本仓库 .git/config 的 remote URL，建议到 GitHub 吊销/轮换此 token。")
        print("   - 推荐在 GitHub 网页注册本机 SSH 公钥（~/.ssh/id_rsa.pub）后改用：")
        print(f"       git -C \"{skill_dir}\" remote set-url origin git@github.com:{args.user}/{repo}.git")
    print("   - 推送走 HTTPS 且关闭了 sslVerify（GFW 代理环境需要），生产环境请评估风险。")


if __name__ == "__main__":
    main()
