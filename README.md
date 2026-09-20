# MewCode — 个人编码助手 / Terminal AI Coding Assistant

一个基于 **Python + Textual** 的终端 AI 编码助手。MewCode 运行在你的终端里，以 TUI（文本用户界面）为核心，内置多模型接入、权限控制、技能（Skill）、记忆（Memory）、团队协作（Teams）、MCP 工具扩展等能力，帮助你在命令行中高效地完成编码、审查、重构等任务。

> MewCode 强调 **人在回路（human-in-the-loop）**：默认权限模式下，写文件与执行命令都会先征求你的同意，把控制权始终留在你手里。

---

## ✨ 特性

### 多模型 / 多供应商
- 支持 `anthropic`、`openai`、`openai-compat` 三种协议。
- `openai-compat` 可接入 DeepSeek、Kimi、GLM、MiniMax 等任意 OpenAI 兼容服务。
- 自动从 provider 的 `/v1/models` 端点拉取模型的 **context window**，并内置「模型名 → 上下文窗口」映射表，失败时回退到保守默认值。

### 权限体系
- 四种权限模式：`default` / `acceptEdits` / `plan` / `bypassPermissions`。
  - **default**：读自动放行，写文件与执行命令需确认。
  - **acceptEdits**：写文件自动放行，命令仍需确认。
  - **plan**：只读规划模式。
  - **bypassPermissions**：全自动放行。
- 危险命令检测（`DangerousCommandDetector`）、路径沙箱（`PathSandbox`）、基于规则的权限引擎（支持全局 / 项目 / 本地三层规则）。
- 可选的 **OS 级沙箱**（Linux `bwrap` / macOS `seatbelt`）。

### 团队协作（Teams）
- 支持多 Agent 并行协作：Lead 调度，队员执行。
- 三种后端：`in-process`、`tmux`、`iTerm2`。
- 队员邮箱、共享任务板、进度追踪、`coordinator mode`（Lead 只调度不写码）。

### 技能（Skill）
- 三层技能来源：项目级（`.mewcode/skills`）、用户级（`~/.mewcode/skills`）、内置技能。
- 技能可用 `/skill` 命令安装、加载与管理，内置 `frontend-design`、`skill-creator` 示例。

### 记忆（Memory）
- 自动记忆、记忆合并/整合（consolidation）、按需召回（recall）、会话级指令（instructions）。

### 工具扩展
- **MCP（Model Context Protocol）** 支持：stdio 子进程 与 Streamable HTTP（2025-03-26 spec）两种传输。
- 工具检索（`ToolSearch`）：根据上下文窗口自动决定工具的加载策略。

### Hooks 事件系统
- 会话 / 轮次 / 工具 / 消息 / 系统 五个层级的生命周期事件钩子，可自定义执行器。

### 工程化能力
- **Worktree** 管理：Git worktree 的创建、切换、清理。
- **Rewind**：文件历史回溯到任意检查点。
- **Crash 恢复**：异常落盘 + 崩溃日志，可查看上一次运行现场。
- 上下文压缩（`/compact`）、流式/批处理输出。
- 非交互模式 + `stream-json` 输出，便于脚本化与集成。
- **Remote 模式**：WebSocket 桥接，在浏览器中远程控制 Agent。

---

## 📦 安装

