# {name}

> {description}

本 skill 遵循通用 SKILL 规范（`SKILL.md` + `meta.json` + 资源目录），可装入任何支持 skill 的 AI 工具（WorkBuddy、Claude Code、Cursor 等）。

## 安装（作为 AI 工具的 skill）

1. 克隆仓库：

   ```bash
   git clone {repo}.git
   ```

2. 把目录放进你的 AI 工具 skills 目录（以 WorkBuddy 为例）：

   ```bash
   # Windows
   xcopy /E /I {name} %USERPROFILE%\.workbuddy\skills\{name}
   # macOS / Linux
   cp -r {name} ~/.workbuddy/skills/
   ```

3. 如有依赖，进入目录安装：

   ```bash
   cd {name} && npm install   # 或 pip install -r requirements.txt（视 skill 而定）
   ```

## 使用方式

装好后使用就是普通的对话形式——在 AI 工具里说出对应意图，它会按 `SKILL.md` 的流程引导你完成。详细流程见 `SKILL.md`。

## 项目结构

```
{tree}
```

## License

[MIT](./LICENSE) © {year} {author}

---

GitHub: {repo}
