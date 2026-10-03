---
id: xueren-skill-publish-github
name: 雪人老师·Skill发布到GitHub
title: 雪人老师·Skill发布到GitHub
description: 把本地任意 WorkBuddy skill 一键发布为 GitHub 开源仓库（不限 skill 名称与命名前缀）。自动完成脱敏（本机绝对路径替换为通用占位）、给 SKILL.md 加 github 字段、生成 meta.json / LICENSE / .gitignore / 基础 README、通过 GitHub API 建仓库、git 提交并用 PAT 走 HTTPS 重试推送（应对 GFW 抖动）、推送后自动创建/更新 GitHub Release（v<version> 标签，幂等）。本地备份到 WB Skill 为可选步骤（需本机另装 xueren-skill-backup，默认不触发，不影响发布）。用户提到"把 skill 发到 github""开源发布 skill""publish skill to github""推送 skill 到 github""发布 skill 到 github"时触发。不适用于：需要 GitHub Pages 站点发布的场景（走各自流程）。
slug: xueren-skill-publish-github
displayName: 雪人老师·Skill发布到GitHub
summary: 把本地任意 WorkBuddy skill 一键发布为 GitHub 开源仓库（不限 skill 名称与命名前缀）。
description_en: One-click publish WorkBuddy skills to GitHub (auto-scrub + release).
version: 1.0.18
author: 雪人
license: MIT
allowed-tools: ""
display_name: xueren-skill-publish-github
display_name_zh: 雪人老师·Skill发布到GitHub
trigger: ["把 skill 发到 github", "开源发布 skill", "publish skill to github", "推送 skill 到 github", "发布 skill 到 github"]
examples: "用户：把 my-awesome-skill 发布到我的 github 作为开源项目（脱敏） → 运行 scripts/publish_skill.py --skill my-awesome-skill --token <PAT> --bump-version，自动脱敏+建仓库+重试推送+自动 Release。"
platforms: [ima, WorkBuddy, QClaw]
github: https://github.com/JackieZheng/xueren-skill-publish-github
metadata:
  author: 雪人
  category: 工具
description_zh: 把本地任意 WorkBuddy skill 一键发布为 GitHub 开源仓库（不限 skill 名称与命名前缀）。自动完成脱敏（本机绝对路径替换为通用占位）、给 SKILL.md 加 github 字段、生成 meta.json / LICENSE / .gitignore / 基础 README、通过 GitHub API 建仓库、git 提交并用 PAT 走 HTTPS 重试推送（应对 GFW 抖动）、推送后自动创建/更新 GitHub Release（v<version> 标签，幂等）。本地备份到 WB Skill 为可选步骤（需本机另装 xueren-skill-backup，默认不触发，不影响发布）。用户提到"把 skill 发到 github""开源发布 skill""publish skill to github""推送 skill 到 github""发布 skill 到 github"时触发。不适用于：需要 GitHub Pages 站点发布的场景（走各自流程）。

---

# 雪人老师·Skill发布到GitHub

## 概述

把本地自建的任意 WorkBuddy skill 发布为 GitHub 开源仓库的一站式 skill（不限命名前缀）。把「易错/重复」
步骤全部固化进 `scripts/publish_skill.py`，LLM 只负责写 README 这类内容相关部分与最终核验。

**设计原则：开源仓库零个人信息。** 脚本不设任何默认 GitHub 账号、不硬编码任何邮箱/用户名/本地路径——
`--user` 强制必填，脱敏规则用正则通配用户名，版权人取 SKILL.md 的 `author` 字段。凡涉及身份的取值一律
来自调用方本机配置或命令行参数，脚本自身保持通用。

**核心价值**：
- **脱敏安全**：发布前自动把本机绝对路径（`WB Skill 备份目录`、用户主目录）替换为通用占位，杜绝泄漏。
- **合规交付物**：自动补齐开源仓库四件套（`meta.json` / `LICENSE` / `.gitignore` / `README.md`）+ `SKILL.md` 的 `github:` 字段。
- **抗 GFW**：建仓库走可靠的 `api.github.com`，推送走 HTTPS+PAT 重试循环（`github.com:443` 在国内间歇性被墙）。
- **自动 Release**：推送成功后自动打 `v<version>` 标签并创建/更新 GitHub Release（正文含提交摘要，幂等；`--no-release` 可关，`--prune-releases` 可只留最新版）。
- **零外部依赖**：脱敏 / 建仓库 / 推送 / Release 全部自包含，不依赖其它 skill 即可跑通；本地备份是可选增强（本机另装 `xueren-skill-backup` 且传 `--backup` 时才触发，缺失不报错）。

