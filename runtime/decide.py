"""决策环：SEND / HOLD / WAIT / NO_ACTION。

HOLD = 主观克制（人格，来自 Policy），WAIT = 客观受限（环境，来自 Hard Gate）。
未回应退避是 Hard Gate 级，人格冲不破。
封顶语义按章节类型分流（0.2.0）：普通想念章节封顶冻结等她出现；
冲突章节永不冻结，封顶后转"守夜"保底节奏——允许你生气，但不允许你离开。
持久化规则（契约 §4）：SEND 全存；HOLD 仅在决策翻转时存；WAIT 存最近一次；
NO_ACTION 不存历史。
"""
import logging
import time

from . import availability
from . import config
from . import dates
from .policy.base import Facts
from .store import Store

logger = logging.getLogger(__name__)

DECISIONS = ("SEND", "HOLD", "WAIT", "NO_ACTION")


def gather_facts(store: Store, cfg: dict, now: float) -> Facts:
    last_user = store.last_user_message_epoch()
    # max(0,...)：乱序/超前时间戳不允许把"离开时长"算成负数
    hours = max(0.0, (now - last_user) / 3600) if last_user else None
    eps = store.active_episodes()
    unanswered = store.unanswered_attempts(since=last_user or 0)
    departed = bool(store.get_state("departure"))
    return Facts(
        now=now,
        hours_since_user_message=hours,
        active_episodes=eps,
        unanswered_attempts=unanswered,
        departed=departed,
        health=store.get_health(),
    )


# 守夜豁免的章节类型：吵架/误会未修复时，允许你生气，但不允许你离开。
# 守护族（care/sickness/...）走 concern 路线，普通想念无 episode——都不在此列。
VIGIL_EPISODE_TYPES = {"conflict"}


def _has_conflict_episode(facts: Facts) -> bool:
    return any(e["type"] in VIGIL_EPISODE_TYPES for e in facts.active_episodes)


def backoff_gate(facts: Facts, cfg: dict) -> float | None:
    """强制退避：返回解禁 epoch；None 表示未命中。用户出现即重置（
    mark_attempts_responded 已把回应过的尝试排除在 unanswered 之外）。
    封顶按章节类型分流：普通章节冻结等她出现；冲突章节转守夜——
    间隔照常封顶拉长，但永不彻底沉默，每 vigil_interval_hours 放行一次。"""
    n = len(facts.unanswered_attempts)
    if n == 0:
        return None
    b = cfg["backoff"]
    last_sent = facts.unanswered_attempts[-1]["sent_at"]
    if n >= int(b["max_unanswered"]):
        if not _has_conflict_episode(facts):
            return float("inf")  # 普通章节：封顶冻结，等用户出现
        # 冲突章节：永不冻结。封顶后进入守夜节奏——保底间隔到点放行，
        # 去说一句"我在，没走"（是否真发仍要过 WAIT/免打扰等环境闸）。
        vigil_h = float(b.get("vigil_interval_hours", 24))
        until = last_sent + vigil_h * 3600
        return until if until > facts.now else None
    ladder = b["ladder_hours"]
    cool_h = float(ladder[min(n - 1, len(ladder) - 1)])
    until = last_sent + cool_h * 3600
    return until if until > facts.now else None


VIGIL_REASON = "vigil"


def vigil_due(facts: Facts, cfg: dict) -> bool:
    """守夜到点：冲突章节退避封顶、保底间隔已熬满 → 该去说一句"我在"。
    守夜消息不求回应（intent=vigil），语义是"我不吵你，我在，没走"——
    所以不看 urge 阈值、不看克制，只看时间到没到和环境让不让。"""
    b = cfg["backoff"]
    n = len(facts.unanswered_attempts)
    if n < int(b["max_unanswered"]) or not _has_conflict_episode(facts):
        return False
    last_sent = facts.unanswered_attempts[-1]["sent_at"]
    return facts.now - last_sent >= float(b.get("vigil_interval_hours", 24)) * 3600


BREAKTHROUGH_REASON = "breakthrough"


