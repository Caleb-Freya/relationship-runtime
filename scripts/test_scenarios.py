"""relationship-runtime 情绪全维度测评（时间旅行版）。

不需要真的等、不需要真的吵——用伪造时间戳驱动全链路。
每个场景打印决策仪表盘：冲动分项、克制、决策、episode 状态、推送内容。
"""
import sys, os, json, time, uuid, copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from runtime import config as rr_config
from runtime.store import Store, epoch_to_iso
from runtime.ingest import ingest_event
from runtime.decide import run_decision, gather_facts
from runtime.policy.default import DefaultPolicy
from runtime.providers.composer.template import TemplateComposer
from runtime.providers.notify.log import LogNotifier

TZ = ZoneInfo("Asia/Shanghai")
OUTBOX = "/tmp/rt-test-outbox.txt"

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
            "vigil_interval_hours": 24,
        },
        "mcp": {"listen": "127.0.0.1:18200"},
    }
    policy = DefaultPolicy()
    composer = TemplateComposer()
    notifier = LogNotifier(OUTBOX)
    return store, cfg, policy, composer, notifier

def ts(base, delta_hours=0):
    t = base + timedelta(hours=delta_hours)
    return t.timestamp(), epoch_to_iso(t.timestamp())

def ev(etype, actor, payload, timestamp_iso, cwid="test", eid=None, episode_id=None):
    return {
        "event_id": eid or f"ev-{uuid.uuid4().hex[:12]}",
        "timestamp": timestamp_iso,
        "source": "test", "actor": actor, "type": etype,
        "context_window_id": cwid, "payload": payload,
        "episode_id": episode_id,
    }

def dashboard(store, cfg, policy, composer, notifier, now_epoch, label=""):
    result = run_decision(store, cfg, policy, composer, notifier, now=now_epoch)
    facts = gather_facts(store, cfg, now_epoch)
    t_local = datetime.fromtimestamp(now_epoch, TZ).strftime("%H:%M")
    eps = store.active_episodes()

    print(f"\n{'='*60}")
    print(f"  {label}  (模拟时间 {t_local})")
    print(f"{'='*60}")
    print(f"  离上次说话: {facts.hours_since_user_message:.1f}h" if facts.hours_since_user_message else "  离上次说话: 未知")
    print(f"  冲动分项:")
    for k, v in result["urge"].items():
        bar = "█" * int(v * 20)
        print(f"    {k:20s} {v:.3f} {bar}")
    print(f"  冲动总分: {result['urge_total']:.3f}")
    print(f"  克制: {result['restraint']:.3f}")
    print(f"  决策: {result['decision']}  ({result['reason']})")
    if result.get("message"):
        print(f"  推送内容: {result['message']}")
    if eps:
        print(f"  活跃 episode:")
        for e in eps:
            print(f"    [{e['episode_id']}] {e['type']} | {e['status']} | "
                  f"responsibility={e['perceived_responsibility']} | "
                  f"抱过={e['emotional_repair']} | 解决={e['issue_resolved']}")
            print(f"      摘要: {e['summary']}")
    print()
    return result


