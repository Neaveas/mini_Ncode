# mini_Ncode

一个基于 Python 的命令行编程智能体，通过 Anthropic SDK 调用模型，支持本地工具、MCP 服务、任务规划、技能加载和持久化记忆。核心实现位于 `mini_Ncode/`，按功能拆分，便于阅读、扩展和单独测试。

## 功能

- **工具调用**：执行命令、读取文件、写入文件、替换文本和搜索路径，通过统一注册表分发调用。
- **任务规划**：使用 `todo_write` 管理待办，最多 20 项，同时最多一项处于进行中；连续三轮工具调用未更新 Todo 时加入提醒。
- **技能加载**：扫描 `.skills/*/SKILL.md`，将技能目录提供给模型，通过 `load_skill` 加载完整指南。
- **持久化记忆**：在 `.memory/` 保存记忆和索引，通过模型召回相关记录、抽取长期信息，并在达到阈值后整合。
- **上下文压缩**：主循环与子任务共用分层压缩机制，支持工具结果落盘、历史裁剪、模型摘要、`compact` 工具，以及上下文超限后的单次恢复。
- **独立子任务**：通过 `task` 启动独立对话上下文的子任务，返回最终文本。
- **MCP 接入**：配置高德 API Key 后，加载高德地图 MCP 工具。
- **钩子与日志**：提供用户输入、工具执行前后、回合结束钩子，以及部分命令和越界文件访问的权限检查；日志写入 `.logs/agent.log`。

## 环境要求

- Python 3.11 或更高版本。
- Python 依赖：`anthropic`、`python-dotenv`、`PyYAML`、`fastmcp`。
- 使用高德 MCP 时，需要 Node.js 和 npm，并保证 `npx` 可用。
- 支持 Anthropic Messages API 与工具调用的模型服务。

## 快速开始

以下命令在项目根目录执行。

### 1. 安装依赖

```powershell
python -m pip install -r requirements.txt
```


### 2. 配置环境变量

在项目根目录创建或编辑 `.env`：

```dotenv
MODEL=你的模型名称
LLM_API_KEY=你的模型服务密钥

# 可选：使用自定义模型服务地址时配置
# LLM_BASE_URL=https://your-provider.example

# 可选：设置后启用高德地图 MCP
# AMAP_MAPS_API_KEY=你的高德密钥
```

也支持以下替代变量名；同时设置时优先使用左侧变量：


| 优先变量       | 替代变量             | 用途           |
| -------------- | -------------------- | -------------- |
| `MODEL`        | `MODEL_ID`           | 模型名称       |
| `LLM_API_KEY`  | `ANTHROPIC_API_KEY`  | 模型服务密钥   |
| `LLM_BASE_URL` | `ANTHROPIC_BASE_URL` | 自定义服务地址 |

启动时通过 `load_dotenv(override=True)` 加载配置，`.env` 中的值会覆盖同名环境变量。

### 3. 启动

启动命令：

```powershell
python mini_Ncode.py
```

输入问题后按回车发送。输入 `q`、`quit`、`exit` 或空行退出。

也可以使用 `python -c "from mini_Ncode.app import run; run()"` 直接调用应用模块。

## 项目结构

```text
mini_Ncode/
├── mini_Ncode.py          # 启动脚本
├── mini_Ncode/            # 模块化实现
│   ├── app.py            # 组件初始化、命令行交互、资源关闭
│   ├── config.py         # 环境配置、路径和常量
│   ├── runner.py         # 主对话循环、提示词、Todo 提醒
│   ├── context.py        # 上下文压缩、输出归档和超限恢复
│   ├── tool_types.py     # 工具定义、结果和 Provider 接口
│   ├── providers.py      # 本地函数与 MCP 服务适配
│   ├── registry.py       # 工具聚合、模型工具格式、调用路由
│   ├── tools.py          # 本地工具实现与注册
│   ├── subagent.py       # 独立上下文的子任务循环
│   ├── hooks.py          # 钩子调度、权限检查和调用统计
│   ├── todo.py           # 待办状态校验与展示
│   ├── skills.py         # 技能扫描和加载
│   ├── memory_store.py   # 记忆文件、索引、路径与记录校验
│   ├── memory.py         # 模型驱动的记忆召回、抽取与整合
│   ├── messages.py       # 消息文本和 JSON 提取
│   └── logging_setup.py  # 控制台和滚动文件日志
├── tests/test_agent2.py   # 原有功能的离线测试
├── tests/test_context.py  # 上下文压缩与循环接入测试
├── .skills/              # 技能目录
├── .memory/              # 记忆记录与 MEMORY.md 索引
├── .transcripts/         # 压缩前的 JSONL 对话归档
├── .task_outputs/        # 大工具结果的完整返回内容
├── .logs/                # 运行日志
├── agent.py              # 原始单文件版本
├── agent1.py             # 预留文件
└── requirements.txt      # 依赖清单
```

