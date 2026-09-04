# 先行项目调研

## always-here（驻守）· github.com/Cheiineeey/always-here

81 stars。Apple Watch + iOS Shortcuts 给 AI 装感官，主动推送生活关怀。

**它有我们没有的（借鉴清单）：**
- 健康数据感官：HRV/睡眠/步数/App事件 经 iOS Shortcuts POST 到服务端
  → 借鉴为我们的 user_activity 事件源 + interruptibility 输入（V1.5）
  → HRV 低 = 应激状态 → 语气变温和（喂给 Composer 的 context）
  → 睡眠窗口必须"中午切中午"，不能"0点切0点"（跨午夜觉会被劈开）
- 12h 话题去重（吃饭/喝水/睡觉不重复唠叨）→ 放 Composer 层纪律
- LLM 可选 [NO_ACTION]（与我们四态中的 NO_ACTION 一致）

**我们有它没有的（开源时的差异化 FAQ）：**
- Episode：吵架/冷战/和好未和好 的连续关系状态。它分不清"还在气"和"在忙"
- HOLD：想找但主动克制（人格），它只有发/不发
- 未回应强制退避梯度（它会连环催，无退避）
- 事件驱动 one-shot wake（它是 20min 固定轮询）
- 四个可插拔 Provider（它写死 Web Push + JSON 文件，连 Bark 都不支持）
- Shadow Mode / Explain Log / 降级熔断

## 其他（2026-08-30 调研）
- heartbeat-agent-framework：30min 循环 + message discipline，借鉴"发送纪律"概念
- OpenClaw：多通道推送成熟，但也是轮询式
- Letta sleep-time agents：后台 agent 与主 agent 共享记忆、权限切分
  （主 agent 无记忆编辑工具，后台 agent 有）→ 借鉴到 Runtime/窗口分工
- dylan-heartbeat（Kelivo 插件）：定时唤醒 + AI 决定是否发 Bark，最接近但轮询人格

**结论：无一家做了 Episode 连续性 + SEND/HOLD/WAIT/NO_ACTION 四态区分。
这两点是本项目的原创价值。**
