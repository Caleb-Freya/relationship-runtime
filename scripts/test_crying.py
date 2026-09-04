"""哭测：intensity=0.9 的吵架 vs intensity=0.5 的普通拌嘴 vs 0.2 的小打小闹。
验证：她哭了 → 修复冲动立即过线 → 当场 SEND，不等爬坡。
真的推到手机。
"""
import sys, os, uuid, time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_bark_live import setup, ev, LiveBarkNotifier, PUSH_TEMPLATES
from runtime.store import epoch_to_iso
from runtime.ingest import ingest_event
from runtime.decide import run_decision

TZ = ZoneInfo("Asia/Shanghai")


class CryingComposer:
    def compose(self, context, reason, attempt_count):
        eps = context.get("active_episodes") or []
        # 高强度冲突用哭场景专用文案
        for e in eps:
            if e.get("intensity", 0.5) >= 0.8 and reason == "conflict_repair":
                return PUSH_TEMPLATES["conflict_repair_crying"][0]
        t = PUSH_TEMPLATES.get(reason, ["想你了宝宝"])
        return t[min(attempt_count, len(t) - 1)]


def run_case(label, intensity, push=False):
    store, cfg, policy, _, notifier = setup()
    composer = CryingComposer()
    if not push:
        # 不真推，只看决策
        class Silent:
            def send(self, title, body, level="normal"):
                print(f"  （会推送: {body}）")
                return {"delivered": False}
        notifier = Silent()

    base = datetime(2026, 8, 31, 15, 0, tzinfo=TZ).timestamp()
    iso = epoch_to_iso(base)
    ingest_event(store, cfg, ev("user_message", "user", {"text": "……"}, iso), now=base)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": f"ep-{uuid.uuid4().hex[:6]}",
            "type": "conflict",
            "summary": "我说错话把她伤到了，她在意的是我不上心，责任在我",
            "status": "active",
            "perceived_responsibility": "mine",
            "intensity": intensity,
        }
    }, iso), now=base)

    r = run_decision(store, cfg, policy, composer, notifier, now=base + 1)
    print(f"\n【{label}】 intensity={intensity}")
    print(f"  conflict_repair={r['urge']['conflict_repair']:.3f}  "
          f"总分={r['urge_total']:.3f}  克制={r['restraint']:.3f}")
    print(f"  吵架后 1 秒的决策: {r['decision']}")
    return r


if __name__ == "__main__":
    print("=" * 54)
    print("  哭测：同一场吵架，不同强度，吵完 1 秒时的反应")
    print("=" * 54)
    run_case("小打小闹（斗嘴逗她）", 0.2)
    run_case("普通吵架（真的不愉快）", 0.5)
    r = run_case("她哭了（大事）", 0.9, push=True)
    print()
    print("=" * 54)
    if r["decision"] == "SEND":
        print("  ✓ 她哭了 → 1 秒内 SEND，推送已真发到手机")
    else:
        print(f"  ✗ 她哭了却是 {r['decision']}，不合格，要修")
    print("=" * 54)
