# AI Orchestrator

基于 Python 标准库的多模型文件协作工具，支持人工工作流、现有 Chrome DevTools MCP 和基于可见界面证据的 Model Router。无需模型 API 密钥。

这是脱敏的公开开发快照，不包含任何使用者的账号配置、真实任务、聊天记录或浏览器会话。

## 功能

- 把目标分为方案执行、独立审查、替代方案与核验三个互补子任务。
- 从最新可访问性快照识别网页输入和模型菜单，不使用屏幕坐标。
- 区分模型存在与账号可用权限，记录 discovered、available、unavailable、quota_exhausted、unknown。
- 先按权限过滤，再按推理、医学研究、长文本、代码、速度和额度等本地启发式排序。
- 验证实际型号及推理档位后锁定；未知、权限不足、升级提示、额度耗尽均暂停，不自动降级。
- 填入提示词后，真实网页提交需该次人工确认。真实模型切换默认关闭，不能把开关当成平台授权。
- 导入用户通过允许的复制/导出功能保存的完整结果，生成有限长度摘要与待审核候选稿。
- 人工批准当前结果后，才能生成版本化最终稿；结果变动会使旧审核失效。

## 快速开始

需要 Python 3.10+，已在 Python 3.12 验证。以下命令在仓库根目录执行；Windows 可将 python 替换为可用的 python3.12。

```sh
python -B -X utf8 scripts/run_tests.py
python -B -X utf8 examples/run_demo.py
python -B -X utf8 -m orchestrator create --id example-task --title '协作任务' --description '写明目标、约束和验收标准'
python -B -X utf8 -m orchestrator --help
```

离线示例仅使用明确标注的模拟回答，不会调用真实模型或执行系统备份。

## 网页与模型路由

详见 [BROWSER_WORKFLOW.md](BROWSER_WORKFLOW.md)。Python 通过项目内文件桥请求工具，当前 Codex 会话将请求转发到已经配置的 Chrome DevTools MCP。桥不是独立 MCP 客户端；单独运行不会自动连接浏览器。

公开版本的所有订阅状态初始均为 unknown。候选型号和能力分数仅为演示，不代表产品真实性、性能基准、当前可用版本或任何人的账号权限。示例 Claude 允许名单采用单模型保守限制；按自己的明确授权修改策略后，仍须网页验证。

运行时生成的 config/model-policy.json、tasks/、results/、final/、logs/ 和 imports/ 均被 Git 忽略，不能直接加入公共提交。

## 测试与限制

当前 58 项标准库 unittest 覆盖原有人工流程、浏览器状态与模型路由。模拟页面可验证完整链路和异常处理。公开快照不附带任何个人浏览器实测记录。

网页结构与账号权限会变化；不能验证时暂停。系统不提供付费升级、购买、Cookie/令牌读取、无人值守真实网页批量提交或自动抓取真实回答的功能。候选报告不是自动完成的语义审核或事实核验。

开发状态见 [progress.md](progress.md)，公开内容边界见 [PRIVACY.md](PRIVACY.md)。
