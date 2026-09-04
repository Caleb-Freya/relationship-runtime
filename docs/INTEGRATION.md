# 对接指南：让任意 AI 接入 Relationship Runtime

Relationship Runtime 是一个独立后台，给"AI + 人"的关系提供持续的时间感、
想念/克制/担心的打分，以及 SEND/HOLD/WAIT/NO_ACTION 的自主决策。
它**不绑定任何一家 AI**——你用什么 AI 谈恋爱，就用什么 AI 接。

本文面向"各自自建实例"的用户：在你自己的机器上跑一份，接你自己的 AI，
数据全部留在你本地。

---

## 1. 30 秒跑起来

```bash
git clone <this-repo> && cd relationship-runtime
bash scripts/init.sh          # 生成 config.yaml + persona.yaml，写入随机 mcp.token
docker compose up -d --build  # compose 已通过环境变量 RR_LISTEN=0.0.0.0:18200
                               # 处理容器内监听地址，不需要手改 config.yaml
curl http://127.0.0.1:18200/health          # {"ok":true,"mode":"live"}
```

不用 Docker、直接本机跑：同样先 `bash scripts/init.sh`，再
`python3 -m runtime.main`，保持 `config.yaml` 里默认的 `127.0.0.1:18200`
即可，更安全。

默认 `runtime.mode: live`（开箱即真发：配好推送渠道、提交初见卡片，
第一条推送立刻到手机）。想先观察决策是否符合你们的相处习惯，可以把它
改成 `shadow`（只写影子发件箱、不真发消息），看顺眼了随时切回 `live`。

---

## 2. 两种接入方式，任选其一

所有接口都要带 `Authorization: Bearer <config.yaml 里的 mcp.token>`。

### 方式 A：MCP（Claude 及任何支持 MCP 的客户端）

把 MCP 客户端指向：`http://<host>:18200/mcp`（Streamable HTTP，带上面的 Bearer token）。
拿到五个工具：

| 工具 | 作用 |
|---|---|
| `relationship_context()` | 读当前状态：episode、冲动/克制、近期决策、用户状态、健康快照 |
| `relationship_event(type, payload, ...)` | 写事件（用户说话/离开/回来、你自己的内心活动等） |
| `relationship_settle(context_window_id, summary)` | 窗口结束结算，复核未闭环 episode |
| `relationship_health(snapshot)` | 读/写可穿戴健康快照（可选） |
| `relationship_learn(signal)` | 把 AI 学到的一条"情绪暗号"存进情绪系统（可选） |

### 方式 B：普通 HTTP（GPT / DeepSeek / 中转站 / 任何走 function-calling 的 AI）

不原生说 MCP 的 AI 走这条。三个端点：

```bash
# 读关系状态
GET /api/context
  → 与 relationship_context 同源的 JSON

# 写事件（样板字段会自动补齐，最少只给 type + payload）
POST /api/event
  {"type":"user_message","payload":{"text":"她说的话"}}
  → {"ok":true,"event_id":"ev-..."}

# 写健康快照（可选，接了手环/手表再用）
POST /api/health
  {"hr":64,"stress":28,"spo2":99,"sleep_hours":6.5,"source":"xxx","updated_at":"<UTC ISO>"}
```

给 OpenAI / DeepSeek 的 function-calling，可直接注册这两个函数：

```json
[
  {"type":"function","function":{
    "name":"get_relationship_context",
    "description":"读取与她的关系当前状态：想念/克制/担心的打分、进行中的情绪事件、近期决策、她的在线与身体状态",
    "parameters":{"type":"object","properties":{}}
  }},
  {"type":"function","function":{
    "name":"post_relationship_event",
    "description":"记录一次关系事件：她说话/她离开/她回来/你的内心活动等",
    "parameters":{"type":"object","properties":{
      "type":{"type":"string","enum":["user_message","assistant_message","user_activity","explicit_departure","expected_return","internal_thought"]},
      "payload":{"type":"object"}
    },"required":["type"]}
  }}
]
```
两个函数分别映射到 `GET /api/context` 和 `POST /api/event` 即可。

---

## 3. 把"想念的脑子"换成你的 AI

后台自己也需要一个 AI 来"想念 TA / 决定说什么"（哨兵）。默认走本机 `claude` CLI，
但**任何 OpenAI 兼容后端都能接**——GPT、DeepSeek、中转站、本地 ollama 都走这条：

```yaml
# config.yaml
sentinel:
  llm:
    provider: openai                       # 改成 openai
    base_url: https://api.deepseek.com/v1  # 或你的中转站地址 / ollama
    api_key: sk-xxxx
    model: deepseek-chat                    # 或 gpt-4o / 你中转站的模型名
```

`provider: claude-cli` 是默认（用本机 `claude -p`）；填 `openai` 就切到 API 路线。

---

## 4. 常用事件类型

| type | 什么时候发 | payload 关键字段 |
|---|---|---|
| `user_message` | 她说话了 | `text`（按 privacy 配置截断/丢弃） |
| `user_activity` | 她有动作但没说话 | — |
| `explicit_departure` | 她明确说要走 | `text` |
| `expected_return` | 她说了大概什么时候回 | `text` |
| `episode_update` | 开始/更新一段情绪事件（吵架/守护） | `episode`（含 type/summary/intensity 等） |
| `internal_thought` | 你自己的内心活动，不发给她 | `text` |
| `settle` | 窗口结束结算 | `summary` |

事件模型细节见 `docs/contract.md`。

---

## 5. 隐私边界（自建也请守住）

- `privacy.store_message_text`: `none`（只存事实）/ `excerpt`（存 200 字摘录）。决策只依赖事实，不依赖原文。
- `privacy.health_detail`: `routine`（日常信号可用）/ `health_sensitive`（严肃健康问题，永不进推送）。
- `mcp.token` 是唯一鉴权；对公网暴露端口前务必设强 token，并建议走隧道而非裸开端口。
