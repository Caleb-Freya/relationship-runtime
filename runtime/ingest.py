"""统一 Event 入口：校验、幂等、副作用（episode 更新 / 回应判定）。"""
import json
import time
import uuid

from .store import Store, EPISODE_STATUSES, iso_to_epoch

REQUIRED = ("event_id", "timestamp", "source", "actor", "type", "context_window_id")
EVENT_TYPES = {
    "user_message", "assistant_message", "user_activity",
    "explicit_departure", "expected_return",
    "contact_attempt", "notification_sent",
    "episode_update", "internal_thought", "settle",
}


class IngestError(ValueError):
    pass


def ingest_event(store: Store, cfg: dict, ev: dict,
                 now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    for k in REQUIRED:
        if not ev.get(k):
            raise IngestError(f"missing required field: {k}")
    if ev["type"] not in EVENT_TYPES:
        raise IngestError(f"unknown event type: {ev['type']}")
    try:
        ts = iso_to_epoch(ev["timestamp"])
    except (ValueError, TypeError) as e:
        # 客户端输入错误，不是服务端的锅：外部 AI/adapter 传错格式的
        # timestamp 应该拿到 400 让它自己改，不是让 api_event 兜不住
        # 变成一个吓人的 500（mcp_server.py api_event 只捕获 IngestError）。
        raise IngestError(f"invalid timestamp: {ev['timestamp']!r}") from e
    if ev.get("payload") is None:
        ev["payload"] = {}

    # 隐私：按配置丢弃/截断对话原文。Runtime 决策只依赖事实，不依赖原文。
    level = cfg.get("privacy", {}).get("store_message_text", "none")
    if "text" in ev["payload"]:
        if level == "none":
            ev["payload"] = {k: v for k, v in ev["payload"].items() if k != "text"}
        elif level == "excerpt":
            ev["payload"]["text"] = str(ev["payload"]["text"])[:200]

    inserted = store.insert_event(ev, received_at=now)
    if not inserted:
        return {"ok": True, "deduped": True, "event_id": ev["event_id"]}

    payload = ev["payload"]

    # 副作用 1：episode 创建/更新（V1 定案：episode 只由显式事件驱动）
    if ev["type"] == "episode_update":
        ep = dict(payload.get("episode") or {})
        if not ep:
            raise IngestError("episode_update requires payload.episode")
        if ep.get("status") and ep["status"] not in EPISODE_STATUSES:
            raise IngestError(f"invalid episode status: {ep['status']}")
        ep.setdefault("episode_id", ev.get("episode_id") or f"ep-{uuid.uuid4().hex[:8]}")
        ep.setdefault("started_at", ts)
        store.upsert_episode(ep, now=ts)
        return {"ok": True, "event_id": ev["event_id"],
                "episode_id": ep["episode_id"]}

    # 副作用 2：用户出现 → 判定"已回应"（冻结规则：窗口内出现 user_message 即回应）
    if ev["type"] == "user_message":
        window = float(cfg["backoff"]["responded_window_hours"])
        store.mark_attempts_responded(responded_at=ts, window_hours=window)

    # 副作用 3：明确离开/DND 记录到 runtime_state，availability 使用
    if ev["type"] == "explicit_departure":
        store.set_state("departure", str(ts))
        store.set_state("departure_payload", json.dumps(payload, ensure_ascii=False))
    if ev["type"] == "user_message" or ev["type"] == "user_activity":
        store.set_state("departure", "")  # 人出现了，离开状态解除

    return {"ok": True, "event_id": ev["event_id"]}
