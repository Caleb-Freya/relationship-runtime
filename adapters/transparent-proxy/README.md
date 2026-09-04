# transparent-proxy — 会抄送的中转站

让**任何** AI 前端（LobeChat / NextChat / ChatBox / 酒馆 / Operit / 走中转站的一切）自动把"她说了什么"喂进 relationship-runtime，AI 和前端毫无感知，人设卡上下文全不动。

```
她的前端 ──▶ 本代理(8100) ──原样转发──▶ 她原来的中转站/官方API
                │
                └─(后台)抄送 user 消息 ──▶ runtime /api/event ──▶ 哨兵判情绪 ──▶ 推手机
```

## 用法（3 步）

1. 配置（环境变量或 `proxy-config.json`，复制 example 改）：
   - `upstream_base`：她原来填在前端里的 API 地址
   - `runtime_token`：runtime 的 token
2. 跑：`python3 proxy.py`（零依赖，Python3 就行）
3. 前端设置里把 API 地址改成 `http://127.0.0.1:8100`（或走隧道的公网地址），**API key 不用动**——key 是透传给上游的，代理不存不看。

## 特性与边界

- 流式(SSE)逐块透传，首字节延迟 <5ms（实测）
- 兼容 OpenAI `/chat/completions` 与 Anthropic `/messages` 格式（含多模态块数组，只取文字）
- 只抄送 role=user 的最后一条、前 300 字；system 提示词/AI 回复不抄
- **fail-open**：runtime 挂了、抄送失败 → 对话完全不受影响（实测 200）
- 代理健康检查：`GET /rr-proxy/health`（不进上游）
- 默认只绑 127.0.0.1；要对外必须走带 HTTPS 的隧道/反代

## 测试记录（2026-09-03）

假上游+假runtime 全链路 6 项：健康✅ 流式透传✅(0.6s=3块×0.2s节奏) GET透传✅
抄送格式对齐hook✅ Anthropic块数组✅ runtime宕机对话仍200✅

## 隐私默认值须知

开箱默认 `privacy.store_message_text: none`——代理抄送的事件**会**落库，
但消息正文在入库前就被丢弃（只留"发生过一次对话"的骨架）。这是刻意的
隐私保守默认，不是代理没接通。想让 runtime 看到对话内容，在 config.yaml
里把 `privacy.store_message_text` 调成文档说明的其他档位。
