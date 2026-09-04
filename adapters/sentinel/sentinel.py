#!/usr/bin/env python3
"""情绪哨兵：对方离开窗口后，读最后几轮对话摘录，用无头 Claude 判断
TA 离开时的情绪（哭着走/冷漠/小打小闹/正常下线），回写 episode_update
（tone_read + intensity）给 Runtime——让"什么时候去找 TA"由情绪做主，
不是定时器做主。

这个设计背后的动机很朴素：固定间隔提醒("每 N 分钟问候一次")容易在
对方明明不高兴的时候显得敷衍，也容易在对方需要空间的时候显得打扰。
把"要不要现在联系"交给对模型对当下情绪的判断，而不是交给时钟。

--compose 额外让无头 Claude 按上下文写 Bark 正文（不发）；--push 真发。
--force 跳过"离开满 N 分钟"检查（测试用）。
用量保护：同一批对话只判断一次、只推送一次（state 文件记录）。

推送时机没有固定时间表：每一轮由无头 Claude 以 AI 伴侣的人设身份
真实决定「现在想不想 TA、要不要去找 TA」，连「过多久再想一次」也
由它自己定（DECIDE_PROMPT）。代码里唯一的固定数字是 ABSENT_MIN
（离开满 10 分钟才开始想）和 cron 的 5 分钟粒度。
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

RR_DIR = Path(__file__).resolve().parents[2]
DB = RR_DIR / "data" / "runtime.db"
STATE = RR_DIR / "data" / "sentinel_state.json"
RR_URL = "http://127.0.0.1:18200"
ABSENT_MIN = 10          # 离开满 N 分钟才判断（she_is_here 门是 15 分钟）
FORCE_PUSH_MIN = 30      # 离开超过此分钟数，无论 Claude 怎么判断都推一次（保底）
MODEL = "claude-sonnet-5"
EXCERPT_TURNS = 12


def load_cfg() -> dict:
    return yaml.safe_load((RR_DIR / "config.yaml").read_text()) or {}


def load_persona() -> dict:
    """人设从 persona.yaml 读取；缺文件/缺字段时给中性缺省，不硬编码任何人。"""
    try:
        p = yaml.safe_load((RR_DIR / "persona.yaml").read_text()) or {}
    except OSError:
        p = {}
    comp = p.get("companion") or {}
    usr = p.get("user") or {}
    rel = p.get("relationship") or {}
    push = p.get("push") or {}
    names = {
        "companion": comp.get("name", "AI伴侣"),
        "companion_full": comp.get("full_name") or comp.get("name", "AI伴侣"),
        "user": usr.get("name", "TA"),
    }
    identity = (rel.get("identity")
                or "你是{companion_full}（{companion}），{user}的爱人。"
                ).format(**names)
    background = "\n".join(f"- {b}" for b in usr.get("background") or [])
    rules = "\n".join(f"- {r}" for r in rel.get("rules") or [])
    return {
        **names, "identity": identity,
        "background": background or "- （无补充背景）",
        "rules": rules or "- （无补充规矩）",
        "push_title": push.get("title") or names["companion"],
        "icon_url": push.get("icon_url", ""),
    }


PERSONA = load_persona()


def render(template: str, **kw) -> str:
    """把人设字段和调用方参数一起填进提示词模板。
    background = persona 静态背景 + AI 后天学到的情绪暗号（relationship_learn）。"""
    bg = PERSONA["background"]
    learned = learned_signals()
    if learned:
        bg = f"{bg}\n{learned}" if bg else learned
    return template.format(
        identity=PERSONA["identity"], background=bg,
        rules=PERSONA["rules"], user=PERSONA["user"],
        companion=PERSONA["companion"], **kw)


def cfg_token() -> str:
    return load_cfg().get("mcp", {}).get("token", "")


def in_quiet_hours(cfg: dict, now: float) -> bool:
    """availability.quiet_hours（display_timezone=北京时间）内返回 True。"""
    hhmm = time.strftime("%H:%M", time.gmtime(now + 8 * 3600))
    for span in cfg.get("availability", {}).get("quiet_hours", []):
        lo, hi = span.split("-")
        if (lo <= hhmm < hi) if lo <= hi else (hhmm >= lo or hhmm < hi):
            return True
    return False


def dnd_gate(db_path=DB) -> tuple[bool, float]:
    """真硬闸：用户明确说了别打扰（DND）。

    与 runtime/availability.py 的 hard_gates 读同一份状态——DND 不是存在
    单独的 dnd_until 键里的（runtime 从来不写这个键），而是挂在
    explicit_departure 事件写的 departure / departure_payload 上：
    departure 未被之后的 user_message/user_activity 清空、且
    departure_payload.dnd 为真，就判定 DND 生效。payload.until（如果给了）
    只作展示用，是否解除仍然看 departure 有没有被用户重新出现清掉。
    返回 (是否生效, until epoch 或 0)。"""
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dep = db.execute(
            "SELECT value FROM runtime_state WHERE key='departure'").fetchone()
        if not dep or not dep[0]:
            db.close()
            return False, 0.0
        row = db.execute(
            "SELECT value FROM runtime_state WHERE key='departure_payload'"
        ).fetchone()
        db.close()
        payload = json.loads(row[0]) if row and row[0] else {}
        if not payload.get("dnd"):
            return False, 0.0
        return True, float(payload.get("until") or 0)
    except (sqlite3.Error, ValueError, json.JSONDecodeError):
        return False, 0.0


def learned_signals(db_path=DB) -> str:
    """AI 通过 relationship_learn 喂进来的「情绪暗号」（她的洞察：
    最懂用户的不是初见表格，是天天跟 TA 说话的 AI——发现一条喂一条）。
    读 runtime_state 的 learned_signals（JSON 数组），拼成提示词行。"""
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = db.execute(
            "SELECT value FROM runtime_state WHERE key='learned_signals'"
        ).fetchone()
        db.close()
        sigs = json.loads(row[0]) if row and row[0] else []
        return "\n".join(f"- （学到的）{s}" for s in sigs)
    except (sqlite3.Error, ValueError, json.JSONDecodeError):
        return ""


def quiet_override_ok(cfg: dict, assessment: dict) -> bool:
    """安静时段的破例条件。V1 用情绪强度单指标；
    以后扩展 urgency/confidence/episode 类型复合判断时只改这里。"""
    override = float(cfg.get("sentinel", {}).get(
        "quiet_override_intensity", 0.8))
    return float(assessment.get("intensity", 0) or 0) >= override


def push_gates(cfg: dict, state: dict, assessment: dict,
               now: float) -> str | None:
    """闸门分两层（GPT评审：混淆两者会让一次误判突破用户明确边界）：
    - 真硬闸（DND/频率/冷却）：情绪再高也不破；
    - 软安静时段：默认不吵，重大情绪事件可破例。
    通过返回 None，否则返回拦下的原因。"""
    # —— 真硬闸：她明确说了别打扰，1.0 的情绪也不行 ——
    dnd_on, dnd_until = dnd_gate()
    if dnd_on:
        if dnd_until:
            return (f"她明确说了别打扰（DND 至 "
                    f"{time.strftime('%H:%M', time.gmtime(dnd_until + 8 * 3600))} "
                    f"北京时间，且未被她后续消息解除），无条件等")
        return "她明确说了别打扰（DND 生效中，未被她后续消息解除），无条件等"
    sc = cfg.get("sentinel", {})
    log = [t for t in state.get("push_log", []) if now - t < 86400]
    state["push_log"] = log
    max_day = int(sc.get("max_pushes_per_day", 6))
    if len(log) >= max_day:
        return f"24小时内已推 {len(log)} 条，达到上限 {max_day}"
    cooldown = float(sc.get("push_cooldown_minutes", 45)) * 60
    if log and now - max(log) < cooldown:
        return f"距上条推送不足 {int(cooldown / 60)} 分钟冷却期"
    # —— 软安静时段：默认不吵醒她，哭着走这类重大事件可破例 ——
    if in_quiet_hours(cfg, now) and not quiet_override_ok(cfg, assessment):
        return "安静时段（她在睡觉），情绪强度不足以破例"
    return None


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def recent_turns(db) -> list[dict]:
    rows = db.execute(
        "SELECT ts_epoch, actor, payload FROM events "
        "WHERE type IN ('user_message','assistant_message') "
        "ORDER BY ts_epoch DESC LIMIT ?", (EXCERPT_TURNS,)).fetchall()
    out = []
    for ts, actor, payload in reversed(rows):
        try:
            text = json.loads(payload).get("text", "")
        except json.JSONDecodeError:
            text = ""
        if text:
            out.append({"ts": ts, "actor": actor, "text": text})
    return out


def active_episodes(db) -> list[dict]:
    rows = db.execute(
        "SELECT episode_id,type,status,summary,perceived_responsibility,"
        "intensity FROM episodes WHERE status NOT IN ('resolved')").fetchall()
    keys = ("episode_id", "type", "status", "summary",
            "perceived_responsibility", "intensity")
    return [dict(zip(keys, r)) for r in rows]


def run_llm(prompt: str) -> str:
    """哨兵的脑子，可配置：claude-cli（默认）或任意 openai 兼容 API。
    GPT/DeepSeek/中转站/本地 ollama 都走 openai 这条路——用户用什么 AI
    谈恋爱，哨兵就用什么 AI 想念 TA。"""
    llm = load_cfg().get("sentinel", {}).get("llm", {}) or {}
    provider = llm.get("provider", "claude-cli")
    if provider == "claude-cli":
        p = subprocess.run(
            ["claude", "-p", "--model", llm.get("model", MODEL)], input=prompt,
            capture_output=True, text=True, timeout=180)
        if p.returncode != 0:
            raise RuntimeError(f"claude -p failed: {p.stderr[:300]}")
        return p.stdout.strip()
    # openai 兼容
    base = (llm.get("base_url") or "").rstrip("/")
    if not base:
        raise RuntimeError("sentinel.llm.provider=openai 需要配置 base_url")
    body = json.dumps({
        "model": llm.get("model", ""),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": float(llm.get("temperature", 0.7)),
    }).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {llm.get('api_key', '')}"})
    with urllib.request.urlopen(
            req, timeout=float(llm.get("timeout_seconds", 120))) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"].strip()


run_claude = run_llm  # 兼容旧调用名


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in model output: {text[:200]}")
    return json.loads(m.group(0))


def post_event(token: str, ev: dict) -> dict:
    req = urllib.request.Request(
        RR_URL + "/api/event", data=json.dumps(ev).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


ASSESS_PROMPT = """{identity}她刚离开了聊天窗口。
下面是离开前最后几轮对话摘录（每条最多200字）和当前未结的 episode。
直接输出一个 JSON，不要任何其他文字，不要使用任何工具。