要求 **Python ≥ 3.11**，推荐使用 [uv](https://github.com/astral-sh/uv) 管理环境。

```bash
# 克隆仓库
git clone <your-repo-url>
cd mewcode-python

# 安装依赖（项目使用 uv + hatchling）
uv sync
```

或直接以可编辑方式安装为命令行工具：

```bash
uv pip install -e .
```

## ⚙️ 配置

首次使用前创建配置文件。参考模板见 `.mewcode/config.yaml.example`。

配置文件按优先级合并（后者覆盖前者）：

1. `~/.mewcode/config.yaml` — 全局配置
2. `<project>/.mewcode/config.yaml` — 项目配置
3. `<project>/.mewcode/config.local.yaml` — 本地（不提交）配置

```bash
cp .mewcode/config.yaml.example .mewcode/config.yaml
# 编辑 API Key 与模型
```

最小配置示例：

```yaml
providers:
  - name: anthropic-official
    protocol: anthropic
    base_url: https://api.anthropic.com
    api_key: "${ANTHROPIC_API_KEY}"   # 也支持直接读取环境变量
    model: claude-sonnet-4-20250514
    thinking: true

permission_mode: default
```

> `api_key` 可直接写死，或留空后由环境变量提供（`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`）。值中的 `${VAR}` 占位符会解析为环境变量。

### 接入其他模型

OpenAI 及任意 OpenAI 兼容服务（DeepSeek、Kimi、GLM、MiniMax…）：

```yaml
providers:
  - name: deepseek
    protocol: openai-compat
    base_url: https://api.deepseek.com/v1
    api_key: "your-deepseek-key"
    model: deepseek-chat
```

---

## 🚀 快速开始

### 交互式 TUI

```bash
mewcode
```

### 非交互模式

```bash
# 文本输出
mewcode -p "解释这个仓库的架构"

# 结构化 NDJSON 流式输出（便于脚本消费）
mewcode -p "修复 src/main.py 的 bug" --output-format stream-json
```

### 远程模式（浏览器访问 http://localhost:18888）

```bash
mewcode --remote
```

### 权限模式覆盖

```bash
mewcode --mode bypassPermissions
```

---

## ⌨️ 斜杠命令

| 命令 | 别名 | 说明 |
| --- | --- | --- |
| `/help` | `/h`, `/?` | 显示帮助信息 |
| `/status` | — | 显示状态信息 |
| `/clear` | — | 清除对话历史 |
| `/compact` | — | 压缩上下文 |
| `/plan` | — | 切换到 Plan 模式 |
| `/review` | — | 审查代码变更 |
| `/rewind` | — | 回退到之前的检查点 |
| `/session` | — | 会话管理 |
| `/memory` | — | 记忆管理 |
| `/skill` | — | 管理 Skill 技能包 |
| `/mcp` | — | 显示 MCP 服务器状态 |
| `/sandbox` | — | 沙箱管理 |
| `/tasks` | — | 管理后台任务 |
| `/trace` | — | 查看 Agent 父子追踪树 |
| `/worktree` | — | 管理 Git Worktree |

> 还支持**自定义命令**：把 `.md` 文件放入 `~/.mewcode/commands/` 或 `<project>/.mewcode/commands/`，文件名即为命令名，可带 YAML frontmatter（`description` / `aliases` / `argument-hint`），正文支持 `$ARGUMENTS` 占位符，子目录用冒号命名空间（如 `git:log`）。

---

## 🧩 项目结构

```
mewcode/
├── __main__.py        # 入口：TUI / 非交互 / Remote / 队友 worker 模式
├── app.py             # Textual TUI 应用
├── agent.py           # 核心 Agent 循环与事件模型
├── client.py          # LLM 客户端（多协议）与 context window 解析
├── config.py          # 配置加载与多文件合并
├── permissions/       # 权限模式 / 危险命令检测 / 规则引擎 / 沙箱
├── teams/             # 团队协作（tmux / iTerm2 / in-process）
├── skills/            # 技能加载与执行
├── memory/            # 记忆管理
├── mcp/               # MCP 客户端与工具包装
├── hooks/             # 生命周期事件钩子
├── worktree/          # Git worktree 管理
├── filehistory/       # 文件历史 / rewind
├── tools/             # 内置工具实现
├── commands/          # 斜杠命令系统（内置 + 自定义 .md）
└── agents/            # 子 Agent、fork、任务管理、追踪
```

---

## 🧪 开发与测试

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试
uv run pytest
```

`MEWCODE.md` 中记录了项目约定：

- commit message 使用英文
- 变量命名使用 `snake_case`

---

## 📄 开源协议

本项目为个人学习 / 自用项目，暂未指定正式开源协议。如用于开源分发，请自行添加合适的 LICENSE。

---

*基于 [Textual](https://github.com/Textualize/textual) 与 [Anthropic / OpenAI 官方 SDK](https://anthropic.com) 构建。*
