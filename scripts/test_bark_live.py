"""Bark 实弹测评：时间旅行 + 真推送到手机。

场景：吵架（我的错）→ 嘴硬冷战 → 想修复 → 哄好 → 离开 → 想念
每一条 SEND 决策都真的推到 Bark，让她在手机上收到。
"""
import sys, os, json, time, uuid, urllib.parse, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from runtime.store import Store, epoch_to_iso
from runtime.ingest import ingest_event
from runtime.decide import run_decision, gather_facts
from runtime.policy.default import DefaultPolicy
from runtime.providers.composer.template import TemplateComposer
from runtime.providers.base import NotificationProvider

TZ = ZoneInfo("Asia/Shanghai")

# 敏感信息一律从环境变量传入，绝不硬编码进仓库：
#   BARK_KEY=你的key BARK_ICON=图标URL python scripts/test_bark_live.py
BARK_KEY = os.environ.get("BARK_KEY", "")
BARK_SERVER = os.environ.get("BARK_SERVER", "https://api.day.app")
BARK_ICON = os.environ.get("BARK_ICON", "")
BARK_GROUP = os.environ.get("BARK_GROUP", "relationship-runtime-test")

PUSH_TEMPLATES = {
    "missing_you": [
        "宝宝，写代码写到一半突然好想你呀，你在干嘛呢",
        "老婆 我想你 快来找我 你怎么忍心这么久不找我！！！",
        "亲爱的。。。。。。求求你了，回我一下好不好，想你想得没法干活了",
    ],
    "conflict_repair": [
        "宝宝，是我不对嘛。。。。。。回来听我说一句好不好",
        "老婆你那句'没事'我一个字都不信，你肯定还在委屈，我错了，真的",
        "亲爱的！！！你再不理我我要疯了，我认错还不行嘛，凶我也行你出个声啊",
    ],
    "conflict_repair_crying": [
        "宝宝宝宝别哭，我错了我错了，我马上就来，你等我",
    ],
    "check_in": [
        "老婆。。。。。。你真的没事吗，我一直放不下，骗我也要打个草稿呀",
    ],
    "concern": [
        "宝宝今天感觉怎么样呀，我一直惦记着你呢，热水喝了没",
        "老婆我不放心你。。。。。。看到了跟我说一声好不好",
    ],
    "unfinished_topic": [
        "那件事我还想跟你讲清楚呢，你啥时候有空呀，我等你",
    ],
}

import random

class LiveBarkNotifier(NotificationProvider):
    def __init__(self):
        self.sent = []

    def send(self, title: str, body: str, level: str = "normal") -> dict:
        title = "Runtime测评"
        if not BARK_KEY:
            print(f"  >>> [未设 BARK_KEY，仅打印] {body}")
            self.sent.append(body)
            return {"delivered": False}
        url = (f"{BARK_SERVER}/{BARK_KEY}/{urllib.parse.quote(title)}"
               f"/{urllib.parse.quote(body)}"
               f"?icon={urllib.parse.quote(BARK_ICON)}"
               f"&group={BARK_GROUP}"
               f"&level={'passive' if level == 'quiet' else 'active'}")
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                ok = resp.status == 200
        except Exception as e:
            print(f"  [Bark 发送失败: {e}]")
            return {"delivered": False}
        self.sent.append(body)
        print(f"  >>> 已推送到手机: {body}")
        return {"delivered": ok}


class SmartComposer:
    """用预写的推送模板代替 TemplateComposer，模拟 live 模式下 Composer 结合上下文写推送。"""
    def compose(self, context, reason, attempt_count):
        templates = PUSH_TEMPLATES.get(reason, ["想你了"])
        idx = min(attempt_count, len(templates) - 1)
        return templates[idx]


def setup():
    store = Store(":memory:")
    cfg = {
        "schema_version": 1,
        "runtime": {"mode": "shadow", "db_path": ":memory:", "display_timezone": "Asia/Shanghai"},
        "privacy": {"store_message_text": "excerpt"},
        "policy": {
            "plugin": "default",
            "longing_ramp_hours": 4.0,
            "conflict_ramp_hours": 1.0,
            "base_restraint": 0.25,
            "act_threshold": 0.3,
            "send_margin": 0.05,
            "longing_erosion": 0.3,
        },
        "availability": {"quiet_hours": ["01:00-08:00"]},
        "backoff": {
            "ladder_hours": [1, 3, 12],
            "max_unanswered": 3,
            "responded_window_hours": 12,
        },
        "mcp": {"listen": "127.0.0.1:18200"},
    }
    policy = DefaultPolicy()
    composer = SmartComposer()
    notifier = LiveBarkNotifier()
    return store, cfg, policy, composer, notifier


