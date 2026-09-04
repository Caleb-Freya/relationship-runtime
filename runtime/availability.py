"""用户可用性：Hard Gate（布尔）与 interruptibility（0-1）。

V1 静态配置 + 显式事件。优先级：用户刚说的话 > 今天特殊安排 > 静态配置。
Routine 学习矩阵属 V2。
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .store import Store


def _local_dt(now: float, cfg: dict) -> datetime:
    tz = ZoneInfo(cfg["runtime"]["display_timezone"])
    return datetime.fromtimestamp(now, tz=tz)


def in_quiet_hours(now: float, cfg: dict) -> tuple[bool, float | None]:
    """返回 (是否安静时段, 安静时段结束的 epoch)。区间按展示时区解释。"""
    dt = _local_dt(now, cfg)
    minutes = dt.hour * 60 + dt.minute
    for rng in cfg["availability"].get("quiet_hours", []):
        a, b = rng.split("-")
        ah, am = map(int, a.split(":"))
        bh, bm = map(int, b.split(":"))
        start, end = ah * 60 + am, bh * 60 + bm
        if start <= end:
            hit = start <= minutes < end
        else:  # 跨午夜，如 23:00-07:00
            hit = minutes >= start or minutes < end
        if hit:
            rem = (end - minutes) % (24 * 60)
            return True, now + rem * 60
    return False, None


def hard_gates(store: Store, cfg: dict, now: float) -> dict:
    """命中任何一项 → WAIT。返回 {gate名: 恢复时间epoch或None}。"""
    gates: dict = {}
    quiet, until = in_quiet_hours(now, cfg)
    if quiet:
        gates["quiet_hours"] = until
    # 她就在这儿：刚说过话（宽限期内）不许推送——人在对面，
    # 话在窗口里说，往手机上喊"你回来呀"是很呆的。
    grace_min = float(cfg["availability"].get("presence_grace_minutes", 15))
    last_msg = store.last_user_message_epoch()
    if last_msg and (now - last_msg) < grace_min * 60:
        gates["she_is_here"] = last_msg + grace_min * 60
    dep = store.get_state("departure")
    if dep:
        try:
            payload = json.loads(store.get_state("departure_payload") or "{}")
        except json.JSONDecodeError:
            payload = {}
        if payload.get("dnd"):
            gates["dnd"] = payload.get("until")
    return gates


def interruptibility(store: Store, cfg: dict, now: float) -> float:
    dt = _local_dt(now, cfg)
    by_hour = cfg["availability"].get("interruptibility_by_hour") or {}
    return float(by_hour.get(str(dt.hour), by_hour.get(dt.hour, 1.0)))