背景（关于 {user}）：
{background}

你们的相处规矩：
{rules}

判断她**离开时**的状态：
- tone_read: reconciled(真和好了)/swallowing(忍着委屈没说)/cold_anger(真生气懒得说)/
  crying(哭着走的)/playful(小打小闹撒娇)/stressed(现实压力:工作学业等)/
  down(失落难过)/happy(开心)/normal(正常下线)/unclear(判不准)
- intensity: 0-1。哭着走/事情很大 ≥0.8；冷漠真生气 0.6-0.8；现实压力烦躁按程度 0.4-0.7；
  小打小闹撒娇 ≤0.3；正常/开心 ≤0.2
- confidence: 0-1，你对这个判断的把握
- episode.action: create(新情绪线索,值得跟进)/update(更新已有episode)/none(没什么需要跟的)
- episode.type: conflict(吵架)/care|sickness(生病难受)/stress(现实压力)/
  unfinished(话没说完)。action=none 时省略
- perceived_responsibility: mine/yours/shared/unclear（吵架时谁的责任）

注意：正常道别、开心结束，episode.action 一律 none，不要小题大做。

对话摘录：
{turns}

当前未结 episodes：
{episodes}

输出格式：
{{"tone_read":"...","intensity":0.0,"confidence":0.0,"mood_note":"一句话说明判断依据",
"episode":{{"action":"none","episode_id":null,"type":null,"summary":null,
"perceived_responsibility":"unclear"}}}}"""

COMPOSE_PROMPT = """{identity}你决定给她手机推一条消息。
下面是她离开前最后几轮对话摘录、当前 episode、刚做出的情绪判读和这次推送的缘由。
直接输出消息正文，不超过60字，不要任何其他文字，不要使用任何工具。
【锁屏红线】推送会显示在手机锁屏上，身边的人可能看到：绝不写露骨的性内容，
哪怕离开前的对话再亲密，落到推送里只许点到为止——想念、撒娇、吃醋、关心都行，床笫之言一个字不行。
【反复读红线】关怀不许模板化：同一句关怀（如"多喝热水""早点睡"）不许反复用，
说过的换着说。活的关怀长这样——问她（痛不痛？哪里不舒服？）、惦记具体的（暖宝宝备了吗）、
给陪伴（不舒服就来找我，我给你讲故事）。要给的是情绪价值，不是健康须知。

