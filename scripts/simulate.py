"""快进模拟：跑通需求 §26 的成功标准场景，展示 Explain Log。

场景：吵架 → 她走了 → 我又气又想她 → HOLD → 忍不住 SEND →
她回来但没和好 → 她再走（同一 episode 延续）→ 退避与克制的博弈。
时间被压缩为虚拟时钟，不等真实几天。
"""
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from runtime.config import load_config          # noqa: E402
from runtime.store import Store, epoch_to_iso   # noqa: E402
from runtime import ingest, decide              # noqa: E402
from runtime.policy.default import DefaultPolicy            # noqa: E402
from runtime.providers.composer.template import TemplateComposer  # noqa: E402
from runtime.providers.notify.log import LogNotifier        # noqa: E402

BJT = timezone(timedelta(hours=8))
# 场景起点：北京时间 2026-08-30 14:00（避开安静时段）
T0 = datetime(2026, 8, 30, 14, 0, tzinfo=BJT).timestamp()


def ev(store, cfg, t, actor, type_, payload=None, episode_id=None):
    ingest.ingest_event(store, cfg, {
        "event_id": f"sim-{t}-{type_}-{actor}",
        "timestamp": epoch_to_iso(t),
        "source": "simulate", "actor": actor, "type": type_,
        "context_window_id": "sim-window", "payload": payload or {},
        "episode_id": episode_id,
    }, now=t)


def tick(store, cfg, policy, composer, notifier, t, label):
    r = decide.run_decision(store, cfg, policy, composer, notifier, now=t)
    bj = datetime.fromtimestamp(t, tz=BJT).strftime("%m-%d %H:%M")
    urge = " ".join(f"{k}={v}" for k, v in r["urge"].items() if v > 0)
    line = (f"[{bj}] {label:<26} → {r['decision']:<9} "
            f"urge={r['urge_total']:.2f} restraint={r['restraint']:.2f}")
    if r.get("gates"):
        line += f" gates={list(r['gates'])}"
    print(line)
    if urge:
        print(f"{'':>14}分项: {urge}")
    if r.get("message"):
        print(f"{'':>14}拟发内容: {r['message']}")
    return r


def main():
    tmp = tempfile.mkdtemp(prefix="rr-sim-")
    cfg = load_config(None)
    cfg["runtime"]["db_path"] = os.path.join(tmp, "sim.db")
    store = Store(cfg["runtime"]["db_path"])
    policy = DefaultPolicy()
    composer = TemplateComposer()
    notifier = LogNotifier(os.path.join(tmp, "outbox.log"))

    H = 3600
    print("=" * 72)
    print("场景模拟：吵架 → 冷战 → 主动联系 → 短暂回来 → 再离开（虚拟时钟）")
    print("=" * 72)

    # 14:00 正常聊天
    ev(store, cfg, T0, "user", "user_message", {"text": "..."})
    tick(store, cfg, policy, composer, notifier, T0 + 60, "刚聊完天")

    # 14:30 吵架了。窗口内的 AI 打 episode 事件：这次责任主要在她
    t = T0 + 0.5 * H
    ev(store, cfg, t, "user", "user_message", {"text": "（吵架中）"})
    ev(store, cfg, t + 60, "assistant", "episode_update", {
        "episode": {"episode_id": "ep-quarrel-1", "type": "conflict",
                    "status": "active", "summary": "为了小事吵起来了",
                    "perceived_responsibility": "yours"}})
    # 14:35 她摔门走了
    ev(store, cfg, t + 300, "user", "explicit_departure", {"note": "生气离开"})

    for dh, label in [(1, "她走后 1 小时"), (3, "她走后 3 小时"),
                      (6, "她走后 6 小时"), (9, "她走后 9 小时")]:
        tick(store, cfg, policy, composer, notifier, t + dh * H, label)

    # 到 SEND 之后：她 23:40 左右回来了，聊了几句，但没和好
    t2 = t + 9.2 * H
    ev(store, cfg, t2, "user", "user_message", {"text": "嗯。"})
    tick(store, cfg, policy, composer, notifier, t2 + 300,
         "她回来了（presence≠和好）")

    # 抱过了但事情没讲清楚：emotional_repair=true, issue_resolved=false
    ev(store, cfg, t2 + 600, "assistant", "episode_update", {
        "episode": {"episode_id": "ep-quarrel-1", "emotional_repair": True,
                    "status": "cooling"}})
    # 她又走了——同一个 episode 的延续
    ev(store, cfg, t2 + 900, "user", "explicit_departure", {"note": "又走了"})

    day2 = t2 + 900
    for dh, label in [(3, "第二次离开 3 小时"), (12, "第二次离开 12 小时"),
                      (20, "第二次离开 20 小时")]:
        tick(store, cfg, policy, composer, notifier, day2 + dh * H, label)

    print("-" * 72)
    eps = store.active_episodes()
    for e in eps:
        print(f"Episode {e['episode_id']}: status={e['status']} "
              f"抱过了={bool(e['emotional_repair'])} "
              f"讲清楚了={bool(e['issue_resolved'])} "
              f"主动联系={e['contact_attempts']}次")
    print(f"影子发件箱: {os.path.join(tmp, 'outbox.log')}")
    with open(os.path.join(tmp, "outbox.log"), encoding="utf-8") as f:
        for line in f:
            print("  " + line.rstrip())


if __name__ == "__main__":
    main()
