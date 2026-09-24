# mini_Ncode代码导航

`mini_Ncode.py` 现在只负责启动，原有功能按职责放在 `mini_Ncode/`。
`agent.py` 和 `agent1.py` 未修改。

## 启动

在项目根目录运行，命令不变：

```powershell
python mini_Ncode.py
```

环境变量仍然沿用原来的 `.env`：

- 模型：`MODEL`，或者 `MODEL_ID`。
- API Key：`LLM_API_KEY`，或者 `ANTHROPIC_API_KEY`。
- API 地址：`LLM_BASE_URL`，或者 `ANTHROPIC_BASE_URL`。
- 可选高德 MCP：设置 `AMAP_MAPS_API_KEY` 后启用。

记忆目录仍是当前工作目录的 `.memory/`，技能目录仍是 `.skills/`，
日志仍写到 `.logs/agent.log`。此次没有迁移目录或改动现有记忆内容。

## 到哪个文件找功能


| 文件                           | 职责                                                     |
| ------------------------------ | -------------------------------------------------------- |
| `agent2.py`                    | 启动入口                                                 |
| `mini_agent2/app.py`           | 创建客户端、组装工具与钩子、用户输入、输出回答、关闭资源 |
| `mini_agent2/config.py`        | 路径、记忆参数、环境变量读取                             |
| `mini_agent2/logging_setup.py` | 控制台日志和滚动文件日志                                 |
| `mini_agent2/runner.py`        | 系统提示词、模型与工具的循环、Todo 提醒、记忆流程        |
| `mini_agent2/tool_types.py`    | `ToolSpec`、`ToolResult`、`ToolProvider` 接口            |
| `mini_agent2/providers.py`     | 本地函数和 MCP 工具的执行适配                            |
| `mini_agent2/registry.py`      | 工具聚合、名称冲突检查、模型工具格式、调用路由           |
| `mini_agent2/tools.py`         | 注册 bash、文件读写、glob、todo_write、load_skill、task  |
| `mini_agent2/subagent.py`      | 独立上下文的子任务循环                                   |
| `mini_agent2/hooks.py`         | 钩子注册、权限检查、调用日志和结束统计                   |
| `mini_agent2/todo.py`          | 待办状态校验和展示                                       |
| `mini_agent2/skills.py`        | 技能扫描、目录生成和加载                                 |
| `mini_agent2/memory_store.py`  | 记忆文件、索引、路径和记录校验，不调用模型               |
| `mini_agent2/memory.py`        | 通过模型召回、抽取和整合记忆                             |
| `mini_agent2/messages.py`      | 从消息提取文本和 JSON 的共用函数                         |
| `tests/test_agent2.py`         | 离线回归测试                                             |

## 建议阅读顺序

先看 `app.py` 的 `main()`，理解组件如何组装；
再看 `runner.py` 的 `agent_loop()`，理解一次请求如何经过模型和工具；
最后跟到 `registry.py → providers.py → tools.py`，查看工具如何执行。

记忆部分可以分开阅读：先看 `memory_store.py` 的本地读写，
再看 `memory.py` 的模型调用。Todo 和技能加载也可以独立阅读。

## 如何扩展

- 加本地工具：在 `tools.py` 的 `create_local_provider()` 中添加带装饰器的函数。
- 加 MCP 服务：在 `app.py` 中创建新的 `MCPToolProvider` 并加入注册表。
- 调整权限规则：修改 `hooks.py`。
- 调整模型循环或提醒策略：修改 `runner.py`。
- 调整记忆筛选：修改 `memory.py`；调整存储格式：修改 `memory_store.py`。

初始化集中在 `app.py`，不再在导入时创建客户端、读取 `.env` 或连接服务。
`agent_loop()` 的客户端、模型、注册表、技能和钩子由参数传入；
`create_local_provider()` 的 Todo 与技能也由调用方传入。
因此测试可以替换这些依赖，而不必启动整个应用。

## 上下文压缩

`mini_Ncode/context.py` 的 `ContextCompactor` 负责大结果落盘、旧结果缩短、历史裁剪和模型摘要。
`app.py` 在会话中创建压缩器，`runner.py` 在模型请求前调用，并在 `compact` 工具所在批次执行完后处理主动压缩。
`subagent.py` 为每个子任务创建独立压缩器，复用同样的自动压缩与单次超限重试逻辑。

历史归档保存在 `.transcripts/`，工具完整返回内容保存在 `.task_outputs/tool-results/`。
工具调用与返回结果在裁剪时成组保留；摘要失败不替换原始历史。
阈值、行为与限制见 [README 的上下文压缩说明](README.md#上下文压缩)。
对应离线测试为 `tests/test_context.py`。

## 测试

```powershell
# 全部离线测试
python -B -m unittest tests.test_agent2 -v

# 只测主循环
python -B -m unittest tests.test_agent2.RunnerTests -v

# 只测工具
python -B -m unittest tests.test_agent2.ToolTests -v

# 只测 Todo 和记忆
python -B -m unittest tests.test_agent2.TodoAndMemoryTests -v
```

测试使用假的模型客户端和 MCP 客户端，文件操作使用临时目录。
这些测试验证拆分后的本地逻辑和模块衔接；真实模型、高德服务和密钥配置
仍需在你的实际运行环境中验证。