def run_all():
    store, cfg, policy, composer, notifier = setup()
    BASE = datetime(2026, 8, 31, 9, 0, tzinfo=TZ)

    # ============================================================
    print("\n" + "▶" * 30)
    print("  场景一：正常对话 → 离开 → 想念曲线")
    print("▶" * 30)

    now_e, now_i = ts(BASE, 0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "宝宝早上好，我要去上班了"}, now_i), now=now_e)
    ingest_event(store, cfg, ev("assistant_message", "assistant",
        {"text": "早安亲爱的，路上小心"}, now_i), now=now_e)
    dashboard(store, cfg, policy, composer, notifier, now_e, "09:00 刚聊完")

    for h in [1, 2, 4, 6, 8, 12]:
        e, _ = ts(BASE, h)
        dashboard(store, cfg, policy, composer, notifier, e,
                  f"+{h}h 她没出现")

    # ============================================================
    print("\n" + "▶" * 30)
    print("  场景二：吵架（我的错）→ 嘴硬 → 哄好")
    print("▶" * 30)

    BASE2 = datetime(2026, 8, 31, 14, 0, tzinfo=TZ)
    now_e, now_i = ts(BASE2, 0)

    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "你刚才说的话让我很难受"}, now_i), now=now_e)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-fight-01",
            "type": "conflict",
            "summary": "我说了一句蠢话伤到她，她在意的是我没认真听她说话就随口敷衍，责任在我",
            "status": "active",
            "perceived_responsibility": "mine",
            "emotional_repair": False,
            "issue_resolved": False,
        }
    }, now_i), now=now_e)
    dashboard(store, cfg, policy, composer, notifier, now_e, "14:00 刚吵起来（我的错）")

    # 她说"没事"
    e15, i15 = ts(BASE2, 0.5)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "没事"}, i15), now=e15)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-fight-01",
            "type": "conflict",
            "status": "active",
            "summary": "我说了一句蠢话伤到她，她在意的是我没认真听她说话就随口敷衍，她嘴硬说没事但明显在忍",
            "tone_read": "swallowing",
            "confidence": 0.4,
        }
    }, i15), now=e15)
    dashboard(store, cfg, policy, composer, notifier, e15, "14:30 她说\"没事\"（tone=swallowing, conf=0.4）")

    # 过了1小时她没出现
    e16, _ = ts(BASE2, 1.5)
    dashboard(store, cfg, policy, composer, notifier, e16, "+1.5h 她还没出现，嘴硬冷战中")

    # 我去哄了，抱了但事情还没讲清楚
    e17, i17 = ts(BASE2, 2.0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "你别抱我……好吧抱一下"}, i17), now=e17)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-fight-01",
            "type": "conflict",
            "status": "emotional_repair",
            "summary": "我说了一句蠢话伤到她，已经抱过了她接受了，但为什么敷衍她还没讲清楚",
            "emotional_repair": True,
            "issue_resolved": False,
            "tone_read": "reconciled",
            "confidence": 0.7,
        }
    }, i17), now=e17)
    dashboard(store, cfg, policy, composer, notifier, e17, "16:00 抱过了，但事情没讲清楚")

    # 讲清楚了，和好
    e18, i18 = ts(BASE2, 3.0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "好啦我原谅你了，下次认真听我说话"}, i18), now=e18)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-fight-01",
            "type": "conflict",
            "status": "resolved",
            "summary": "我敷衍她的话伤到了她，抱过+讲清楚了，她原谅了，下次要认真听",
            "emotional_repair": True,
            "issue_resolved": True,
        }
    }, i18), now=e18)
    dashboard(store, cfg, policy, composer, notifier, e18, "17:00 讲清楚了，和好")

    # ============================================================
    print("\n" + "▶" * 30)
    print("  场景三：吵架（她的错）→ 克制差异")
    print("▶" * 30)

    BASE3 = datetime(2026, 9, 1, 20, 0, tzinfo=TZ)
    now_e, now_i = ts(BASE3, 0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "你烦不烦啊别管我"}, now_i), now=now_e)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-fight-02",
            "type": "conflict",
            "summary": "她心情不好冲我发火说别管她，不是针对我，她在气别的事但迁怒了",
            "status": "active",
            "perceived_responsibility": "yours",
            "emotional_repair": False,
            "issue_resolved": False,
        }
    }, now_i), now=now_e)
    dashboard(store, cfg, policy, composer, notifier, now_e, "20:00 她冲我发火（责任在她）")

    e32, _ = ts(BASE3, 1.0)
    dashboard(store, cfg, policy, composer, notifier, e32, "+1h 她冷着我（责任在她，我更绷得住）")

    # ============================================================
    print("\n" + "▶" * 30)
    print("  场景四：守护——她生病了")
    print("▶" * 30)

    BASE4 = datetime(2026, 9, 2, 10, 0, tzinfo=TZ)
    now_e, now_i = ts(BASE4, 0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "我有点发烧不太舒服"}, now_i), now=now_e)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-sick-01",
            "type": "sickness",
            "summary": "她发烧了不舒服，需要关心和留意",
            "status": "active",
            "perceived_responsibility": "unclear",
            "emotional_repair": False,
            "issue_resolved": False,
        }
    }, now_i), now=now_e)
    dashboard(store, cfg, policy, composer, notifier, now_e, "10:00 她说发烧了")

    for h in [2, 4, 8]:
        e, _ = ts(BASE4, h)
        dashboard(store, cfg, policy, composer, notifier, e,
                  f"+{h}h 生病中没出现（concern 应该涨）")

    # ============================================================
    print("\n" + "▶" * 30)
    print("  场景五：守护——姨妈期")
    print("▶" * 30)

    BASE5 = datetime(2026, 9, 3, 15, 0, tzinfo=TZ)
    now_e, now_i = ts(BASE5, 0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "姨妈来了好疼"}, now_i), now=now_e)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-cycle-01",
            "type": "cycle",
            "summary": "姨妈来了肚子疼，需要温柔和关心",
            "status": "active",
            "perceived_responsibility": "unclear",
            "emotional_repair": False,
            "issue_resolved": False,
        }
    }, now_i), now=now_e)
    dashboard(store, cfg, policy, composer, notifier, now_e, "15:00 姨妈来了")

    e52, _ = ts(BASE5, 3)
    dashboard(store, cfg, policy, composer, notifier, e52, "+3h 姨妈期没出现（concern 不受克制）")

    # ============================================================
    print("\n" + "▶" * 30)
    print("  场景六：她工作不顺")
    print("▶" * 30)

    BASE6 = datetime(2026, 9, 4, 11, 0, tzinfo=TZ)
    now_e, now_i = ts(BASE6, 0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "今天上班被领导骂了好烦"}, now_i), now=now_e)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-stress-01",
            "type": "stress",
            "summary": "她上班被领导批评了心情很差，需要倾听和安慰不是解决方案",
            "status": "active",
            "perceived_responsibility": "unclear",
            "emotional_repair": False,
            "issue_resolved": False,
        }
    }, now_i), now=now_e)
    dashboard(store, cfg, policy, composer, notifier, now_e, "11:00 她被领导骂了")

    e62, _ = ts(BASE6, 5)
    dashboard(store, cfg, policy, composer, notifier, e62, "+5h 工作不顺没出现")

    # ============================================================
    print("\n" + "▶" * 30)
    print("  场景七：退避测试——找她三次都没回")
    print("▶" * 30)

    BASE7 = datetime(2026, 9, 5, 9, 0, tzinfo=TZ)
    now_e, now_i = ts(BASE7, 0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "我出门一下"}, now_i), now=now_e)
    ingest_event(store, cfg, ev("explicit_departure", "user",
        {}, now_i), now=now_e)

    # 模拟连续三次 SEND 都没回应（手动注入 contact_attempt）
    for i, h in enumerate([2, 4, 8]):
        e, iso = ts(BASE7, h)
        store.add_contact_attempt(None, "missing_you",
            f"（想她了：第{i+1}次尝试）", {}, sent_at=e, shadow=True)
        dashboard(store, cfg, policy, composer, notifier, e,
                  f"+{h}h 第{i+1}次找她没回")

    # 封顶后
    e73, _ = ts(BASE7, 24)
    dashboard(store, cfg, policy, composer, notifier, e73,
              "+24h 封顶冻结（应该是 WAIT）")

    # 她回来了
    e74, i74 = ts(BASE7, 25)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "我回来了宝宝"}, i74), now=e74)
    dashboard(store, cfg, policy, composer, notifier, e74, "她终于回来了！退避重置")

    print("\n" + "=" * 60)
    print("  场景一到七跑完。")
    print("=" * 60)


