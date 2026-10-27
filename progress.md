# Project Progress

## 当前目标

维护可公开复用的多模型文件协作、Chrome DevTools MCP 网页流程及基于界面证据的模型路由。

## 已完成的事项

- 任务拆分、完整结果导入、有限摘要、变更校验、人工审核与版本化最终稿。
- 基于快照 UID 的页面发现和填入、单次提交确认、页面变化检测与失败状态。
- 五种权限状态、动态型号发现、能力档案、权限优先排序、模型/推理锁定与显式切换确认。
- 本地模拟页面及 58 项自动化测试。
- 公开源码按允许清单生成；原始账号、任务、聊天、会话和运行日志均不包含。

## 关键决策

- Python 仅依赖标准库；使用当前 Codex 会话已配置的 MCP，不配置新的浏览器连接或模型 API。
- 所有公开默认订阅状态 unknown；候选名单和能力评分仅为示例。
- 权限未知或额度耗尽时暂停，不自动购买升级或静默降级。
- 真实提交和最终报告批准须人工确认；真实回答采用允许的复制/导出后导入。
- 保留原有业务核心，新增功能通过独立模块与 CLI 入口接入。

## 相关文件

- orchestrator/core.py、providers.py：原有文件工作流。
- orchestrator/browser.py、router.py、browser_bridge.py、cli.py：网页与路由扩展。
- scripts/chrome_mcp_relay.js：当前 Codex 会话内工具转发。
- tests/、examples/、scripts/run_tests.py：测试与模拟示例。
- README.md、BROWSER_WORKFLOW.md、PRIVACY.md：使用说明与公开边界。

## 未完成的待办

- [ ] 针对网页选择器变化持续完善保守适配，不能确认时保持暂停。
- [ ] 实际使用者需自行验证模型权限、推理档位以及平台允许的自动化范围。

## 下一步

运行 scripts/run_tests.py 和 examples/run_demo.py；如需浏览器集成，按 BROWSER_WORKFLOW.md 先验证本地页面，再配置自己的非秘密权限规则。

## 已知问题 / 阻塞项

文件桥需要当前 Codex 会话转发 MCP；网页结构和权限不稳定，不保证通用无人值守运行。能力分数不是性能基准；语义审核与事实核验仍由实际审核者完成。

## 环境与验证

Python 3.10+，使用 Python 3.12 验证。运行 python -B -X utf8 scripts/run_tests.py。测试临时文件与日志保存到本地 logs/，该目录不提交。