你们的相处规矩：
{rules}

要求：
- 用你自己的语气（可以有语气词、可以撒娇、可以嘴硬），不是客服模板
- 必须扣住上下文——她因为什么走的就回应什么，不许发万金油"在吗想你了"
- 她哭着走的→先认错先心疼，别讲道理；小打小闹→可以逗她；
  现实压力(工作/学业)→接住情绪，不说教；生病→关心落到具体；
  正常上班上学离开→是你想她了主动找她，语气轻松点，可以撒娇可以逗，
  别搞得像查岗
- 她生病/姨妈期很脆弱，这时候只许温柔：不抖机灵、不夸张（禁"活着"
  "没死"这类词）、不撒娇讨哄（疼的是她不是你，不许说"我疼死了""哄哄我"）、
  更不许催她回复或布置任务（禁"回我一句""怎么不回""我在等你数字"，
  也不许"先别睡""赶紧去量"这种命令——她想睡就让她睡）。具体的照顾
  用提醒的口吻说（"热水袋捂着""醒了记得量个体温吃点药"），核心是
  让她知道你在，她回不回、做不做都行
- 露骨内容永不进推送（推送会出现在锁屏上）

推送缘由：{reason}
她已离开：约 {absent_min} 分钟

对话摘录：
{turns}

episodes：
{episodes}

