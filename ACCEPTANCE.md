# Acceptance Evidence

日期：2026-03-31

本次验收针对“Key 优先、自动探测协议、后台任务化长校验”的版本。

## 验收目标

确认以下能力已经具备：

- 页面已经拆分，不再把所有功能塞进一个页面
- 先录入站点和 Key，不要求预先手选协议
- 新增 Key 后自动在后台探测协议
- 每个协议分别保存自己的模型列表和检查结果
- 长时间的模型检查会进入后台任务，可查看进度
- 搜索模型时可按“仅显示可用”过滤
- 进程重启后，旧任务不会永久卡在 `running`

## 1. 多页面可访问

已验证以下页面能直接打开：

- `/`
- `/keys`
- `/history`

通过 `curl` 可直接拿到页面 HTML：

```text
LLM Relay Manager · Dashboard
LLM Relay Manager · Keys
LLM Relay Manager · History
```

结论：

- 页面结构已经按职责拆开
- 长任务状态被放进 `History` 页面，不再要求用户盯着一个长请求

## 2. 新数据结构已生效

当前 `summary` 返回：

```json
{
  "station_count": 3,
  "key_count": 4,
  "binding_count": 10,
  "supported_binding_count": 4,
  "model_count": 94,
  "available_count": 14,
  "checked_count": 79,
  "history_count": 189,
  "active_job_count": 0
}
```

结论：

- 系统已使用 `Key -> 协议绑定 -> 模型 -> 当前检查 -> 历史检查 -> 后台任务` 这套结构
- `jobs` 表已经参与运行时统计

## 3. 新增/触发探测已改为后台任务

对真实 Key `daiju-auto-second` 触发协议探测：

```http
POST /api/keys/4/detect
```

立即返回的是任务对象，而不是阻塞等待：

```json
{
  "id": 3,
  "job_type": "detect_key",
  "status": "queued",
  "scope_type": "key",
  "scope_id": 4,
  "title": "探测 Key #4 协议",
  "total_steps": 4
}
```

随后查询：

```http
GET /api/jobs/3
```

最终任务成功完成，关键结果为：

```json
{
  "status": "ok",
  "completed_steps": 4,
  "total_steps": 4,
  "result": {
    "key_id": 4,
    "detection": [
      {"adapter_type": "openai_chat", "supported": true, "status": "ok", "model_count": 4},
      {"adapter_type": "openai_responses", "supported": false, "status": "unsupported", "model_count": 4},
      {"adapter_type": "anthropic_messages", "supported": true, "status": "ok", "model_count": 4},
      {"adapter_type": "gemini_generate_content", "supported": false, "status": "unsupported", "model_count": 0}
    ]
  }
}
```

结论：

- 新增或手动探测不会再把前端请求长时间卡住
- 探测结果仍然能完整落回数据库

## 4. 协议绑定的模型检查也已任务化

对 `binding_id = 4` 的 `OpenAI Chat` 执行检查：

```http
POST /api/bindings/4/check
```

立即返回：

```json
{
  "id": 5,
  "job_type": "check_binding",
  "status": "running",
  "scope_id": 4,
  "total_steps": 4
}
```

运行中状态曾观测到：

```json
{
  "status": "running",
  "completed_steps": 2,
  "total_steps": 4,
  "progress_percent": 50.0,
  "current_step": "检查 daiju-auto-second · OpenAI Chat · LongCat-Flash-Thinking"
}
```

最终完成状态：

```json
{
  "status": "ok",
  "completed_steps": 4,
  "total_steps": 4,
  "result": {
    "checked": [
      {"model_id": "LongCat-Flash-Chat", "status": "ok", "available": true},
      {"model_id": "LongCat-Flash-Lite", "status": "ok", "available": true},
      {"model_id": "LongCat-Flash-Thinking", "status": "error", "available": false},
      {"model_id": "LongCat-Flash-Thinking-2601", "status": "partial", "available": false}
    ]
  }
}
```

结论：

- 模型检查已经支持后台进度追踪
- 同一协议下不同模型会被分成 `ok / partial / error`

## 5. 搜索与历史仍可用

历史接口最近返回中可看到刚跑完的真实检查记录，例如：

```json
{
  "model_id": "LongCat-Flash-Chat",
  "status": "ok",
  "available": 1,
  "latency_ms": 1369
}
```

```json
{
  "model_id": "LongCat-Flash-Thinking-2601",
  "status": "partial",
  "available": 0,
  "error": "reasoning_only"
}
```

结论：

- 后台任务化没有破坏原有历史记录能力
- 历史仍然可用来证明某个模型曾经成功或失败

## 6. 重启后的中断状态可追踪

服务重启前故意留下未完成任务，重启后再次查询：

```http
GET /api/jobs?limit=10
```

可看到旧任务被自动标记为：

```json
{
  "status": "error",
  "detail": "进程重启，任务中断",
  "error_text": "process_restarted"
}
```

同时调度状态也被从 `running` 收口为：

```json
{
  "last_cycle_status": "error",
  "last_cycle_note": "process_restarted"
}
```

结论：

- 重启后不会再出现任务永久卡死在 `running`
- 后续新任务可继续正常启动

## 7. `qihaze` 强制全量校验已可后台持续执行

对 `key_id = 3` 的 `qihaze` 触发：

```http
POST /api/keys/3/force-audit
```

返回后台任务：

```json
{
  "id": 6,
  "job_type": "force_audit_key",
  "status": "queued",
  "scope_id": 3
}
```

随后查询：

```http
GET /api/jobs/6
```

已确认任务进入运行态，并且在完成协议探测后继续进入逐模型检查：

```json
{
  "status": "running",
  "completed_steps": 6,
  "total_steps": 41,
  "current_step": "检查 qihaze · Claude / Anthropic Messages · gpt-5-codex-mini"
}
```

当前 `GET /api/bindings?key_id=3` 可见：

- `openai_chat`：已检查 5 个，当前可用 0
- `openai_responses`：已检查 12 个，当前可用 0
- `anthropic_messages`：已检查 12 个，当前可用 0

最近历史里也已经持续出现 30 秒超时记录，例如：

```json
{
  "model_id": "gpt-5-codex",
  "status": "error",
  "error": "curl: (28) Operation timed out after 30005 milliseconds with 0 bytes received"
}
```

结论：

- `qihaze` 这种慢且容易超时的站点，现在可以在后台稳定跑完
- 这次已经确认：系统不会因为前端请求断开而中止全量校验

## 8. 验收结论

现在这版已经完成了当前关键闭环：

- 先加站点和 Key
- 自动探测协议
- 每个协议单独列模型
- 每个协议单独校验模型
- 长任务后台执行并可查进度
- 搜索模型时能按可用性过滤
- 进程重启后任务状态不会污染系统

## 9. 仍未覆盖

这轮没有处理的内容：

- Key 加密存储
- 导入导出
- 更细的权限系统
- 大规模性能优化
- 真正独立的任务队列 / worker