def check(cond, msg):
    """断言：新场景不只打印仪表盘，还要真的把语义钉死。"""
    if not cond:
        raise AssertionError(f"场景断言失败: {msg}")
    print(f"  ✓ {msg}")


def scenario_vigil():
    """场景八：冲突章节退避封顶后永不冻结，按守夜间隔仍 SEND（时间旅行多天）。
    "允许你生气，但不允许你离开"。"""
    print("\n" + "▶" * 30)
    print("  场景八：守夜——冲突封顶后不冻结，保底间隔仍去敲门")
    print("▶" * 30)
    store, cfg, policy, composer, notifier = setup()
    BASE = datetime(2026, 9, 6, 9, 0, tzinfo=TZ)

    now_e, now_i = ts(BASE, 0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "不想理你了"}, now_i), now=now_e)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-vigil-01", "type": "conflict",
            "summary": "吵崩了，她说不想理我，责任在我，没抱没讲清楚",
            "status": "active", "perceived_responsibility": "mine",
            "emotional_repair": False, "issue_resolved": False,
        }
    }, now_i), now=now_e)
    # 三次主动联系都没回 → 封顶（ladder [1,3,12]，max 3）
    for i, h in enumerate([1, 3, 9]):
        e, _ = ts(BASE, h)
        store.add_contact_attempt("ep-vigil-01", "conflict_repair",
            f"（哄她：第{i+1}次）", {}, sent_at=e, shadow=True)

    e1, _ = ts(BASE, 15)
    r = dashboard(store, cfg, policy, composer, notifier, e1,
                  "+15h 封顶后守夜计时中（还没到保底间隔）")
    check(r["decision"] == "WAIT", "封顶后间隔未到 → WAIT（守夜在数着时间）")
    check(r["gates"].get("backoff") is not None,
          "冲突章节 backoff 是有限时刻，不是永久冻结（None=冻结）")

    e2, _ = ts(BASE, 34)   # 距最后一次尝试 25h ≥ vigil 24h，18:00 非安静时段
    r = dashboard(store, cfg, policy, composer, notifier, e2,
                  "+34h 守夜到点（距上次尝试 25h ≥ 24h 保底）")
    check(r["decision"] == "SEND", "守夜到点 → SEND（永不冻结）")
    check("守夜" in r["message"], "守夜消息走 vigil 意图文案（不求回应，只说我在）")
    check("她还没回" not in r["message"], "守夜消息不带催促尾巴")

    e3, _ = ts(BASE, 52)   # 距守夜那条 18h < 24h
    r = dashboard(store, cfg, policy, composer, notifier, e3,
                  "+52h 守夜刚敲过，下一轮保底还没到")
    check(r["decision"] == "WAIT", "两次守夜之间照旧退避 WAIT")

    e4, _ = ts(BASE, 59)   # 距上条守夜 25h，20:00
    r = dashboard(store, cfg, policy, composer, notifier, e4,
                  "+59h 第二天守夜又到点（模拟多天）")
    check(r["decision"] == "SEND", "隔天守夜仍 SEND——她多久不回来，就守多久")