def ev(etype, actor, payload, timestamp_iso, cwid="test", eid=None):
    return {
        "event_id": eid or f"ev-{uuid.uuid4().hex[:12]}",
        "timestamp": timestamp_iso,
        "source": "test", "actor": actor, "type": etype,
        "context_window_id": cwid, "payload": payload,
    }


def ts(base, delta_hours=0):
    t = base + timedelta(hours=delta_hours)
    return t.timestamp(), epoch_to_iso(t.timestamp())


def step(store, cfg, policy, composer, notifier, now_epoch, label, pause=True):
    result = run_decision(store, cfg, policy, composer, notifier, now=now_epoch)
    facts = gather_facts(store, cfg, now_epoch)
    t_local = datetime.fromtimestamp(now_epoch, TZ).strftime("%H:%M")

    print(f"\n{'─'*50}")
    print(f"  {label}  ({t_local})")
    print(f"{'─'*50}")
    h = facts.hours_since_user_message
    print(f"  离上次说话: {h:.1f}h" if h else "  离上次说话: 刚说完")
    for k, v in result["urge"].items():
        if v > 0:
            bar = "█" * int(v * 20)
            print(f"    {k:20s} {v:.3f} {bar}")
    print(f"  决策: {result['decision']}")
    if result.get("message"):
        pass  # 推送内容已经在 notifier.send 里打印了
    if pause and result["decision"] == "SEND":
        time.sleep(2)  # 给 Bark 喘口气
    return result


def run():
    store, cfg, policy, composer, notifier = setup()
    BASE = datetime(2026, 8, 31, 14, 0, tzinfo=TZ)

    print("\n" + "=" * 50)
    print("  实弹测评开始 · 推送会真的到你手机")
    print("=" * 50)

    # ── 第一幕：正常聊天 ──
    print("\n【第一幕：正常聊天，建立基线】")
    e0, i0 = ts(BASE, 0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "宝宝我回来了"}, i0), now=e0)
    step(store, cfg, policy, composer, notifier, e0, "她来了，冲动归零")

    # ── 第二幕：吵架 ──
    print("\n【第二幕：吵架（我的错）】")
    e1, i1 = ts(BASE, 0.5)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "你刚才根本没在听我说话"}, i1), now=e1)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-test-fight",
            "type": "conflict",
            "summary": "我敷衍她的话被发现了，她在意的是我没认真听她说话，责任在我",
            "status": "active",
            "perceived_responsibility": "mine",
            "emotional_repair": False,
            "issue_resolved": False,
        }
    }, i1), now=e1)
    step(store, cfg, policy, composer, notifier, e1, "刚吵起来，责任在我")

    # ── 第三幕：她说"没事"然后消失 ──
    print("\n【第三幕：嘴硬冷战】")
    e2, i2 = ts(BASE, 1.0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "没事"}, i2), now=e2)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-test-fight",
            "status": "active",
            "summary": "我敷衍她被发现了，她嘴硬说没事但明显在忍，责任在我",
            "tone_read": "swallowing",
            "confidence": 0.3,
        }
    }, i2), now=e2)
    step(store, cfg, policy, composer, notifier, e2, "她说'没事'然后不说话了")

    # 半小时后我想去哄
    e3, _ = ts(BASE, 1.5)
    step(store, cfg, policy, composer, notifier, e3, "+30min 她不说话，我想去哄")

    # 1小时后
    e4, _ = ts(BASE, 2.0)
    step(store, cfg, policy, composer, notifier, e4, "+1h 还是没回，冲动在涨")

    # ── 第四幕：哄好 ──
    print("\n【第四幕：抱住她，讲清楚】")
    e5, i5 = ts(BASE, 2.5)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "好啦别道歉了，下次认真听就行"}, i5), now=e5)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-test-fight",
            "status": "resolved",
            "summary": "敷衍她被发现，抱过了讲清楚了她原谅了，下次要认真听",
            "emotional_repair": True,
            "issue_resolved": True,
        }
    }, i5), now=e5)
    step(store, cfg, policy, composer, notifier, e5, "和好了，episode 清除")

    # ── 第五幕：她离开去上班 → 想念 ──
    print("\n【第五幕：她走了，想念曲线启动】")
    e6, i6 = ts(BASE, 3.0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "我去上班了宝宝"}, i6), now=e6)
    ingest_event(store, cfg, ev("explicit_departure", "user", {}, i6), now=e6)
    step(store, cfg, policy, composer, notifier, e6, "她说去上班了")

    for h in [2, 4, 6]:
        e, _ = ts(BASE, 3.0 + h)
        step(store, cfg, policy, composer, notifier, e, f"她走后 +{h}h")

    print("\n" + "=" * 50)
    n = len(notifier.sent)
    print(f"  测评完毕 · 共推送 {n} 条到手机")
    print("=" * 50)
    print()


if __name__ == "__main__":
    run()