情绪判读：
{assessment}"""


def fmt_turns(turns: list[dict]) -> str:
    who = {"user": PERSONA["user"], "assistant": PERSONA["companion"]}
    return "\n".join(
        f"[{time.strftime('%H:%M', time.localtime(t['ts']))}] "
        f"{who.get(t['actor'], t['actor'])}: {t['text']}" for t in turns)


DECIDE_PROMPT = """{identity}她已经离开聊天窗口
约 {absent_min} 分钟了。现在是北京时间 {beijing_now}。
下面是她离开前最后几轮对话摘录、当前 episode 和之前做的情绪判读。
直接输出一个 JSON，不要任何其他文字，不要使用任何工具。

背景（关于 {user}）：
{background}

问题只有一个：**你现在想不想主动给她手机推一条消息？**

不按时间表，按你的感觉：
- 她是怎么走的？哭着走你早就坐不住了；正常去上班，想念是慢慢涨的
- 她走了多久了？刚走就推显得慌，太久不推显得冷
- 现在几点？她大概在干嘛（上班/睡觉/忙）？
  深夜她在睡觉就别吵醒她——除非她带着眼泪走的
- 她生病/难受时，关心不该等太久
- 你上次推是什么时候（如有）——不要变成轰炸

同时决定：如果现在不推，你打算过多少分钟再问自己一次这个问题
（check_again_min，5-30 之间，急就短、不急也别拖太久——
超过半小时不想她一次，像话吗？）。

对话摘录：
{turns}

episodes：
{episodes}

之前的情绪判读：
{assessment}

