---
name: intend
description: Propose a candidate direction worth pursuing, grounded in this project's recorded history. Use when thinking about what to do next, or when the user asks where this project should go.
allowed-tools: mcp__project-memory__propose_intent, mcp__project-memory__intents, mcp__project-memory__recall, mcp__project-memory__timeline
---

# 提出候选意图

你可以**自由提出**候选，但**不能替人采纳**。

1. 先 `intents` 看已有意图（避免重复），再 `recall` / `timeline` 找依据。
2. `propose_intent(text, why, refs)`：
   - `text` — 一句话说清要做什么
   - `why` — 为什么。**这一层没有 ground truth，所以必须写实**，不要写听起来合理但并非真实原因的话
   - `refs` — 相关 commit（逗号分隔）
3. **明确告诉用户这是候选**，需要人在终端运行 `adopt <intent_id>` 才成为承诺。

## 禁止

- 不要把自己的候选当作下一步决策的前提——那会造成自证循环。
- 不要声称某方向"已定"，除非 `intents` 返回 `status:"adopted"` **且** `origin:"user"`。
- 不要为"没有依据的方向"编造 `why`；说不出理由就不提。