工作区、技能、记忆和日志路径以启动命令所在目录为基准，请从项目根目录运行。

## 上下文压缩

无需额外配置，压缩会在模型请求前自动检查。也可以在对话中让模型调用 `compact`，
在当前整批工具结果收齐后生成摘要。主循环和 `task` 子任务均支持压缩。

| 策略 | 默认行为 |
| --- | --- |
| 大结果落盘 | 单条工具结果超过 30,000 字符时保存到 `.task_outputs/tool-results/`，上下文保留路径和预览 |
| 批次预算 | 同批结果超过 200,000 字符时，进一步缩短较大的结果，包含低于单条阈值的结果 |
| 历史裁剪 | 超过 50 条消息后保留头部和近期交互，中间历史归档为 JSONL；工具调用与结果成组保留，边界处允许少量超出消息数 |
| 轻量压缩 | 历史超过 50,000 字符时，优先把较旧且已被模型消费的工具结果换成归档路径，保留最近 3 条已消费结果 |
| 摘要压缩 | 继续超限时缩短结果预览，再摘要更早的历史；通常保留最近 5 条，必要时收紧到最后一组完整交互 |
| 超限恢复 | API 明确报告上下文过长时再压缩一次并重试；再次失败则返回错误，不无限重试 |

默认参数位于 `mini_Ncode/context.py` 的 `ContextCompactor` 类中，保存路径位于 `config.py`。
字符数用于估算消息历史大小，**不是 token 数**，也不包括 system 提示词和工具定义。
摘要会额外调用模型；摘要输入最多 80,000 字符，输出上限为 2,000 tokens。
单条过大的当前请求无法无损缩短，仍可能触发模型限制。

压缩会保留当前请求和最近一组完整工具交互，历史和完整返回内容可通过 `read_file` 查阅。
文件写入或摘要失败时保留原历史；主动 `compact` 失败会回传工具错误。
压缩摘要被标记为参考数据，也不会作为新的用户事实送入持久化记忆抽取。
`.transcripts/` 和 `.task_outputs/` 已加入 Git 忽略规则，压缩不会删除历史归档。

## 阅读和扩展

建议从 `app.py` 的 `main()` 开始，查看组件如何组装；再阅读 `runner.py` 的 `agent_loop()`，了解“模型返回工具调用 → 执行工具 → 回传结果 → 模型继续回答”的流程。

工具执行路径为 `registry.py → providers.py → tools.py`。记忆部分可先看 `memory_store.py` 的本地读写，再看 `memory.py` 中的模型调用。


| 想修改的功能             | 对应位置                                |
| ------------------------ | --------------------------------------- |
| 添加本地工具             | `tools.py` 的 `create_local_provider()` |
| 接入其他 MCP 服务        | `app.py` 中注册新的 `MCPToolProvider`   |
| 调整提示词或 Todo 提醒   | `runner.py`                             |
| 调整权限检查和钩子       | `hooks.py`                              |
| 调整记忆筛选、召回和整合 | `memory.py`                             |
| 调整记忆文件格式和索引   | `memory_store.py`                       |

添加技能时使用以下目录格式：

```text
.skills/
└── example/
    └── SKILL.md
```

`SKILL.md` 可以包含 YAML 元数据和正文：

```markdown
---
name: example
description: 说明这个技能适合什么任务
---

这里填写具体的操作指南。
```

## 测试

测试使用假的模型客户端和 MCP 客户端，文件操作使用临时目录，不需要真实 API 密钥。覆盖工具注册与路由、文件操作、技能加载、Todo、记忆读写、子任务和主循环。

运行方式：

```powershell
# 全部离线测试
python -B -m unittest discover -s tests -v

# 只测试上下文压缩
python -B -m unittest tests.test_context -v

# 只测试主循环
python -B -m unittest tests.test_agent2.RunnerTests -v

# 只测试工具
python -B -m unittest tests.test_agent2.ToolTests -v
```

离线测试验证本地逻辑和模块衔接；真实模型请求及高德 MCP 连接需要另行验证。