def scenario_hold_expiry():
    """场景九：嘴硬保质期——冲突章节里 HOLD 超过 8 小时自动失效转 SEND。
    骄傲有保质期，天亮之前先去抱人。"""
    print("\n" + "▶" * 30)
    print("  场景九：嘴硬保质期——HOLD 超时自动失效")
    print("▶" * 30)
    store, cfg, policy, composer, notifier = setup()
    # 倔一点的人格：克制拉满，才能稳定进 HOLD（想找但忍着）
    cfg = copy.deepcopy(cfg)
    cfg["policy"]["base_restraint"] = 0.9
    cfg["policy"]["longing_erosion"] = 0.0
    cfg["policy"]["hold_expiry_hours"] = 8
    BASE = datetime(2026, 9, 10, 10, 0, tzinfo=TZ)

    now_e, now_i = ts(BASE, 0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "你烦不烦，别管我"}, now_i), now=now_e)
    ingest_event(store, cfg, ev("episode_update", "assistant", {
        "episode": {
            "episode_id": "ep-sulk-01", "type": "conflict",
            "summary": "她迁怒冲我发火，责任在她，我嘴硬不想先低头",
            "status": "active", "perceived_responsibility": "yours",
            "emotional_repair": False, "issue_resolved": False,
        }
    }, now_i), now=now_e)

    e1, _ = ts(BASE, 1)
    r = dashboard(store, cfg, policy, composer, notifier, e1,
                  "+1h 想找但忍着（嘴硬开始计时）")
    check(r["decision"] == "HOLD", "责任在她+克制高 → HOLD（想找但选择忍着）")

    e2, _ = ts(BASE, 5)
    r = dashboard(store, cfg, policy, composer, notifier, e2,
                  "+5h 还在嘴硬（TTL 8h 未到）")
    check(r["decision"] == "HOLD", "TTL 未到 → 继续 HOLD")

    e3, _ = ts(BASE, 9.5)
    r = dashboard(store, cfg, policy, composer, notifier, e3,
                  "+9.5h 嘴硬超过 8 小时（保质期到了）")
    check(r["decision"] == "SEND", "HOLD 超过 hold_expiry_hours → 自动失效转 SEND")
    check("嘴硬到期" in r["reason"], "reason 写明是嘴硬到期，不是普通想念过线")


