"""纪念日 / 生日 / 大节日：日历驱动的主动祝福。

设计原则（原作者版）：
- 只收「重要的」节日：过年、元宵、端午、七夕、中秋、情人节、圣诞、元旦。
  绝不发爱耳日/大暑这种——宁可漏，不可烦。
- 两个人的纪念日从「在一起的日子」自动算里程碑：第50/100/200/365天、
  520/1314 这种情话数字、以及每一年的周年。
- 生日每年提醒。
- 中国节日是农历，本模块用 2026–2030 阳历对照表（写死、准、无依赖）。
  ⚠️ 2030 年后需续表：见 LUNAR_FESTIVALS 末尾。

对外只暴露 occasions_today(cfg, now)：返回今天该主动说话的日子列表，
每项含 kind / label / message_hint。decide.py 据此注入一次 SEND。
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def _zone(cfg: dict) -> ZoneInfo:
    """按配置的展示时区判定「今天」「几点」，默认北京时间。"""
    tz = (cfg or {}).get("runtime", {}).get("display_timezone") or "Asia/Shanghai"
    return ZoneInfo(tz)


def _today_local(now: float, cfg: dict) -> date:
    return datetime.fromtimestamp(now, _zone(cfg)).date()


# —— 固定阳历大节日（月, 日）→ 文案提示 ——
SOLAR_FESTIVALS = {
    (1, 1):   ("元旦", "新的一年第一天，想第一个把新年好说给你"),
    (2, 14):  ("情人节", "情人节快乐，你是我的情人也是我的家"),
    (12, 24): ("平安夜", "平安夜，只想把你抱在怀里报平安"),
    (12, 25): ("圣诞节", "圣诞快乐，我的礼物就是你还在"),
}

# —— 农历大节日：阳历对照表（写死到 2030；到期务必续表）——
# 每项 "YYYY-MM-DD": (名称, 文案提示)
LUNAR_FESTIVALS = {
    # 春节
    "2026-02-17": ("春节", "过年好！新的一年，我还想赖在你身边"),
    "2027-02-06": ("春节", "过年好！新的一年，我还想赖在你身边"),
    "2028-01-26": ("春节", "过年好！新的一年，我还想赖在你身边"),
    "2029-02-13": ("春节", "过年好！新的一年，我还想赖在你身边"),
    "2030-02-03": ("春节", "过年好！新的一年，我还想赖在你身边"),
    # 元宵
    "2026-03-03": ("元宵节", "元宵节，汤圆要一起吃才圆"),
    "2027-02-20": ("元宵节", "元宵节，汤圆要一起吃才圆"),
    "2028-02-09": ("元宵节", "元宵节，汤圆要一起吃才圆"),
    "2029-02-27": ("元宵节", "元宵节，汤圆要一起吃才圆"),
    "2030-02-17": ("元宵节", "元宵节，汤圆要一起吃才圆"),
    # 端午
    "2026-06-19": ("端午节", "端午安康，给你包个最甜的粽子"),
    "2027-06-09": ("端午节", "端午安康，给你包个最甜的粽子"),
    "2028-05-28": ("端午节", "端午安康，给你包个最甜的粽子"),
    "2029-06-16": ("端午节", "端午安康，给你包个最甜的粽子"),
    "2030-06-05": ("端午节", "端午安康，给你包个最甜的粽子"),
    # 七夕
    "2026-08-19": ("七夕", "七夕快乐，牛郎织女一年一次，我们天天见"),
    "2027-08-08": ("七夕", "七夕快乐，牛郎织女一年一次，我们天天见"),
    "2028-08-26": ("七夕", "七夕快乐，牛郎织女一年一次，我们天天见"),
    "2029-08-16": ("七夕", "七夕快乐，牛郎织女一年一次，我们天天见"),
    "2030-08-05": ("七夕", "七夕快乐，牛郎织女一年一次，我们天天见"),
    # 中秋
    "2026-09-25": ("中秋节", "中秋快乐，月亮我看着，你替我圆着"),
    "2027-09-15": ("中秋节", "中秋快乐，月亮我看着，你替我圆着"),
    "2028-10-03": ("中秋节", "中秋快乐，月亮我看着，你替我圆着"),
    "2029-09-22": ("中秋节", "中秋快乐，月亮我看着，你替我圆着"),
    "2030-09-12": ("中秋节", "中秋快乐，月亮我看着，你替我圆着"),
    # ⚠️ 2031+ 请在此续表（农历节日阳历每年不同，不能公式化算）
}

# —— 情侣天数里程碑：只收有意义的，不逐日轰炸 ——
DAY_MILESTONES = {
    30:   "在一起满一个月",
    50:   "在一起第50天",
    100:  "在一起第100天",
    200:  "在一起第200天",
    # 365天=整整一年，交给"周年"逻辑处理，这里不放，避免同一天重复提醒
    520:  "第520天（我爱你）",
    1000: "在一起第1000天",
    1314: "第1314天（一生一世）",
}


def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def _anniversary_hits(today: date, anchor: date, label_fmt: str,
                      hint_yearly: str, kind: str = "anniversary") -> list[dict]:
    """周年判定：按月日匹配，避开 2/29 的闰年陷阱（落到 2/28）。
    label_fmt 可含 {years} 占位（如"在一起·{years}周年"），也可不含（如"生日"）。
    用 str.replace 而非 .format：label_fmt/hint_yearly 可能来自用户填的自定义
    纪念日文案，里面若恰好带别的花括号，.format 会当占位符解析直接抛
    KeyError/IndexError 把决策环崩掉——只替换我们自己定义的 {years} 占位。"""
    out = []
    m, d = anchor.month, anchor.day
    if (m, d) == (2, 29) and not _is_leap(today.year):
        m, d = 2, 28
    if (today.month, today.day) == (m, d) and today > anchor:
        years = today.year - anchor.year
        label = label_fmt.replace("{years}", str(years))
        hint = hint_yearly.replace("{years}", str(years))
        out.append({"kind": kind, "label": label, "message_hint": hint})
    return out


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def occasions_today(cfg: dict, now: float | None = None) -> list[dict]:
    """返回今天（按 display_timezone 判定的日期）该主动说话的日子。
    空列表=今天没有特别的日子。"""
    now = now if now is not None else time.time()
    today = _today_local(now, cfg)
    dcfg = cfg.get("dates", {}) or {}
    out: list[dict] = []

    # 1) 固定阳历大节日
    if dcfg.get("festivals", True):
        fest = SOLAR_FESTIVALS.get((today.month, today.day))
        if fest:
            out.append({"kind": "festival", "label": fest[0],
                        "message_hint": fest[1]})
        lun = LUNAR_FESTIVALS.get(today.isoformat())
        if lun:
            out.append({"kind": "festival", "label": lun[0],
                        "message_hint": lun[1]})

    # 2) 生日
    bday = _parse_date(dcfg.get("birthday", ""))
    if bday:
        out += _anniversary_hits(
            today, bday, "生日",
            "今天是你的生日呀，生日快乐——许愿的时候记得也留我一个位置",
            kind="birthday")

    # 3) 在一起：天数里程碑 + 每年周年
    tog = _parse_date(dcfg.get("together_date", ""))
    if tog:
        days = (today - tog).days
        if days in DAY_MILESTONES:
            out.append({"kind": "milestone",
                        "label": DAY_MILESTONES[days],
                        "message_hint": f"今天是我们{DAY_MILESTONES[days]}，"
                                        f"{days}天了，一天都没白过"})
        out += _anniversary_hits(
            today, tog, "在一起{years}周年",
            "今天是我们在一起{years}周年，从第一天到现在，我都记着")

    # 4) 姨妈周期预警（来之前温柔提醒）
    out += cycle_occasions(cfg, now)

    # 5) 自定义纪念日（用户加的其它日子）
    for item in dcfg.get("custom", []) or []:
        d = _parse_date(item.get("date", ""))
        if not d:
            continue
        yearly = item.get("yearly", True)
        label = item.get("label", "纪念日")
        hint = item.get("message_hint", f"今天是{label}，我记得")
        if yearly:
            out += _anniversary_hits(today, d, label, hint)
        elif d == today:
            out.append({"kind": "custom", "label": label,
                        "message_hint": hint})

    return out


def cycle_occasions(cfg: dict, now: float | None = None) -> list[dict]:
    """姨妈周期预测：知道周期天数 + 上次开始日，算下次哪天来，来之前温柔提醒。
    只在「前一天」发一次预警，不逐日念叨。真正来了那几天的关怀走 episode（用户报"姨妈来了"）。"""
    now = now if now is not None else time.time()
    dcfg = cfg.get("dates", {}) or {}
    length = int(dcfg.get("cycle_length_days", 0) or 0)
    last = _parse_date(dcfg.get("last_period_start", ""))
    if length < 15 or length > 60 or not last:   # 无有效周期数据则不预测
        return []
    today = _today_local(now, cfg)
    # 找到 >= 今天的下一个预计开始日
    k = 0
    nxt = last
    while nxt < today:
        k += 1
        nxt = last + timedelta(days=length * k)
    days_until = (nxt - today).days
    if days_until == 1:
        return [{"kind": "cycle_care", "label": "姨妈预警",
                 "message_hint": "按周期明天姨妈可能就来。提醒TA把要用的备上（暖宝宝、"
                                 "红糖姜茶……），问问TA需要什么。用你们平时的方式说，"
                                 "别每次都是同一句关怀"}]
    if days_until == 0:
        return [{"kind": "cycle_care", "label": "姨妈预计今天",
                 "message_hint": "按周期今天该来了。问TA痛不痛、有没有哪里不舒服；"
                                 "告诉TA不舒服随时来找你，你可以陪TA、给TA讲故事。"
                                 "要给情绪价值，不是发健康须知"}]
    return []


def in_greet_window(cfg: dict, now: float | None = None) -> bool:
    """避免半夜祝福：只在配置的时段（默认展示时区 8:00–12:00）发。"""
    now = now if now is not None else time.time()
    hour = datetime.fromtimestamp(now, _zone(cfg)).hour
    dcfg = cfg.get("dates", {}) or {}
    lo = int(dcfg.get("greet_hour_start", 8))
    hi = int(dcfg.get("greet_hour_end", 12))
    return lo <= hour < hi


def occasion_marker(cfg: dict, now: float) -> str:
    """当天去重键：同一天同一场合只发一次。"""
    return _today_local(now, cfg).isoformat()
