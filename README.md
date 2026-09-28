# 雪人老师·Skill发布到GitHub

> 把本地**任意** WorkBuddy skill 一键发布为 GitHub 开源仓库（不限名称与前缀）：自动脱敏（本机路径→通用占位）、加 `github` 字段、生成 `meta.json` / `LICENSE` / `.gitignore` / `README`、API 建仓库、PAT 走 HTTPS 重试推送（抗 GFW）、推送后自动创建/更新 GitHub Release（`v<version>` 标签）。脱敏/建仓库/推送/Release 全部自包含、**零外部依赖**；本地备份为可选增强（需本机另装 `xueren-skill-backup` 且传 `--backup`，默认不触发）。

本 skill 遵循通用 SKILL 规范（`SKILL.md` + `meta.json` + 资源目录），可装入任何支持 skill 的 AI 工具（WorkBuddy、Claude Code、Cursor 等）。

## 特性

- **脱敏安全**：发布前自动把本机绝对路径（用户主目录、`WB Skill` 备份目录）替换为通用占位，杜绝泄漏。
- **合规交付物**：自动补齐开源仓库四件套（`meta.json` / `LICENSE` / `.gitignore` / `README.md`）+ `SKILL.md` 的 `github:` 字段。
- **抗 GFW**：建仓库走可靠的 `api.github.com`；推送走 HTTPS + PAT 重试循环（`github.com:443` 在国内间歇性被墙）。
- **自动 Release**：推送成功后自动打 `v<version>` 标签并创建/更新 Release（正文含近期提交摘要，幂等）；`--no-release` 关闭，`--prune-releases` 只保留最新版。
- **零外部依赖**：脱敏 / 建仓库 / 推送 / Release 全部自包含，不装任何其它 skill 也能跑通；本地备份是可选增强（本机另装 `xueren-skill-backup` 且传 `--backup` 时才触发，缺失不报错）。

## 安装（作为 AI 工具的 skill）

1. 克隆仓库：

   ```bash
   git clone https://github.com/JackieZheng/xueren-skill-publish-github.git
   ```

2. 把目录放进你的 AI 工具 skills 目录（以 WorkBuddy 为例）：

   ```bash
   # Windows
   xcopy /E /I xueren-skill-publish-github %USERPROFILE%\.workbuddy\skills\xueren-skill-publish-github
   # macOS / Linux
   cp -r xueren-skill-publish-github ~/.workbuddy/skills/
   ```

3. 本 skill 仅依赖 Python 标准库，无需额外安装。

## 使用方式

### 最简发布（HTTPS + PAT 重试推送 + 版本 +1 + 自动 Release）

```bash
python "~/.workbuddy/skills/xueren-skill-publish-github/scripts/publish_skill.py" ^
  --skill "~/.workbuddy/skills/<skill-name>" ^
  --user <your-github-user> ^
  --email <your-noreply-email> ^
  --token <GITHUB_PAT> ^
  --bump-version
```

- **`--user` 必填**：脚本不设任何默认 GitHub 账号，不填直接报错退出——避免误把仓库推到不打算公开的账号下。
- **`--email` 建议显式传 GitHub noreply 地址**（`{numeric-id}+{login}@users.noreply.github.com`）：否则取本机 git config 的真实邮箱，会随 commit 历史永久公开。
- 仅给目录名也可（脚本自动补 `~/.workbuddy/skills/` 前缀）：`--skill <skill-name>`。
- SSH 通道（本机公钥已注册 GitHub 时，避免 PAT 入 `.git/config`）：追加 `--ssh`。
- 仓库已存在、只重新推送：`--skip-repo`。

### 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--skill` | 必填 | skill 目录（绝对路径或目录名） |
| `--user` | **必填** | 目标 GitHub 用户名（仓库 URL 前缀）。脚本**不设默认账号**，不填直接报错退出 |
| `--token` | 无 | GitHub PAT（建仓库 + HTTPS 推送必需；`--ssh` 时仍需用于建仓库） |
| `--email` | 本机 git config | git 提交邮箱；开源建议显式传 GitHub noreply 地址 |
| `--desc` | 取 frontmatter | 仓库描述 |
| `--private` | 关 | 创建私有仓库 |
| `--license` | `mit` | 许可证模板（生成 LICENSE 用） |
| `--ssh` | 关 | 用 `git@github.com:` remote（需已注册公钥） |
| `--no-release` | 关 | 推送后不创建 Release（默认自动创建/更新） |
| `--tag-prefix` | `v` | Release 标签前缀（`v1.0.4`；version 已带前缀则不重复加） |
| `--prune-releases` | 关 | 删除除当前版本以外的其它 Release，只保留最新版 |
| `--backup` | 关 | 推送后调用本机 `xueren-skill-backup` 同步到 WB Skill 备份目录（可选增强；脚本缺失则跳过，不影响发布） |
| `--push-retries` | 8 | HTTPS 推送重试次数（应对 GFW 抖动） |
| `--bump-version` | 关 | `SKILL.md` version 补丁号 +1 |
| `--skip-repo` | 关 | 跳过 API 建仓库（仓库已存在时） |

## 网络现实（实测）

- `api.github.com` 可达 → 建仓库走这里，稳。
- `github.com:443`（HTTPS git）国内间歇性被墙 → 用 HTTPS + PAT + 重试循环（默认 8 次，单次超时 80s）。
- `github.com:22`（SSH）TCP 可达，但本机公钥未注册 GitHub 前 `Permission denied`；注册后改 SSH 最稳，且 PAT 不入 `.git/config`。

## 脱敏规则（公私分离）

| 文件 | 可见性 | 用途 |
|------|--------|------|
| `references/sensitive_paths.json` | 随仓库公开 | 通用规则；用户名用 `[^\/]+` 通配，**不含任何具体人名** |
| `references/sensitive_paths.local.json` | `.gitignore` 排除 | 本机私有路径（额外磁盘、自定义目录），只在你的机器生效 |

加载顺序：脚本内置默认 → `sensitive_paths.json` → `sensitive_paths.local.json`。

## 项目结构

```
xueren-skill-publish-github/
├── SKILL.md
├── meta.json
├── README.md
├── LICENSE
├── .gitignore
├── scripts/
│   └── publish_skill.py
└── references/
    ├── sensitive_paths.json      # 通用规则（公开）
    ├── sensitive_paths.local.json # 本机私有（gitignore，不在此树）
    └── readme_template.md
```

## License

[MIT](./LICENSE) © 2026 雪人

---

GitHub: https://github.com/JackieZheng/xueren-skill-publish-github
