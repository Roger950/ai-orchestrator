# 浏览器工作流

## 账号与模型

首次运行 router status 会在本地生成 config/model-policy.json。所有订阅默认 unknown，真实自动模型切换默认关闭。示例型号及推理档位不是已验证权限；新型号只发现、不直接启用。Claude 的单模型允许名单是保守示例配置。

配置只保存非秘密的权限规则及界面证据，不放 Cookie、令牌或密码。公开版本不包含任何实际账号配置文件。

```sh
python -B -X utf8 -m orchestrator router status
python -B -X utf8 -m orchestrator router plan --task-id example-task --task-type medical
```

## Codex 与现有 Chrome MCP

先创建任务，然后启动文件桥：

```sh
python -B -X utf8 -m orchestrator.browser_bridge router-discover example-task
```

当前 Codex 会话读取 scripts/chrome_mcp_relay.js，在 functions.exec 环境中求值为函数并调用 relay('example-task', 6)。该函数使用现有 tools.mcp__chrome_devtools__*，按请求转发有限轮次，直到 logs/<id>/bridge-<run>/done.json 出现。它不是 Node 命令；没有工具转发时 300 秒后记录超时。

快照在写盘前投影成模型/推理菜单、输入控件和权限提示，丢弃聊天正文。每次操作使用最新快照 UID。不得恢复完整真实网页快照持久化。

人工在平台允许的界面选好型号和档位后，用 router-lock <id> --model <platform> --target <model-name> --reasoning <level> 验证；router-verify 可重新检查既有观察。无法确认时标为 unknown。真实平台切换默认返回人工提示。

## 填入、确认和提交

```sh
python -B -X utf8 -m orchestrator.browser_bridge fill example-task --model claude
```

向用户展示具体平台、型号、档位、页面、提示词文件，取得该次确认后，才记录并提交：

```sh
python -B -X utf8 -m orchestrator browser approve example-task --model claude --reviewer 'actual reviewer'
python -B -X utf8 -m orchestrator.browser_bridge submit example-task --model claude
```

确认者字段是人工声明而不是身份认证。提交前重新核对锁定、提示词、输入和页面生成时间；已尝试提交而结果不明时不自动重发。升级提示、额度耗尽与选择器异常暂停通道。改变锁定型号/档位需用户明确确认，普通切换确认不能扩大账号允许名单。

## 导入与审核

用户通过平台允许的复制/导出功能保存完整 UTF-8 文件，放在项目内部，再导入：

```sh
python -B -X utf8 -m orchestrator browser import example-task --model claude --file imports/answer.md --complete --method user_export
python -B -X utf8 -m orchestrator browser status example-task
```

完整正文保存到 results/，摘要保存到 tasks/。三份齐全后生成交叉审核模板与报告候选稿；审核者另行撰写审核意见，执行 review --approve，再 finalize。文件导入不能独立证明语义完整性或实际模型来源。

## 本地模拟验收

使用现有 MCP 打开 tests/fixtures/model-router.html 的绝对 file URL，分别添加 ?model=chatgpt、?model=claude、?model=gemini。创建独立模拟任务，再启动并转发：

```sh
python -B -X utf8 -m orchestrator.browser_bridge router-local local-demo --fixture tests/fixtures/model-router.html
python -B -X utf8 -m orchestrator.browser_bridge local-failure-checks failure-demo --fixture tests/fixtures/model-router.html
```

失败模拟包括 quota、unavailable、switch-failure；另一 ai-local.html 页面支持 timeout/incomplete。模拟策略与真实策略分开存储。本地成功不代表真实平台调用成功。