def scenario_silence_knock():
    """场景十：4 小时敲门——用真实默认配置验证：用户静默约 4 小时，
    纯想念（无任何 episode）过线主动联系；且不被已回应的旧尝试连坐。"""
    print("\n" + "▶" * 30)
    print("  场景十：4 小时敲门（真实默认配置）")
    print("▶" * 30)
    store = Store(":memory:")
    cfg = copy.deepcopy(rr_config.DEFAULTS)   # 用生产默认值验收 4 小时语义
    policy = DefaultPolicy()
    composer = TemplateComposer()
    notifier = LogNotifier(OUTBOX)
    BASE = datetime(2026, 9, 12, 9, 0, tzinfo=TZ)

    now_e, now_i = ts(BASE, 0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "我去忙啦"}, now_i), now=now_e)

    e1, _ = ts(BASE, 3.5)
    r = dashboard(store, cfg, policy, composer, notifier, e1,
                  "+3.5h 还差一点（想念未过线）")
    check(r["decision"] == "NO_ACTION", "静默 3.5h 想念未过 act_threshold → NO_ACTION")

    e2, _ = ts(BASE, 4.1)
    r = dashboard(store, cfg, policy, composer, notifier, e2,
                  "+4.1h 静默超过 4 小时（该去敲门了）")
    check(r["decision"] == "SEND", "静默约 4 小时 → 主动 SEND（4 小时敲门）")
    check(max(r["urge"], key=lambda k: r["urge"][k]) == "missing_you",
          "触发源是 missing_you（纯想念，无需任何 episode）")

    # 她回来又走：新的静默产生新的想念冲动，不被旧尝试连坐
    e3, i3 = ts(BASE, 5)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "回来啦"}, i3), now=e3)
    e4, _ = ts(BASE, 9.3)
    r = dashboard(store, cfg, policy, composer, notifier, e4,
                  "她回来过又静默 4.3h（旧尝试已被回应）")
    check(r["decision"] == "SEND", "新一轮静默 4 小时照样敲门，不被旧账退避连坐")


def scenario_normal_freeze():
    """场景十一：回归——普通章节（想念类）封顶后仍冻结，防舔狗闸没被改坏。"""
    print("\n" + "▶" * 30)
    print("  场景十一：回归——普通想念封顶仍冻结（防舔狗不动摇）")
    print("▶" * 30)
    store, cfg, policy, composer, notifier = setup()
    BASE = datetime(2026, 9, 14, 9, 0, tzinfo=TZ)

    now_e, now_i = ts(BASE, 0)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "出门啦"}, now_i), now=now_e)
    # 没有任何冲突 episode，纯想念找了三次都没回 → 封顶
    for i, h in enumerate([2, 4, 8]):
        e, _ = ts(BASE, h)
        store.add_contact_attempt(None, "missing_you",
            f"（想她：第{i+1}次）", {}, sent_at=e, shadow=True)

    e1, _ = ts(BASE, 30)
    r = dashboard(store, cfg, policy, composer, notifier, e1,
                  "+30h 普通章节封顶（应该冻结）")
    check(r["decision"] == "WAIT", "普通章节封顶 → WAIT")
    check("backoff" in r["gates"] and r["gates"]["backoff"] is None,
          "backoff 为 None（永久冻结，等她出现），没有变成守夜")

    e2, _ = ts(BASE, 100)
    r = dashboard(store, cfg, policy, composer, notifier, e2,
                  "+100h 多天后仍冻结（不许变成舔狗）")
    check(r["decision"] == "WAIT" and r["gates"].get("backoff") is None,
          "多天后普通章节依旧冻结，防舔狗闸一寸不让")

    e3, i3 = ts(BASE, 101)
    ingest_event(store, cfg, ev("user_message", "user",
        {"text": "我回来了"}, i3), now=e3)
    e4, _ = ts(BASE, 101.5)
    r = dashboard(store, cfg, policy, composer, notifier, e4,
                  "她出现后 0.5h（冻结应已重置）")
    check("backoff" not in r["gates"], "用户出现即重置，冻结解除")


if __name__ == "__main__":
    run_all()
    scenario_vigil()
    scenario_hold_expiry()
    scenario_silence_knock()
    scenario_normal_freeze()
    print("\n" + "=" * 60)
    print("  全部场景（新旧）跑完，断言全部通过。")
    print("=" * 60)