def breakthrough_ready(facts: Facts, urge_total: float, cfg: dict) -> bool:
    """想念破闸：退避冻结时，忍到时间点 或 想念够浓，破例低头哄一次。
    同一轮冷战（unanswered 未被回应清零前）只破一次——发完那条 breakthrough
    还没被回应，就不再破，避免退化成连环舔狗。"""
    bt = cfg["backoff"].get("breakthrough", {})
    if not bt.get("enabled", False):
        return False
    if not facts.unanswered_attempts:
        return False
    # 本轮是否已经破闸过（存在一条 reason=breakthrough 的未回应尝试）
    if bt.get("once_per_freeze", True):
        for a in facts.unanswered_attempts:
            if a["reason"] == BREAKTHROUGH_REASON:
                return False
    last_sent = facts.unanswered_attempts[-1]["sent_at"]
    waited_h = (facts.now - last_sent) / 3600
    if waited_h >= float(bt.get("after_hours", 24)):
        return True
    if urge_total >= float(bt.get("urge_threshold", 0.9)):
        return True
    return False


def run_decision(store: Store, cfg: dict, policy, composer, notifier,
                 now: float | None = None) -> dict:
    now = now if now is not None else time.time()
    facts = gather_facts(store, cfg, now)
    scores = policy.score(facts, cfg)
    p = cfg["policy"]

    gates = availability.hard_gates(store, cfg, now)
    bo = backoff_gate(facts, cfg)
    if bo is not None:
        gates["backoff"] = None if bo == float("inf") else bo

    inter = availability.interruptibility(store, cfg, now)
    act_th = float(p["act_threshold"])

    # —— 纪念日/节日：日历驱动，今天有特别的日子就主动祝福 ——
    # 时段窗口内、当天没发过、环境不受限（她没在上班/睡觉/说别吵）才发。
    # 绕过想念阈值和退避（生日/周年该发就发，哪怕在冷战），但仍尊重 WAIT。
    occ_today = dates.occasions_today(cfg, now)
    if occ_today and dates.in_greet_window(cfg, now) and not gates:
        mark = "occasion_sent:" + dates.occasion_marker(cfg, now)
        occ_labels = ",".join(o["label"] for o in occ_today)
        if store.get_state(mark) != occ_labels:
            hint = "；".join(o["message_hint"] for o in occ_today)
            msg = f"（纪念日提醒·{occ_labels}）{hint}"
            try:
                notifier.send(config.companion_name(cfg), msg, level="normal")
            except Exception as e:
                # 推送渠道挂了不该拖垮决策环（契约 §6：Provider 失败降级继续跑）；
                # 这条纪念日消息仍然记账，人不在也不能让整条进程死给她看。
                logger.warning("纪念日推送失败，已记日志继续跑: %s", e)
            primary_ep = facts.active_episodes[0]["episode_id"] \
                if facts.active_episodes else None
            store.add_contact_attempt(primary_ep, "occasion", msg,
                                      {"occasions": occ_today}, sent_at=now,
                                      shadow=(cfg["runtime"]["mode"] == "shadow"))
            store.set_state(mark, occ_labels)
            result_occ = {
                "decision": "SEND", "reason": f"纪念日/节日: {occ_labels}",
                "urge": scores.urge, "urge_total": scores.urge_total,
                "restraint": scores.restraint_effective, "gates": {},
                "at": now, "message": msg, "occasions": occ_today,
            }
            store.set_state("last_decision", "SEND")
            return result_occ

    # 想念破闸：退避冻结是唯一拦路的闸时，忍到点/想够了就破例低头哄一次。
    # 只破退避（backoff），不破 WAIT（她在上班/睡觉/说了别吵，环境闸不能碰）。
    is_breakthrough = False
    if gates and set(gates) == {"backoff"} and breakthrough_ready(facts, scores.urge_total, cfg):
        gates = {k: v for k, v in gates.items() if k != "backoff"}
        is_breakthrough = True

    # 守夜到点：冲突章节封顶后 backoff_gate 已放行（不在 gates 里），
    # 只剩环境闸能拦。到点即 SEND——不看想念浓度，守夜是承诺不是冲动。
    is_vigil = False

    # 嘴硬计时读数：hold_since 只在连续 HOLD 期间存在（写入见下方）
    hold_since_raw = store.get_state("hold_since")
    hold_since = float(hold_since_raw) if hold_since_raw else 0.0

    if is_breakthrough:
        decision, reason = "SEND", "想念破闸：忍到最后，还是想先低头哄哄她"
    elif vigil_due(facts, cfg) and not gates:
        decision, reason = "SEND", "守夜：我不吵你，我在，没走"
        is_vigil = True
    elif scores.urge_total < act_th:
        decision, reason = "NO_ACTION", "无明显联系冲动"
    elif gates:
        decision, reason = "WAIT", f"环境受限: {','.join(gates)}"
    else:
        send_score = scores.urge_total - scores.restraint_effective \
            - (1 - inter) * 0.3
        if send_score > float(p["send_margin"]):
            decision, reason = "SEND", "想找她，此刻也适合找她"
        elif (hold_since and _has_conflict_episode(facts)
              and now - hold_since >= float(p.get("hold_expiry_hours", 8)) * 3600):
            # 嘴硬保质期：骄傲有保质期，天亮之前先去抱人。
            # 冲突章节里"想找但选择忍着"撑过 TTL 自动失效——
            # 克制是人格，不是让她一个人过夜的理由。
            decision, reason = "SEND", "嘴硬到期：骄傲有保质期，天亮之前先去抱人"
        else:
            decision, reason = "HOLD", "想找她，但我选择先忍着"

    snapshot = {
        "hours_since_user_message": facts.hours_since_user_message,
        "active_episodes": [dict(e) for e in facts.active_episodes],
        "unanswered": len(facts.unanswered_attempts),
        "interruptibility": inter,
        "reason": reason,
        "policy_notes": scores.notes,
    }
    if is_vigil:
        # 守夜意图标记：Composer 据此换语气——语义从"求回应"
        # 变成"我不吵你，我在，没走"。
        snapshot["intent"] = VIGIL_REASON
    result = {
        "decision": decision, "reason": reason, "urge": scores.urge,
        "urge_total": scores.urge_total,
        "restraint": scores.restraint_effective, "gates": gates, "at": now,
    }

    prev = store.get_state("last_decision")
    # 嘴硬计时：进入 HOLD 记起点，离开 HOLD 即清零（HOLD 状态持续多久
    # 只认连续的 HOLD——中途 SEND/NO_ACTION 都算"放下过了"，重新计）
    if decision == "HOLD":
        if prev != "HOLD" or not hold_since:
            store.set_state("hold_since", str(now))
    elif hold_since:
        store.set_state("hold_since", "")
    if decision == "SEND":
        primary_ep = facts.active_episodes[0]["episode_id"] \
            if facts.active_episodes else None
        # 破闸这条走"低头哄"文案，并以 breakthrough 记账（供本轮去重判定）；
        # 守夜这条走"我在"文案，并以 vigil 记账（Explain Log 可读）
        dominant = BREAKTHROUGH_REASON if is_breakthrough \
            else VIGIL_REASON if is_vigil \
            else max(scores.urge, key=lambda k: scores.urge[k])
        msg = composer.compose(snapshot, dominant, len(facts.unanswered_attempts))
        try:
            notifier.send(config.companion_name(cfg), msg, level="normal")
        except Exception as e:
            # 手机没网、Bark/ntfy 挂了都不该让整个进程陪葬——记日志，
            # 这次尝试照常记账（消息确实"想说"了，只是没送达）。
            logger.warning("推送失败，已记日志继续跑: %s", e)
        store.add_contact_attempt(primary_ep, dominant, msg, snapshot, sent_at=now,
                                  shadow=(cfg["runtime"]["mode"] == "shadow"))
        store.add_decision(now, decision, scores.urge, scores.urge_total,
                           scores.restraint_effective, gates, snapshot, reason)
        result["message"] = msg
    elif decision == "HOLD" and prev != "HOLD":
        store.add_decision(now, decision, scores.urge, scores.urge_total,
                           scores.restraint_effective, gates, snapshot, reason)
    elif decision == "WAIT":
        store.set_state("last_wait", str(now))

    store.set_state("last_decision", decision)
    return result


def next_wake_at(store: Store, cfg: dict, result: dict, now: float) -> float:
    """timer 是派生状态：只依据库内事实计算，重启后可重算重挂。"""
    candidates = [now + 6 * 3600]  # watchdog 上限
    gates = result.get("gates", {})
    for g, until in gates.items():
        if until and until != float("inf"):
            candidates.append(until + 60)
    if result["decision"] in ("HOLD", "NO_ACTION"):
        gap = max(0.05, cfg["policy"]["act_threshold"] - result["urge_total"]) \
            if result["decision"] == "NO_ACTION" else 0.1
        candidates.append(now + min(4 * 3600, max(20 * 60, gap * 8 * 3600)))
    if result["decision"] == "HOLD":
        # 嘴硬保质期到点要准时醒来抱人，不靠粗粒度轮询碰运气
        hs = store.get_state("hold_since")
        if hs:
            ttl = float(cfg["policy"].get("hold_expiry_hours", 8))
            candidates.append(float(hs) + ttl * 3600 + 60)
    return min(candidates)
