# PersonalCode — 个人编码助手 / Terminal AI Coding Assistant

一个基于 **Python + Textual** 的终端 AI 编码助手。PersonalCode 运行在你的终端里，以 TUI（文本用户界面）为核心，内置多模型接入、权限控制、技能（Skill）、记忆（Memory）、团队协作（Teams）、MCP 工具扩展等能力，帮助你在命令行中高效地完成编码、审查、重构等任务。

> PersonalCode 强调 **人在回路（human-in-the-loop）**：默认权限模式下，写文件与执行命令都会先征求你的同意，把控制权始终留在你手里。

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
- 六层权限决策管线：Plan 例外 → 安全只读命令放行 → 危险命令黑名单 → OS 沙箱放行 → 路径沙箱 → 规则引擎（deny > ask > allow）→ 模式矩阵兜底 → 人工确认。
- 危险命令检测（`DangerousCommandDetector`）、路径沙箱（`PathSandbox`）、基于规则的权限引擎（支持全局 / 项目 / 本地三层规则）。
- **受保护路径无条件禁写**：配置文件、权限规则文件、技能目录在**任何权限模式**（含 `bypassPermissions`）下都禁止写入。
- 可选的 **OS 级沙箱**（Linux `bwrap` / macOS `seatbelt`）。

### 团队协作（Teams）
- 支持多 Agent 并行协作：Lead 调度，队员执行。
- 三种后端：`in-process`、`tmux`、`iTerm2`。
- 队员邮箱（文件锁 + 陈旧锁接管）、共享任务板（带依赖）、进度追踪、结构化消息 `request_id` 配对、计划审批、`coordinator mode`（Lead 只调度不写码）。

### 技能（Skill）
- 三层技能来源：项目级（`.personalcode/skills`）、用户级（`~/.personalcode/skills`）、内置技能。
- 技能可用 `/skill` 命令安装、加载与管理，内置 `frontend-design`、`skill-creator` 示例。
- 技能支持 `inline` / `fork` 两种执行模式，可用 `$ARGUMENTS` 占位符。

### 🧬 Skills 自进化
- 从「对话 + 下一轮用户反馈」中**自动沉淀可复用工作流**为 `SKILL.md`：区分「记忆记事实、技能沉淀方法」。
- 链路：当前轮暂存 pending window → 下一轮反馈合并为证据 → 抽取候选（最多一个）→ 维护决策（**新增 / 合并 / 放弃**，优先合并）→ 落盘 + 版本快照 + 审计留痕。
- 提供 `skill_create` / `skill_evolve` 两个显式工具，让模型在对话中手动沉淀；写盘走权限沙箱的**实例级放行边界**（只放开技能目录，权限配置仍禁写）。
- 每次都留 provenance：`.personalcode/skill-evolution/` 下有 `usage.jsonl`、`online_provenance.jsonl`、版本快照 `history/`，可回放「为什么写、怎么写、结果如何」。

### 记忆（Memory）
- 自动记忆、记忆合并/整合（consolidation）、按需召回（recall）、会话级指令（instructions）。
- 每条记忆一个 `.md`（带 frontmatter），用户级 / 项目级双层，`MEMORY.md` 索引带截断保护。

### 🗂 上下文治理与会话折叠
- **两层渐进式压缩**：Layer 1 工具结果入场预算（**无损**——超长结果溢写落盘，只留预览）+ Layer 2 自动压缩（**有损**——双阈值触发 + 熔断器，保留近期原文且不拆散工具配对）。
- **会话记忆折叠**：把长会话折叠成 `episode_memory`（任务经过）/ `working_memory`（当前推进）/ `tool_memory`（工具经验）三块结构化记忆，用状态替换历史，让 Agent 从折叠状态继续执行；与摘要走同一套触发链路、二选一。
- 模型可自主调用 `compact_context` 工具触发折叠；解析失败有 `fallback` 兜底保留原始记录，绝不丢现场。
- 折叠记录落盘为 `folded_memory`，会话恢复（resume）时可从折叠状态继续。

### 工具扩展
- **MCP（Model Context Protocol）** 支持：stdio 子进程 与 Streamable HTTP（2025-03-26 spec）两种传输。
- 工具检索（`ToolSearch`）：根据上下文窗口自动决定工具的加载策略（eager / native / dispatch），保持工具数组稳定以命中 Prompt Cache。

### Hooks 事件系统
- 会话 / 轮次 / 工具 / 消息 / 系统 五个层级的生命周期事件钩子，可自定义执行器。

### 工程化能力
- **Worktree** 管理：Git worktree 的创建、切换、清理。
- **Rewind**：文件历史回溯到任意检查点。
- **Crash 恢复**：异常落盘 + 崩溃日志，可查看上一次运行现场。
- 上下文压缩（`/compact`）、流式/批处理输出。
- 非交互模式 + `stream-json` 输出，便于脚本化与集成。
- **Remote 模式**：WebSocket 桥接，在浏览器中远程控制 Agent。

### 📊 消融测评
- 为 Skill 自进化配套的**配对消融脚本**（`eval/`）：固定任务集，同一任务跑「无自进化 / 有自进化+反馈」两组，交替顺序控制变量，输出正确率差与技能沉淀数。
- 支持 `--validate-only` 先校验题目集、不调用模型。