## 你的工作方式

1. **先准备 README** — 发布前确认目标 skill 有像样的 `README.md`（内容相关，脚本只兜底生成基础版）。用 `references/readme_template.md` 作骨架。
2. **要 PAT** — 让用户提供 GitHub PAT（需 `repo` 权限；`write:public_key` 非必需）。**明文 PAT 属敏感信息，用后必须提醒用户到 GitHub 吊销/轮换**。
3. **跑脚本** — 调用 `scripts/publish_skill.py` 完成脱敏→建仓库→推送→自动 Release 的机械流程（本地备份可选）。
4. **核验** — 打开仓库 URL 确认文件齐全、脱敏生效（搜索仓库内不应出现任何本机绝对路径，如用户主目录、WB Skill 备份目录的字面路径）。

## 执行流程

### Phase 1：发布前准备

- 确认目标 skill 的 `SKILL.md` 已符合模板（16 字段 + 7 章节）；若不符合先补齐。
- 写/润色 `README.md`（建议含：特性、安装、使用、项目结构、License、GitHub 链接）。可用 `references/readme_template.md` 填充。
- 确认 `version` 要 bump（开源发布算一次 release，惯例 +1 补丁号）；让脚本用 `--bump-version` 自动加，或手工 Edit。
- **索取 GitHub PAT**：`Settings → Developer settings → Personal access tokens → Fine-grained 或 classic`，勾 `repo`；拿到后仅在本次命令使用。

### Phase 2：核心执行（跑脚本）

最常用（HTTPS+PAT 重试推送 + 版本 +1 + 自动 Release）：

```bash
python "~/.workbuddy/skills/xueren-skill-publish-github/scripts/publish_skill.py" ^
  --skill "~/.workbuddy/skills/<skill-name>" ^
  --user <your-github-user> ^
  --token <GITHUB_PAT> ^
  --bump-version
```

> **`--user` 必填**：脚本不设任何默认 GitHub 账号，不填会直接报错退出——这是刻意设计，避免别人误把仓库
> 推到自己不打算公开的账号下。**提交邮箱默认就是 GitHub noreply 地址**
> （`{numeric-id}+{login}@users.noreply.github.com`，由 `--token` 查 `/user` 自动生成）；
> 显式传 `--email` 会覆盖它——**若传的不是 noreply 地址，脚本会打醒目告警**（真实邮箱会随 commit 历史永久公开）。

仅给目录名也可（脚本会自动补 `~/.workbuddy/skills/` 前缀）：

```bash
python "...\xueren-skill-publish-github\scripts\publish_skill.py" --skill <skill-name> --user <your-github-user> --token <PAT> --bump-version
```

SSH 通道（本机公钥已注册 GitHub 时，避免 PAT 入 `.git/config`）：

```bash
python "...\publish_skill.py" --skill <skill-name> --user <your-github-user> --token <PAT> --ssh
```

仓库已存在、只重新推送：

```bash
python "...\publish_skill.py" --skill <skill-name> --user <your-github-user> --token <PAT> --skip-repo
```

### Phase 3：核验与收尾

- 打开 `https://github.com/<your-github-user>/<skill-name>` 确认：`SKILL.md` / `meta.json` / `README.md` / `LICENSE` / `.gitignore` / 资源目录齐全。
- **Release 核验**：打开 `https://github.com/<your-github-user>/<skill-name>/releases` 确认存在 `v<version>` Release，且「Latest」标签指向它。
- **脱敏核验**：在仓库内搜索用户主目录 / WB Skill 备份目录的字面路径，应 0 命中；再搜一次 `git log --format='%ae %ce'` 确认 commit 邮箱是 noreply 而非真实邮箱。
- 到 GitHub 吊销本次 PAT（或告知用户去吊销）；若已注册 SSH 公钥，建议把 remote 改回 SSH：
  `git -C <skill-dir> remote set-url origin git@github.com:<your-github-user>/<skill-name>.git`
