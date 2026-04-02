# LLM Relay Manager

LLM Relay Manager 是一个本地 Web 控制台，用于管理 AI 中转站、API Key、协议探测、模型发现和可用性检查。

它适合需要验证某个中转站是否真的能承载 OpenAI、Anthropic 或 Gemini 兼容流量的运维或测试场景，方便在交给用户或下游系统之前先做统一检查。

## 功能说明

- 管理中转站，以及每个站点下的多个 API Key
- 新增 Key 后自动探测支持的协议
- 按协议发现模型列表
- 对单个协议绑定执行可用性检查
- 跨站点、Key、绑定搜索模型
- 查看后台任务、调度状态和检查历史
- 使用单文件 Python 后端和 SQLite 本地存储，部署简单

## 当前支持的协议探测器

- `OpenAI Chat`
- `OpenAI Responses`
- `Claude / Anthropic Messages`
- `Gemini GenerateContent`

## 页面与路由

- `/` 仪表盘总览
- `/stations` 以站点为中心的管理页面
- `/keys` 以 Key 为中心的运维页面
- `/models` 模型搜索页面
- `/history` 后台任务、调度状态和历史记录

## 技术栈

- Python 3.10+
- SQLite
- 内置 `http.server`
- 使用 `curl` 进行上游探测和检查
- 原生 HTML、CSS、JavaScript

## 快速开始

```bash
git clone https://github.com/qihaze123/llm-relay-manager.git
cd llm-relay-manager
python3 app.py
```

默认地址：

```text
http://127.0.0.1:8787
```

自定义监听地址或端口：

```bash
python3 app.py --host 0.0.0.0 --port 8791
```

## 运行要求

- Python `3.10` 或更高版本
- 系统 `PATH` 中可用的 `curl`

当前版本不依赖第三方 Python 包。

## 接口概览

- `GET /api/summary`
- `GET /api/stations`
- `POST /api/stations`
- `PUT /api/stations/:id`
- `DELETE /api/stations/:id`
- `GET /api/keys`
- `POST /api/keys`
- `PUT /api/keys/:id`
- `DELETE /api/keys/:id`
- `GET /api/bindings`
- `POST /api/keys/:id/detect`
- `POST /api/bindings/:id/discover`
- `POST /api/bindings/:id/check`
- `GET /api/models/search?q=...&available_only=1`
- `GET /api/history?limit=100`
- `GET /api/jobs?limit=100`
- `GET /api/jobs/:id`
- `GET /api/settings/scheduler`
- `PUT /api/settings/scheduler`
- `POST /api/run-cycle`

## 数据存储

应用默认把本地数据存放在 `data/relay_manager.db`。

主要表包括：

- `stations`
- `api_keys`
- `protocol_bindings`
- `binding_models`
- `binding_checks`
- `binding_check_history`
- `jobs`
- `app_settings`

## 安全说明

- API Key 当前以明文形式存储在 SQLite 中
- 本项目适合本地环境或受控的内部环境使用
- 不建议把 Web 界面直接暴露到公网
- 如果运行主机不受信任，请不要直接放入高价值生产密钥，除非你已经自行补充加密与访问控制

更详细的说明见 `SECURITY.md`。

## 已知限制

- 这是单机工具，不是完整的生产控制台
- 调度器和后台任务都运行在进程内
- 进程重启后，执行中的任务会中断
- 协议探测属于尽力判定，部分中转实现可能会造成误判
- 当前版本优先解决运维使用流程，不以大规模性能优化为目标

## 后续计划

- API Key 加密存储
- 增加更多协议适配器和探测策略
- 支持导入导出
- 增加批量操作
- 提供更完整的成功率、延迟和错误统计
- 改进任务执行架构

## 开发运行

```bash
python3 app.py
```

当前 UI 本身就是中文优先，这版文档也已统一为中文说明。

## 开源协议

MIT