---

## 📦 安装

要求 **Python ≥ 3.11**，推荐使用 [uv](https://github.com/astral-sh/uv) 管理环境。

```bash
# 克隆仓库
git clone <your-repo-url>
cd personalcode-python

# 安装依赖（项目使用 uv + hatchling）
uv sync
```

或直接以可编辑方式安装为命令行工具：

```bash
uv pip install -e .
```

## ⚙️ 配置

首次使用前创建配置文件。参考模板见 `.personalcode/config.yaml.example`。

配置文件按优先级合并（后者覆盖前者）：

1. `~/.personalcode/config.yaml` — 全局配置
2. `<project>/.personalcode/config.yaml` — 项目配置
3. `<project>/.personalcode/config.local.yaml` — 本地（不提交）配置

```bash
cp .personalcode/config.yaml.example .personalcode/config.yaml
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
personalcode
```

### 非交互模式

```bash
# 文本输出
personalcode -p "解释这个仓库的架构"

# 结构化 NDJSON 流式输出（便于脚本消费）
personalcode -p "修复 src/main.py 的 bug" --output-format stream-json
```

### 远程模式（浏览器访问 http://localhost:18888）

```bash
personalcode --remote
```

### 权限模式覆盖

```bash
personalcode --mode bypassPermissions
```

---

## 🧬 让 Agent 自己沉淀能力

在会话里给一句**稳定的、未来同类任务仍适用的反馈**，PersonalCode 会在下一轮把它沉淀成技能：

```text
以后写报告时，先给结论再展开理由，语言要正式克制。
```

适合沉淀：输出规范、工作流、判断标准、用户偏好。
不适合沉淀：一次性任务内容、临时参数、私密信息。

运行 Skill 自进化消融（需先配置好 API key）：

```bash
# 先校验题目集，不调用模型
python -m personalcode.eval.eval_skill_evolution \
  --cases data/eval/skill_cases.jsonl --output eval-out/skills --validate-only

# 实跑：同一任务跑「无自进化 / 有自进化」两组
python -m personalcode.eval.eval_skill_evolution \
  --cases data/eval/skill_cases.jsonl --output eval-out/skills
# 结果看 eval-out/skills/summary.json 的 accuracy_delta_percentage_points 与 skills_created
```

---

## ⌨️ 斜杠命令

| 命令 | 别名 | 说明 |
| --- | --- | --- |
| `/help` | `/h`, `/?` | 显示帮助信息 |
| `/status` | — | 显示状态信息 |
| `/clear` | — | 清除对话历史 |
| `/compact` | — | 压缩上下文（折叠启用时产出结构化折叠记录） |
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

> 还支持**自定义命令**：把 `.md` 文件放入 `~/.personalcode/commands/` 或 `<project>/.personalcode/commands/`，文件名即为命令名，可带 YAML frontmatter（`description` / `aliases` / `argument-hint`），正文支持 `$ARGUMENTS` 占位符，子目录用冒号命名空间（如 `git:log`）。

---

## 🧩 项目结构

```
personalcode/
├── __main__.py        # 入口：TUI / 非交互 / Remote / 队友 worker 模式
├── app.py             # Textual TUI 应用
├── agent.py           # 核心 Agent 循环与事件模型
├── client.py          # LLM 客户端（多协议）与 context window 解析
├── config.py          # 配置加载与多文件合并
├── permissions/       # 权限模式 / 危险命令检测 / 规则引擎 / 沙箱
├── teams/             # 团队协作（tmux / iTerm2 / in-process）
├── skills/            # 技能加载与执行 + 自进化（evolution / online_evolution / retrieval）
├── memory/            # 记忆管理 + 会话持久化（含折叠记录）
├── context/           # 上下文治理：压缩（auto_compact）/ 溢写 / 会话折叠（folding）
├── mcp/               # MCP 客户端与工具包装
├── hooks/             # 生命周期事件钩子
├── worktree/          # Git worktree 管理
├── filehistory/       # 文件历史 / rewind
├── tools/             # 内置工具实现（含 skill_create / skill_evolve / compact_context）
├── commands/          # 斜杠命令系统（内置 + 自定义 .md）
├── agents/            # 子 Agent、fork、任务管理、追踪
└── eval/              # 消融测评脚本
data/
└── eval/              # 测评任务集（skill_cases.jsonl 等）
```

---

## 🧪 开发与测试

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试
uv run pytest
```

当前测试基线：**768 passed / 1 failed / 3 skipped**（失败项为既有的并发批执行断言 `tests/test_agent.py::test_concurrent_batch_execution`，待定位）。

`PERSONALCODE.md` 中记录了项目约定：

- commit message 使用英文
- 变量命名使用 `snake_case`

---

## 📄 开源协议

本项目为个人学习 / 自用项目，暂未指定正式开源协议。如用于开源分发，请自行添加合适的 LICENSE。

---

*基于 [Textual](https://github.com/Textualize/textual) 与 [Anthropic / OpenAI 官方 SDK](https://anthropic.com) 构建。*