- 本地备份（可选）：本 skill **不依赖** `xueren-skill-backup`。若本机已装该 skill 且需要本地存档，加 `--backup` 触发同步；否则跳过，不影响发布结果。

## 配置与参数

`scripts/publish_skill.py` 参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--skill` | 必填 | skill 目录（绝对路径或目录名） |
| `--user` | **必填** | 目标 GitHub 用户名（仓库 URL 前缀）。脚本**不设默认账号**，不填直接报错退出，避免误推到不打算公开的账号 |
| `--token` | 无 | GitHub PAT（建仓库 + HTTPS 推送必需；`--ssh` 时仍需用于建仓库） |
| `--email` | **GitHub noreply 地址**（由 `--token` 查 `/user` 生成 `{id}+{login}@users.noreply.github.com`） | git 提交邮箱。**不要传本机真实邮箱**（会随 commit 历史永久公开）；传非 noreply 地址时脚本会告警。旧版本曾默认取本机 `git config user.email`，已废弃 |
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

**脱敏映射（两层，公私分离）**：
- `references/sensitive_paths.json` —— **通用规则**，随仓库公开发布。用户名用 `[^\/]+` 通配，不含任何具体人名。新增通用规则往这里加 `{pattern, replacement}`。
- `references/sensitive_paths.local.json` —— **本机私有**，已被 `.gitignore`（`*.local.json`）排除，不会随仓库发布。你自己的私有路径（额外磁盘、自定义目录）写这里。
- 加载顺序：脚本内置默认 → `sensitive_paths.json` → `sensitive_paths.local.json`，按序合并去重。

**网络现实**（2026-09-28 实测）：
- `api.github.com` 可达 → 建仓库走这里，稳。
- `github.com:443`（HTTPS git）国内间歇性被墙 → 用 HTTPS+PAT + 重试循环（默认 8 次，单次超时 80s）。
- `github.com:22`（SSH）TCP 可达，但本机公钥未注册 GitHub 前 `Permission denied`；注册后改 SSH 最稳，且 PAT 不入 `.git/config`。

## 资源目录

### scripts/
- `publish_skill.py`：核心发布脚本（脱敏 + frontmatter github 字段 + meta/LICENSE/.gitignore/README 生成 + API 建仓库 + git 重试推送 + 自动 Release；本地备份为可选）。

### references/
- `sensitive_paths.json`：脱敏路径映射（**通用规则，随仓库发布**；用户名正则通配，不含任何具体人名）。
- `sensitive_paths.local.json`：脱敏路径映射的**本机私有补充**（`.gitignore` 排除，不随仓库发布）。
- **tag 指向远端 main**：git push 失败走 API 兜底后，本地 ref 与远端不同源，脚本会优先用**远端 main 的 head** 建 tag，并跳过本地打标签，避免 `422 Object does not exist`。
- **`DEVLOG.md`（开发日志）—— 不随仓库发布**（用户约定，2026-10-01）：脚本会自动把它补进 `.gitignore` 并 `git rm --cached` 从索引摘掉；走 API 直推/兜底路径时同样跳过该文件，并额外下发 `sha=None` 的删除条目，把远端**历史已提交**的 `DEVLOG.md` 一并清掉。
- `readme_template.md`：README 骨架模板（`{name}` / `{description}` / `{repo}` / `{tree}` / `{year}` / `{author}` 占位）。

### assets/
（无）

### data/
（无）

### log/
（无）

## 资源固化与自包含（强制）

**凡与本 skill 强相关的资源，一律固化进 skill 目录内**，使 skill **自包含、可独立运行**。适用于 skill 的**创建、修改、更新全过程**，是长期标准。

1. **判断口径**：没有它 skill 就跑不出正确结果 → 强相关，必须固化。典型：发布脚本（`scripts/publish_skill.py`）、脱敏映射（`references/sensitive_paths.json`）、README 模板（`references/readme_template.md`）。
2. **放置位置**：脚本 → `scripts/`；配置/模板 → `references/`。
3. **取资源顺序固定**：`skill 内 references/scripts → 外部路径回退`。脚本内 `BACKUP_SCRIPT` 指向本机 `~/.workbuddy/skills/xueren-skill-backup/scripts/sync_backups.py`，属于**可选**的外部 skill——找不到即静默跳过，不是本 skill 的运行依赖。
4. **不固化的例外**：① 用户凭据（GitHub PAT）——**绝不写入 skill**，只作命令参数临时使用；② 可再生的仓库 `.git` —— 不固化进备份（同步时排除）。
5. **更新即同步**：每次修改本 skill 后**自动重新封装**到目标位置（GitHub 仓库；本机启用 `xueren-skill-backup` 时可追加同步本地备份，**排除 `__pycache__` / `.git` / 凭据**），无需用户另外吩咐。
6. **交付前自检**：假设把外部 `xueren-skill-backup` 路径改名，本 skill 还能跑通吗？能（脱敏/建仓库/推送全部自包含，备份同步失败也只影响最后一步并告警）。

## 注意事项

- **🔒 开源零个人信息（铁律，不可触碰）**：开源仓库里**不得出现任何个人信息或指向特定账号的设置**——
  ① 脚本不硬编码任何 GitHub 账号/邮箱/用户名/本地绝对路径；`--user` 强制必填，不填报错退出；
  ② 脱敏规则用 `[^\/]+` 通配用户名，通用规则与本机私有规则分离（`sensitive_paths.json` / `sensitive_paths.local.json`）；
  ③ 生成物（LICENSE / README）的版权人与署名取自 SKILL.md 的 `author` 字段，模板内不留任何硬编码人名；
  ④ 凡要写进脚本的默认值，默认是「无」而不是「我的」；⑤ 发布前必查 commit 历史邮箱（`git log --format='%ae %ce'`），
  真实邮箱必须改成 GitHub noreply 地址。这一条对所有开源交付物长期生效，不因赶时间而省略。
- **⚠️ 提交身份两处都别踩（2026-10-02 实测泄漏过）**：① 脚本内**不得**把 `git config user.email`（本机全局＝真实邮箱）
  当默认提交邮箱——默认必须走 `noreply_identity()`（`{id}+{login}@users.noreply.github.com`）；
  ② 泄漏后的净化要**三处一起清**：远端 `--api-only --reset-history` 重写根提交、本地重建 commit 与 tag、并检查
  `refs/remotes/origin/*` 是否还指向污染 commit（gc 清不掉 = 一定还有 ref 或 reflog 在引用，先 `for-each-ref` 找）。
- **⚠️ `git()` 必须捕获 `TimeoutExpired`**（2026-10-02 实测炸过）：GFW 下 `git push origin <tag>` 超时会让整个
  发布流程 traceback 中断（main 推成功、tag/Release 全丢）。超时要转成 `CompletedProcess(124)`，
  交给重试与 API 兜底处理。
- **脱敏是硬约束**：发布到公网前必须脱敏；脚本默认全量扫描文本文件替换本机路径，但仍需在 Phase 3 搜索核验 0 命中。
- **PAT 安全**：明文 PAT 只在命令中使用，绝写入 skill 文件；用完务必提醒用户到 GitHub 吊销/轮换。PAT 会进 `.git/config` 的 remote URL，吊销后该 URL 即失效（无害），但建议改用 SSH。
- **version 必 +1**：开源发布算 release，惯例 bump 补丁号；用 `--bump-version` 或手工 Edit。
- **README 内容为王**：脚本只兜底生成基础 README；优质 README（特性/安装/使用/结构）由 LLM 按模板手写，更利开源传播。
- **GFW 重试**：HTTPS 推送与标签推送默认 8 次重试；仍全失败多为网络持续被墙，建议换时段或改用 SSH（注册公钥后）。
- **Release 幂等**：同 tag 的 Release 已存在时脚本 PATCH 更新正文而不是重建；本地已有同名 tag 则**不覆盖**（保护历史版本），需替换请先 `git tag -d <tag>`。Release 失败不会阻断已完成的推送（只打印失败原因）。
- **与 xueren-skill-backup 关系**：本 skill **不依赖**它。二者互补——一个管"出公网"，一个管"留本地存档"；仅当本机另装该 skill 且传 `--backup` 时才会串联调用。
- **每次修改本 SKILL.md，`version` 必须 +1**，并推送到 GitHub 仓库（本机启用 `--backup` 时另同步本地备份）。
