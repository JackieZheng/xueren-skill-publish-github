# xueren-skill-publish-github 版本演进记录

## v1.0.8（2026-09-30）

新增 --api-only / --reset-history / --force 三条推送路径 + GitHub API 兜底（绕开 443 抖动与 non-fast-forward）；API 提交显式带 noreply 身份、远端收尾强制摘 PAT

## v1.0.9（2026-09-30）

新增 --api-only / --reset-history / --force 三条推送路径 + GitHub API 兜底（绕开 443 抖动与 non-fast-forward）；API 提交显式带 noreply 身份、远端收尾强制摘 PAT

## v1.0.11（2026-09-30）

token 从环境变量兜底 + GH_USER/GH_EMAIL 编排传参（--token-from-env）

## v1.0.13（2026-09-30）

修正发布编排：去掉 --no-release，推完自动建/更新 Release（此前编排声称建 Release 却没建，导致版本漂移）
