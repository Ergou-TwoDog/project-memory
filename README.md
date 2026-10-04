# project-memory

面向 LLM 的项目长期记忆能力包。作为 Claude Code 插件分发：**其他项目配一次，就自动获得"记得自己做过什么"的能力**。

核心纯标准库；只有 MCP server 需要 `mcp`（可选 extra）。通过 [`uvx`](https://docs.astral.sh/uv/) 分发——uv 自己负责解释器与依赖，**跨平台，不需要你管 `python` 叫什么名字**。

## 设计判断

1. **记忆的成本必须接近零。** 需要"决定要不要记 + 填表 + 批准"的系统，最后没人用。
2. **信任来自可复现，不是来自批准。** 能从 git 重放的东西，不需要人点头。
3. **产品是读取时机，不是查询接口。** 记忆应在需要时自己出现。

由此分四层，**v1 实现 L1 + L3 + L4**：

| 层 | 内容 | 谁做 | v1 |
|---|---|---|---|
| L0 | 事实源（git、测试、命令） | 已存在 | 由 L1 读取 |
| **L1** | **自动观察**：机械记录提交/会话，带溯源 | hook，无 LLM | ✅ |
| L2 | 解释：把观察合成为答案 | **模型现算**，标为推断 | 交模型，无代码 |
| **L3** | **意图候选**：模型广提，人只做"提升" | 模型提，人采纳 | ✅ |
| **L4** | **时机投递**：会话/提问时自动注入 | hook | ✅ |

**核心区分：观察是事实（可复现），意图是承诺（不可复现）。** 所以观察无闸门，意图必须由人采纳——而人只做"采纳/拒绝"，不必从零撰写。

## 安装

**前提：`uv` 在 PATH 上。** uv 自己准备 Python 与依赖；装完后**必须重开 Claude Code**，否则它派生 MCP/hook 子进程时找不到 `uvx`。

**1. 装插件**（插件走 git，不进 PyPI）

```bash
# 方式一：本地目录（开发用）
claude --plugin-dir /path/to/project-memory/plugin

# 方式二：从 git
/plugin marketplace add https://github.com/Ergou-TwoDog/project-memory
/plugin install project-memory@project-memory
```

**2. 在目标项目里初始化一次**（创建 `<项目>/.project-memory/`）

```bash
cd /path/to/your-project
uvx --from git+https://github.com/Ergou-TwoDog/project-memory@v0.1.0 project-memory init
```

插件内部的 `.mcp.json` / `hooks.json` 用的是**同一个 git 源**，所以装完即可用，无需其他配置。

> **已钉版本**：插件内部的 `.mcp.json` / `hooks.json` 与上面的命令都钉在 `@v0.1.0`，
> 所以上游对 `main` 的改动**不会**影响使用者。发新版时：打新 tag → 更新这些引用里的版本号 → 重新安装插件。

初始化后**建议**把 `.project-memory/` 加进 `.gitignore`（本工具**不会**替你改 `.gitignore`）。

## 用起来是什么样

初始化之后没有额外步骤：

- 你正常提交 → hook 自动记一条**观察**（提交号、变更文件、提交信息）
- 会话开始 / 你问"这个项目最近怎么样了" → hook 自动**注入**记忆摘要
- 模型想提方向 → 调 `propose_intent` 存为**候选**
- 你决定采纳 → 在终端 `adopt`

## CLI（只有人跑）

```bash
uvx --from git+https://github.com/Ergou-TwoDog/project-memory@v0.1.0 project-memory status
uvx --from git+https://github.com/Ergou-TwoDog/project-memory@v0.1.0 project-memory adopt <intent_id>
uvx --from git+https://github.com/Ergou-TwoDog/project-memory@v0.1.0 project-memory drop  <intent_id>
```

（`CLAUDE_PROJECT_DIR` 已设时自动使用；否则用 `--project <路径>`。）

## MCP 工具

| 工具 | 读/写 | 作用 |
|---|---|---|
| `timeline(limit)` | 读 | 最近的机械观察（原始材料） |
| `recall(query, limit)` | 读 | 按关键词检索观察与意图 |
| `intents(include_candidates)` | 读 | 意图清单（带 `origin`） |
| `propose_intent(text, why, refs)` | 写 | 只写**候选**，`origin="llm"` |

**没有 `adopt` 工具**——采纳只能由人经 CLI 完成。

## 数据

存储在 `<项目>/.project-memory/`，追加式 JSONL，纯本地：

```
observations.jsonl   # 观察，可从 git 重放
intents.jsonl        # 意图，带 origin（llm / user）与 status
observer.json        # reflog 基线，用于判断"哪些提交是新的"
```

## 为什么 hook 便宜

hook 每次 Bash 调用都会跑，所以它**不能拉 mcp**。因此：

- 核心包 `dependencies = []`（纯标准库）
- `mcp` 只是可选 extra，且**钉在 `<2`**（2.x 把 `FastMCP` 改名成了 `MCPServer`），
  **只有 MCP server 用**——`.mcp.json` 里是 `uvx --from "…[mcp]" project-memory-mcp`

实测（本机）：hook 经 uvx **热启动约 0.3 秒**，冷启动约 1.8 秒（仅构建本包）。

## 本地开发的坑：uvx 会缓存构建产物

`uvx --from <本地目录>` 按 `pyproject.toml` 缓存 wheel——**只改 `.py` 不会失效**，
你会一路跑旧代码，而现象极其隐蔽（像是改动没生效）。`--refresh` 和 `--reinstall` **都不能**解决。

绕过（二选一）：

- `uvx --no-cache --from <目录> …`
- 或改动 `pyproject.toml` 里的 `version`（哪怕只是临时加一位）

**发布形态不受影响**：git URL + 钉 tag 时缓存正是我们要的。

## 边界与限制

- `origin:"llm"` 的候选**永远不是**决策前提；工具返回里始终带 `origin`，skill 也明确要求不得当作已定方向。
- **没有记录 ≠ 没有发生。** 记忆只覆盖观察到的内容，注入文本里始终带这句。
- hook 只在**已初始化**的项目里工作；未初始化/非 git 目录**安静跳过，绝不建目录**。
- 只观察目标仓的 reflog，**不改动、不提交**你的仓库。
- 首次见到某个仓库时只**设基线**，不回填历史提交；历史被改写（rebase 等）时不猜"哪些是新的"。
- 投递有 token 预算，超限会**显式**标注省略了多少行，不静默截断。
- 不是面对恶意并发/多写者的安全沙箱；`.project-memory/` 需要备份，工具不会自动迁移或删除。
- 不提供跨项目聚合、云同步、向量检索。

## 依赖的 Claude Code 事实（本机实测）

这些是本设计的硬前提，已用探针验证：

- **hook 进程有 `CLAUDE_PROJECT_DIR`；MCP 进程没有。**
- MCP 进程有 `CLAUDE_PLUGIN_ROOT`、`CLAUDE_PLUGIN_DATA`，且 **cwd = 项目目录**。
- `.mcp.json` 里 **`${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PLUGIN_DATA}` 会被替换**，
  但 **`${CLAUDE_PROJECT_DIR}` 不会**（原样字面量传入）。所以本插件不依赖项目目录的模板变量：
  MCP 侧由 `uvx` 提供命令，hook 侧由 `CLAUDE_PROJECT_DIR` 环境变量定位项目。
- **`claude` 进程自己必须能找到 `uvx`**——所以加 PATH 之后要重启 Claude Code。
