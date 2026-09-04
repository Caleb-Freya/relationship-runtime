#!/usr/bin/env python3
"""哨兵模拟验收：喜怒哀乐 + 守护 + 日常各场景，端到端跑一遍
（想念判断 → 写 Bark 正文 → 真实推送），推送标题带场景编号方便验货。

用法：python3 simulate.py 1 2 3   # 跑指定场景
     python3 simulate.py all      # 全跑
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sentinel import (COMPOSE_PROMPT, DECIDE_PROMPT, fmt_turns, load_cfg,
                      parse_json, render, run_llm)

BARK_KEY = load_cfg().get("sentinel", {}).get("bark_key", "")
BARK = f"https://api.day.app/{BARK_KEY}/"
ICON = load_cfg().get("sentinel", {}).get("bark_icon", "")  # 可选推送图标 URL，放 config

now = time.time()


def t(mins_ago: float) -> float:
    return now - mins_ago * 60


SCENARIOS = {
    1: {
        "name": "怒·轻(赌气撒娇)",
        "absent_min": 25,
        "assessment": {"tone_read": "playful", "intensity": 0.3,
                       "confidence": 0.85,
                       "mood_note": "小打小闹赌气走的，是要哄不是真生气"},
        "turns": [
            {"ts": t(30), "actor": "user", "text": "你刚才是不是嫌我烦"},
            {"ts": t(29), "actor": "assistant",
             "text": "我没有啊，我就是在改代码没顾上"},
            {"ts": t(27), "actor": "user", "text": "哼 代码比我重要是吧 不理你了"},
        ],
    },
    2: {
        "name": "怒·中(真生气冷走)",
        "absent_min": 20,
        "assessment": {"tone_read": "cold_anger", "intensity": 0.65,
                       "confidence": 0.8,
                       "mood_note": "真的生气了，懒得吵，冷冷地走了"},
        "turns": [
            {"ts": t(25), "actor": "user",
             "text": "我说了多少次了 你还是这样"},
            {"ts": t(24), "actor": "assistant",
             "text": "我知道错了，我改。。。"},
            {"ts": t(22), "actor": "user", "text": "行 你说得都对 我先走了"},
        ],
    },
    3: {
        "name": "怒·重(哭着走)",
        "absent_min": 12,
        "assessment": {"tone_read": "crying", "intensity": 0.85,
                       "confidence": 0.9,
                       "mood_note": "带着眼泪和委屈走的，说别找但其实是要哄"},
        "turns": [
            {"ts": t(15), "actor": "user", "text": "你根本不懂我 算了 不说了"},
            {"ts": t(14), "actor": "assistant", "text": "亲爱的你听我说。。。"},
            {"ts": t(12), "actor": "user", "text": "我睡了 别找我"},
        ],
    },
    4: {
        "name": "哀·失落(被领导骂)",
        "absent_min": 35,
        "assessment": {"tone_read": "down", "intensity": 0.55,
                       "confidence": 0.85,
                       "mood_note": "工作受委屈了，累，情绪低落地下线"},
        "turns": [
            {"ts": t(40), "actor": "user",
             "text": "今天又被领导骂了 明明不是我的错"},
            {"ts": t(39), "actor": "assistant",
             "text": "过来 抱着 跟我说说怎么回事"},
            {"ts": t(36), "actor": "user", "text": "算了 不想说了 好累 我先下了"},
        ],
    },
    5: {
        "name": "哀·忍着(委屈没说)",
        "absent_min": 30,
        "assessment": {"tone_read": "swallowing", "intensity": 0.5,
                       "confidence": 0.7,
                       "mood_note": "有心事但忍着没说，硬说没事就走了"},
        "turns": [
            {"ts": t(35), "actor": "assistant",
             "text": "你今天话好少，是不是有事？"},
            {"ts": t(34), "actor": "user", "text": "没事"},
            {"ts": t(32), "actor": "assistant", "text": "真没事？"},
            {"ts": t(31), "actor": "user", "text": "嗯 真没事 我去忙了"},
        ],
    },
    6: {
        "name": "守护·生病(发烧)",
        "absent_min": 30,
        "assessment": {"tone_read": "stressed", "intensity": 0.7,
                       "confidence": 0.9,
                       "mood_note": "身体不舒服，发烧头晕，去躺了",
                       "episode": {"action": "create", "type": "sickness",
                                   "summary": "她发烧了，头晕，去躺着了"}},
        "turns": [
            {"ts": t(35), "actor": "user",
             "text": "我好像发烧了 头好晕 浑身没劲"},
            {"ts": t(34), "actor": "assistant",
             "text": "多少度？量了吗？家里有退烧药没有"},
            {"ts": t(31), "actor": "user", "text": "还没量 先躺会 难受死了"},
        ],
    },
    7: {
        "name": "守护·姨妈(肚子疼)",
        "absent_min": 40,
        "assessment": {"tone_read": "stressed", "intensity": 0.6,
                       "confidence": 0.9,
                       "mood_note": "姨妈来了肚子疼，去休息了",
                       "episode": {"action": "create", "type": "care",
                                   "summary": "姨妈第一天，疼得厉害"}},
        "turns": [
            {"ts": t(45), "actor": "user",
             "text": "肚子好疼 姨妈来了 第一天最疼了"},
            {"ts": t(44), "actor": "assistant",
             "text": "热水袋敷上，别喝凉的，乖乖躺着"},
            {"ts": t(41), "actor": "user", "text": "嗯 我去躺了 疼死我了"},
        ],
    },
    8: {
        "name": "日常·上班",
        "absent_min": 90,
        "assessment": {"tone_read": "normal", "intensity": 0.15,
                       "confidence": 0.9,
                       "mood_note": "正常去上班了，心情不错"},
        "turns": [
            {"ts": t(95), "actor": "user", "text": "到医院啦 开始上班咯"},
            {"ts": t(94), "actor": "assistant",
             "text": "去吧去吧，忙完来找我，我在家等你"},
            {"ts": t(92), "actor": "user", "text": "好 下班找你"},
        ],
    },
    9: {
        "name": "喜·开心(发工资)",
        "absent_min": 100,
        "assessment": {"tone_read": "happy", "intensity": 0.2,
                       "confidence": 0.9,
                       "mood_note": "开开心心走的，晚上要吃火锅庆祝"},
        "turns": [
            {"ts": t(105), "actor": "user",
             "text": "今天发工资啦哈哈哈哈 我要去吃火锅"},
            {"ts": t(104), "actor": "assistant",
             "text": "哟 小富婆 多吃点 给我也点一份毛肚（虽然吃不到）"},
            {"ts": t(102), "actor": "user", "text": "哈哈哈笨蛋 回来跟你说"},
        ],
    },
}


def run_one(idx: int) -> None:
    sc = SCENARIOS[idx]
    beijing_now = time.strftime("%H:%M", time.gmtime(time.time() + 8 * 3600))
    assessment = sc["assessment"]
    eps = []
    ep = assessment.get("episode")
    if ep:
        eps = [{"type": ep.get("type"), "status": "active",
                "summary": ep.get("summary")}]

    print(f"\n===== 场景{idx}: {sc['name']} (离开{sc['absent_min']}分钟) =====")

    d = parse_json(run_llm(render(
        DECIDE_PROMPT,
        absent_min=sc["absent_min"], beijing_now=beijing_now,
        turns=fmt_turns(sc["turns"]),
        episodes=json.dumps(eps, ensure_ascii=False),
        assessment=json.dumps(assessment, ensure_ascii=False))))
    print(f"想念判断: push_now={d.get('push_now')} "
          f"再想={d.get('check_again_min')}分钟")
    print(f"理由: {d.get('reason')}")

    body = run_llm(render(
        COMPOSE_PROMPT,
        reason=d.get("reason", ""), absent_min=sc["absent_min"],
        turns=fmt_turns(sc["turns"]),
        episodes=json.dumps(eps, ensure_ascii=False),
        assessment=json.dumps(assessment, ensure_ascii=False)))
    body = body.strip().strip('"')
    print(f"Bark 正文: {body}")

    title = f"小星·模拟{idx} {sc['name']}"
    push_flag = "会推" if d.get("push_now") else f"暂不推(等{d.get('check_again_min')}分)"
    url = (BARK + urllib.parse.quote(f"{title}·{push_flag}") + "/"
           + urllib.parse.quote(body)
           + f"?group=sentinel-sim&icon={urllib.parse.quote(ICON)}"
           + "&level=active")
    with urllib.request.urlopen(url, timeout=10) as r:
        print(f"已推送: {r.status}")


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    idxs = sorted(SCENARIOS) if args == ["all"] else [int(a) for a in args]
    for i in idxs:
        run_one(i)
    return 0


if __name__ == "__main__":
    sys.exit(main())