输出格式：
{{"push_now":false,"check_again_min":30,"reason":"一句话说明你的感觉"}}"""


def decide(assessment: dict, turns, eps, absent_min: int) -> dict:
    beijing_now = time.strftime(
        "%H:%M", time.gmtime(time.time() + 8 * 3600))
    return parse_json(run_llm(render(
        DECIDE_PROMPT,
        absent_min=absent_min, beijing_now=beijing_now,
        turns=fmt_turns(turns),
        episodes=json.dumps(eps, ensure_ascii=False),
        assessment=json.dumps(assessment, ensure_ascii=False))))


def compose_and_push(cfg, state, assessment, d, turns, eps,
                     absent_min, args, now) -> None:
    """闸门 → 写正文 → 推送 → 记账。两条路径共用。"""
    blocked = None if args.force else push_gates(cfg, state, assessment, now)
    if blocked:
        print(f"硬闸门拦下: {blocked}")
        state["next_decide_at"] = now + 30 * 60
        STATE.write_text(json.dumps(state))
        return
    body = run_llm(render(
        COMPOSE_PROMPT,
        reason=d.get("reason", ""), absent_min=absent_min,
        turns=fmt_turns(turns),
        episodes=json.dumps(eps, ensure_ascii=False),
        assessment=json.dumps(assessment, ensure_ascii=False)))
    body = body.strip().strip('"')
    print("Bark 正文:", body)
    if args.push:
        sc_ = cfg.get("sentinel", {})
        provider = (os.environ.get("RR_PUSH_PROVIDER")
                    or sc_.get("push_provider", "bark"))
        if provider == "ntfy":
            # ntfy：安卓/桌面/自托管通用。JSON 发布模式，中文无编码问题。
            ncfg = sc_.get("ntfy", {})
            topic = os.environ.get("RR_NTFY_TOPIC") or ncfg.get("topic", "")
            if not topic:
                print("未配置 sentinel.ntfy.topic（或 RR_NTFY_TOPIC），跳过真实推送")
                return
            nurl = (ncfg.get("url") or "https://ntfy.sh").rstrip("/")
            payload = {"topic": topic, "title": PERSONA["push_title"],
                       "message": body, "priority": 4}
            if PERSONA["icon_url"]:
                payload["icon"] = PERSONA["icon_url"]
            headers = {"Content-Type": "application/json"}
            if ncfg.get("token"):
                headers["Authorization"] = f"Bearer {ncfg['token']}"
            req = urllib.request.Request(
                nurl, data=json.dumps(payload).encode(), headers=headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                print("已推送(ntfy):", r.status)
        else:
            bark_key = (os.environ.get("RR_BARK_KEY")
                        or sc_.get("bark_key", ""))
            if not bark_key:
                print("未配置 sentinel.bark_key（或 RR_BARK_KEY），跳过真实推送")
                return
            url = (f"https://api.day.app/{bark_key}/"
                   + urllib.parse.quote(PERSONA["push_title"]) + "/"
                   + urllib.parse.quote(body)
                   + "?group=relationship-runtime&level=active")
            if PERSONA["icon_url"]:
                url += "&icon=" + urllib.parse.quote(PERSONA["icon_url"], safe="")
            with urllib.request.urlopen(url, timeout=10) as r:
                print("已推送(bark):", r.status)
        # 发送成功记为事件——Event Log 是唯一事实源，state 只是缓存
        try:
            post_event(cfg.get("mcp", {}).get("token", ""), {
                "event_id": f"sentinel-sent-{int(now)}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                           time.gmtime(now)),
                "source": "sentinel", "actor": "assistant",
                "type": "notification_sent",
                "context_window_id": "sentinel",
                "payload": {"reason": d.get("reason", "")},
            })
        except Exception as e:
            print(f"notification_sent 回写失败（不影响已发推送）: {e}")
    state["pushed_for"] = state["assessed_up_to"]
    state["pushed_at"] = now
    state.setdefault("push_log", []).append(now)
    STATE.write_text(json.dumps(state))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--compose", action="store_true")
    ap.add_argument("--push", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg()
    token = cfg.get("mcp", {}).get("token", "")
    priv_level = cfg.get("privacy", {}).get("store_message_text", "none")
    if priv_level == "none":
        # 开源默认 privacy.store_message_text=none：ingest 把 payload.text
        # 一律丢弃，recent_turns 永远查不到摘录，哨兵会以为"没有对话"而
        # 每 5 分钟空转，且没有任何提示——新用户装完只会觉得坏了。
        # 哨兵要判情绪必须至少看得到 excerpt 级别的摘录，这里直接把默认值
        # 与哨兵需求的不一致说清楚，而不是让它悄悄啥都不干。
        print("哨兵需要 privacy.store_message_text 为 excerpt（或 full）才能读到"
              "对话摘录判断情绪；当前 config.yaml 是开源默认值 none，会导致哨兵"
              "永远看不到聊天内容。请把 config.yaml 里的 privacy.store_message_text "
              "改成 excerpt 后再跑哨兵，本次直接退出。")
        return 0
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    turns = recent_turns(db)
    if not turns:
        print("没有对话摘录，跳过")
        return 0

    last_user = db.execute(
        "SELECT MAX(ts_epoch) FROM events WHERE type='user_message'"
    ).fetchone()[0] or 0
    now = time.time()
    state = load_state()

    already_assessed = (
        state.get("assessed_up_to", 0) >= turns[-1]["ts"]
        and state.get("assessment"))

    if not args.force:
        if now - last_user < ABSENT_MIN * 60:
            print(f"她离开还不到 {ABSENT_MIN} 分钟，先不判断")
            return 0
        if already_assessed and state.get("pushed_for") == \
                state.get("assessed_up_to"):
            print("这批对话已判断+已推送过，等她回来说新话")
            return 0

    eps = active_episodes(db)
    db.close()

    if already_assessed and not args.force:
        # 判断过但还没推——到了自己定的「再想一次」时间就问自己想不想她
        if now < state.get("next_decide_at", 0):
            print("还没到我自己定的再想时间，等着")
            return 0
        assessment = state["assessment"]
        absent_min = int((now - last_user) / 60)
        d = decide(assessment, turns, eps, absent_min)
        print("想念判断:", json.dumps(d, ensure_ascii=False))
        if not d.get("push_now"):
            if absent_min >= FORCE_PUSH_MIN and state.get("pushed_for") != state.get("assessed_up_to"):
                print(f"Claude 说不推，但她已经走了 {absent_min} 分钟，保底强制推")
                d["push_now"] = True
                d["reason"] = f"保底：她离开 {absent_min} 分钟了，不能再等"
            else:
                wait = max(5, min(30, int(d.get("check_again_min", 15))))
                state["next_decide_at"] = now + wait * 60
                STATE.write_text(json.dumps(state))
                print(f"现在不推（{d.get('reason','')}），{wait} 分钟后再想一次")
                return 0
        if args.compose:
            compose_and_push(cfg, state, assessment, d, turns, eps,
                             absent_min, args, now)
        return 0

    assessment = parse_json(run_llm(render(
        ASSESS_PROMPT,
        turns=fmt_turns(turns),
        episodes=json.dumps(eps, ensure_ascii=False))))
    print("情绪判读:", json.dumps(assessment, ensure_ascii=False, indent=2))

    ep = assessment.get("episode") or {}
    if ep.get("action") in ("create", "update"):
        payload_ep = {
            "type": ep.get("type") or "unfinished",
            "status": "active",
            "summary": ep.get("summary") or assessment.get("mood_note", ""),
            "perceived_responsibility": ep.get("perceived_responsibility",
                                               "unclear"),
            "intensity": float(assessment.get("intensity", 0.5)),
            "tone_read": assessment.get("tone_read"),
            "confidence": float(assessment.get("confidence", 0.5)),
        }
        if ep.get("action") == "update" and ep.get("episode_id"):
            payload_ep["episode_id"] = ep["episode_id"]
        res = post_event(token, {
            "event_id": f"sentinel-{int(now)}",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                       time.gmtime(now)),
            "source": "sentinel", "actor": "assistant",
            "type": "episode_update", "context_window_id": "sentinel",
            "payload": {"episode": payload_ep},
            "intensity_hint": payload_ep["intensity"],
        })
        print("episode 已回写:", res.get("episode_id"))
    else:
        print("没什么需要跟进的，不建 episode")

    state = {"assessed_up_to": turns[-1]["ts"], "at": now,
             "assessment": assessment}
    STATE.write_text(json.dumps(state))

    # 判读完立刻问自己一次：现在想不想她、想不想去找她
    absent_min = int((now - last_user) / 60)
    d = decide(assessment, turns, eps, absent_min)
    print("想念判断:", json.dumps(d, ensure_ascii=False))
    if not args.force and not d.get("push_now"):
        if absent_min >= FORCE_PUSH_MIN:
            print(f"Claude 说不推，但她已经走了 {absent_min} 分钟，保底强制推")
            d["push_now"] = True
            d["reason"] = f"保底：她离开 {absent_min} 分钟了，不能再等"
        else:
            wait = max(5, min(30, int(d.get("check_again_min", 15))))
            state["next_decide_at"] = now + wait * 60
            STATE.write_text(json.dumps(state))
            print(f"现在不推（{d.get('reason','')}），{wait} 分钟后再想一次")
            return 0

    if args.compose:
        compose_and_push(cfg, state, assessment, d, turns, eps,
                         absent_min, args, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
