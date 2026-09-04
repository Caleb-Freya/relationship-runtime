"""默认 Policy（fallback，不是"官方人格"）。

只依赖 Runtime 自身事实：时间流逝、episode 状态、联系历史。
有 Mind Provider 的实例可用自己的插件替换或注入。
"""
import math

from .base import Facts, Policy, Scores
from ..store import iso_to_epoch

# 守护类 episode：她的事，不是我们之间的事。产生 concern 而非 conflict_repair
CARE_TYPES = {"care", "sickness", "stress", "cycle"}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _health_concern(health: dict, now: float, cfg: dict,
                    notes: list[str]) -> float:
    """手环显示她身体不好（压力高/没睡好）→ 想去看看她。
    保守：数据太旧不触发；只用 routine 级信号（压力/睡眠/静息心率）。"""
    if not health:
        return 0.0
    fresh_h = float(cfg.get("fresh_hours", 6.0))
    updated = health.get("updated_at")
    if updated:
        try:
            if (now - iso_to_epoch(updated)) / 3600 > fresh_h:
                return 0.0  # 数据过期，不拿旧账担心
        except (ValueError, TypeError):
            pass

    score = 0.0
    stress = health.get("stress")
    if isinstance(stress, (int, float)):
        hi = float(cfg.get("stress_high", 70))
        if stress >= hi:
            score = max(score, min(0.35, 0.15 + (stress - hi) / 100.0))
            notes.append(f"手环压力 {stress:.0f}，偏高，想去看看她")

    sleep = health.get("sleep_hours")
    if isinstance(sleep, (int, float)) and sleep > 0:
        low = float(cfg.get("sleep_low_hours", 5.0))
        if sleep < low:
            score = max(score, min(0.3, 0.12 + (low - sleep) * 0.06))
            notes.append(f"手环睡眠仅 {sleep:.1f} 小时，没睡好，心疼")

    return score


class DefaultPolicy(Policy):
    def score(self, facts: Facts, cfg: dict) -> Scores:
        p = cfg["policy"]
        notes: list[str] = []
        urge: dict[str, float] = {}

        # --- missing_you: 随离开时间平滑上升 ---
        h = facts.hours_since_user_message
        if h is None:
            missing = 0.0
        else:
            missing = 1.0 - math.exp(-h / float(p["longing_ramp_hours"]))
        urge["missing_you"] = round(missing, 3)

        # --- conflict_repair / unfinished_topic: 来自 active episodes ---
        conflict = 0.0
        unfinished = 0.0
        resp_adjust = 0.0
        conflict_ramp = float(p.get("conflict_ramp_hours", 6.0))
        care = 0.0
        for ep in facts.active_episodes:
            age_h = (facts.now - ep["started_at"]) / 3600
            # 强度调制：她哭了(≥0.8)和小拌嘴(≤0.3)不是同一件事。
            # 0.5 为中性（倍率恰为 1.0，与无强度时代码行为一致）。
            keys = ep.keys() if hasattr(ep, "keys") else ()
            inten = ep["intensity"] if "intensity" in keys and ep["intensity"] is not None else 0.5
            scale = 0.6 + 0.8 * inten
            if ep["type"] in CARE_TYPES:
                # 她的事（生病/难处/姨妈期）：她越久没出现，我越该去看看。
                # 对克制没有加成——关心不需要面子。
                # 起点高（0.5）、涨得快（1小时），she_is_here 15分钟门过了很快就该去问。
                absent = h if h is not None else 0.0
                care_ramp = float(p.get("care_ramp_hours", 1.0))
                care = max(care, (0.5 + 0.3 * (1 - math.exp(-absent / care_ramp))) * scale)
                notes.append(f"{ep['episode_id']}: {ep['summary']}（守护中）")
                continue
            base = (0.35 + 0.35 * (1 - math.exp(-age_h / conflict_ramp))) * scale
            if inten >= 0.8:
                # 她哭了/事情很大：修复冲动立即过线，不允许爬坡等待
                base = max(base, 0.75)
                notes.append(f"{ep['episode_id']}: 强度{inten:.1f}，她情绪很大，立刻过去")
            if ep["emotional_repair"]:
                base *= 0.4          # 抱过了，急迫感降但不清零
                if not ep["issue_resolved"]:
                    unfinished = max(unfinished, 0.25)  # 事情没讲清楚
            conflict = max(conflict, base)
            r = ep["perceived_responsibility"]
            if r == "yours":
                resp_adjust += 0.25  # 这次不是我的错 → 更绷得住
                notes.append(f"{ep['episode_id']}: 责任在她，我先不低头")
            elif r == "mine":
                resp_adjust -= 0.20  # 是我的错 → 该我先开口
                notes.append(f"{ep['episode_id']}: 是我的错，该我先开口")
        urge["conflict_repair"] = round(conflict, 3)
        urge["unfinished_topic"] = round(unfinished, 3)

        # --- concern: 守护类 episode + 明确离开后久未出现 + 手环身体信号 ---
        concern = care
        if facts.departed and h is not None and h > 12:
            concern = max(concern, _clamp((h - 12) / 24) * 0.5)
        hc_cfg = p.get("health_concern") or {}
        if hc_cfg.get("enabled", True):
            concern = max(concern, _health_concern(facts.health, facts.now,
                                                    hc_cfg, notes))
        urge["concern"] = round(concern, 3)

        urge_total = _clamp(
            0.6 * missing + 0.8 * conflict + 0.3 * unfinished + 0.5 * concern
        )

        # --- restraint ---
        restraint = float(p["base_restraint"]) + resp_adjust
        n_unanswered = len(facts.unanswered_attempts)
        if n_unanswered:
            restraint += 0.15 * n_unanswered  # 找过没回，不想显得上赶着
            notes.append(f"已主动找过 {n_unanswered} 次未回应，克制上调")
        restraint_raw = _clamp(restraint)
        # 想念侵蚀克制：越想越绷不住
        restraint_eff = _clamp(
            restraint_raw - missing * float(p["longing_erosion"]))

        return Scores(
            urge=urge,
            urge_total=round(urge_total, 3),
            restraint_raw=round(restraint_raw, 3),
            restraint_effective=round(restraint_eff, 3),
            notes=notes,
        )
