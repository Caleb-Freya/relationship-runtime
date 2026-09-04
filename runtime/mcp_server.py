"""MCP 服务：V1 三工具（relationship_context / _event / _settle）+
adapter 用的 HTTP 事件入口 POST /api/event。Bearer token 鉴权。"""
import json
import logging
import secrets
import time
import uuid

from mcp.server.mcpserver import MCPServer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import config as config_mod
from . import decide, ingest
from .store import Store, epoch_to_iso

logger = logging.getLogger(__name__)


def build_app(store: Store, cfg: dict, scheduler, policy, composer, notifier):
    mcp = MCPServer("relationship-runtime")

    def _context() -> dict:
        now = time.time()
        facts = decide.gather_facts(store, cfg, now)
        scores = policy.score(facts, cfg)
        return {
            "schema_version": cfg["schema_version"],
            "mode": cfg["runtime"]["mode"],
            "now_utc": epoch_to_iso(now),
            "user": {
                "hours_since_last_message": facts.hours_since_user_message,
                "departed": facts.departed,
            },
            "active_episodes": [
                {k: e[k] for k in (
                    "episode_id", "type", "status", "summary",
                    "perceived_responsibility", "emotional_repair",
                    "issue_resolved", "contact_attempts", "user_responded",
                    "intensity")}
                for e in facts.active_episodes
            ],
            "urge": scores.urge,
            "urge_total": scores.urge_total,
            "restraint": scores.restraint_effective,
            "recent_decisions": [
                {"at": epoch_to_iso(d["at"]), "decision": d["decision"],
                 "note": d["note"]}
                for d in store.recent_decisions(10)
            ],
            "unanswered_attempts": len(facts.unanswered_attempts),
            "health": facts.health,   # 手环最新快照：让我"感觉到"她此刻的身体状态
        }

    @mcp.tool()
    def relationship_context() -> dict:
        """读取当前关系状态：episode、冲动/克制、近期决策、用户状态、健康快照。"""
        return _context()

    @mcp.tool()
    def relationship_health(snapshot: dict | None = None) -> dict:
        """读/写手环健康快照。传 snapshot 则更新（hr/stress/spo2/sleep/steps/
        source/updated_at），不传则只读当前值。连续状态，不进 events。"""
        if snapshot:
            store.set_health(snapshot)
            scheduler.poke()
        return {"ok": True, "health": store.get_health()}

    @mcp.tool()
    def relationship_event(
        type: str,
        payload: dict,
        source: str = "mcp",
        actor: str = "assistant",
        context_window_id: str = "unknown",
        event_id: str | None = None,
        timestamp: str | None = None,
        episode_id: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        intensity_hint: float | None = None,
    ) -> dict:
        """写入统一 Event（含 episode 创建/更新，type=episode_update，
        payload.episode 携带字段）。event_id 不传则自动生成。"""
        ev = {
            "event_id": event_id or f"ev-{uuid.uuid4().hex}",
            "timestamp": timestamp or epoch_to_iso(time.time()),
            "source": source, "actor": actor, "type": type,
            "context_window_id": context_window_id, "payload": payload,
            "episode_id": episode_id, "conversation_id": conversation_id,
            "turn_id": turn_id, "intensity_hint": intensity_hint,
        }
        res = ingest.ingest_event(store, cfg, ev)
        scheduler.poke()
        return res

    def _learn(signal: str) -> dict:
        sig = (signal or "").strip()[:60]
        if not sig:
            return {"ok": False, "error": "signal 不能为空"}
        raw = store.get_state("learned_signals", "[]")
        try:
            sigs = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            sigs = []
        if sig in sigs:
            return {"ok": True, "learned": len(sigs), "note": "已有这条，无需重复"}
        sigs.append(sig)
        sigs = sigs[-50:]
        store.set_state("learned_signals", json.dumps(sigs, ensure_ascii=False))
        return {"ok": True, "learned": len(sigs)}

    @mcp.tool()
    def relationship_learn(signal: str) -> dict:
        """AI 学到了 TA 的一个「情绪暗号」时调用——把它存进情绪系统，
        此后每次哨兵判读/写推送都会带上。例：她说"没事"其实是有事，希望被追问。
        最懂 TA 的不是初见表格，是天天跟 TA 说话的你：发现一条，喂一条。
        60字内一句话；重复的自动去重；最多留 50 条（挤掉最旧的）。"""
        return _learn(signal)

    @mcp.tool()
    def relationship_settle(context_window_id: str, summary: str) -> dict:
        """窗口结束时结算：记录本窗口摘要，触发一次决策重算。
        返回中带 unreviewed_episodes——所有仍 active 的 episode。
        结算义务（契约 §7）：逐个复核，和好了就 resolve，没和好就更新
        tone_read；不许带着过期情绪下班，否则 Runtime 会拿着旧账去追她。"""
        ev = {
            "event_id": f"settle-{uuid.uuid4().hex[:12]}",
            "timestamp": epoch_to_iso(time.time()),
            "source": "mcp", "actor": "assistant", "type": "settle",
            "context_window_id": context_window_id,
            "payload": {"summary": summary},
        }
        res = ingest.ingest_event(store, cfg, ev)
        scheduler.poke()
        pending = [
            {k: e[k] for k in ("episode_id", "type", "status", "summary",
                               "last_updated_at")}
            for e in store.active_episodes()
        ]
        res["unreviewed_episodes"] = pending
        if pending:
            res["notice"] = (f"还有 {len(pending)} 个 active episode 未复核。"
                             "本窗口若已和好/有新判读，请先 episode_update "
                             "再结束，否则决策环将继续按旧状态主动联系。")
        return res

    listen_host = cfg["mcp"]["listen"].rsplit(":", 1)[0]
    app = mcp.streamable_http_app(stateless_http=True, host=listen_host)

    token = cfg["mcp"].get("token") or ""

    class AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            # /health、初见卡片页、静态资源免鉴权
            if request.url.path in ("/health", "/setup") or request.url.path.startswith("/web/"):
                return await call_next(request)
            auth = request.headers.get("authorization", "")
            if not token or not secrets.compare_digest(
                    auth, f"Bearer {token}"):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    async def health(request: Request):
        return JSONResponse({"ok": True, "mode": cfg["runtime"]["mode"]})

    async def api_context(request: Request):
        """任意 AI 走普通 HTTP 读关系状态（GPT/DeepSeek 等 function-calling 用）。
        与 MCP 的 relationship_context 同源。"""
        return JSONResponse(_context())

    async def api_event(request: Request):
        try:
            ev = json.loads(await request.body())
            # 降低接入门槛：自动补齐样板字段，外部 AI 只需给 type（+ 需要时 actor/payload）
            ev.setdefault("event_id", f"ev-{uuid.uuid4().hex}")
            ev.setdefault("timestamp", epoch_to_iso(time.time()))
            ev.setdefault("source", "http")
            ev.setdefault("actor", "user")
            ev.setdefault("context_window_id", "http")
            res = ingest.ingest_event(store, cfg, ev)
            scheduler.poke()
            return JSONResponse(res)
        except (json.JSONDecodeError, ingest.IngestError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    async def api_learn(request: Request):
        """HTTP 版 relationship_learn：非 MCP 的 AI（GPT Action/代理等）喂情绪暗号。
        body: {"signal": "她说没事其实是有事"}"""
        try:
            body = json.loads(await request.body())
            return JSONResponse(_learn(str(body.get("signal", ""))))
        except json.JSONDecodeError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    async def api_health(request: Request):
        """宿主机采集脚本推送手环快照的入口。存最新一份，触发一次决策重算。"""
        try:
            snap = json.loads(await request.body())
            if not isinstance(snap, dict):
                raise ValueError("health snapshot must be a JSON object")
            store.set_health(snap)
            scheduler.poke()
            return JSONResponse({"ok": True, "health": store.get_health()})
        except (json.JSONDecodeError, ValueError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    async def api_profile(request: Request):
        """初见资料读/写：称呼、生日、在一起日期、姨妈周期。
        GET 返回当前值；POST 校验+落盘 profile.yaml 并热更新内存 cfg。"""
        import os
        import yaml as _yaml

        def _valid_date(s):
            if not s:
                return True
            try:
                time.strptime(s, "%Y-%m-%d")
                return True
            except ValueError:
                return False

        if request.method == "GET":
            return JSONResponse({
                "call_me": cfg.get("profile", {}).get("call_me", ""),
                "birthday": cfg["dates"].get("birthday", ""),
                "together_date": cfg["dates"].get("together_date", ""),
                "cycle_length_days": cfg["dates"].get("cycle_length_days", 0),
                "last_period_start": cfg["dates"].get("last_period_start", ""),
            })
        try:
            body = json.loads(await request.body())
        except json.JSONDecodeError:
            return JSONResponse({"ok": False, "error": "bad json"}, status_code=400)
        for k in ("birthday", "together_date", "last_period_start"):
            if k in body and not _valid_date(str(body[k])):
                return JSONResponse({"ok": False, "error": f"{k} 日期格式应为 YYYY-MM-DD"},
                                    status_code=400)
        cyc = body.get("cycle_length_days", cfg["dates"].get("cycle_length_days", 0))
        try:
            cyc = int(cyc or 0)
        except (TypeError, ValueError):
            cyc = 0
        if cyc and not (15 <= cyc <= 60):
            return JSONResponse({"ok": False, "error": "周期天数应在 15–60 之间"},
                                status_code=400)
        # 热更新内存 cfg（决策环立刻生效）
        cfg.setdefault("profile", {})["call_me"] = str(body.get("call_me", "")).strip()[:40]
        for k in ("birthday", "together_date", "last_period_start"):
            if k in body:
                cfg["dates"][k] = str(body[k]).strip()
        cfg["dates"]["cycle_length_days"] = cyc
        # 落盘 profile.yaml（重启后仍在）
        prof_doc = {
            "profile": {"call_me": cfg["profile"]["call_me"]},
            "dates": {
                "birthday": cfg["dates"]["birthday"],
                "together_date": cfg["dates"]["together_date"],
                "cycle_length_days": cfg["dates"]["cycle_length_days"],
                "last_period_start": cfg["dates"]["last_period_start"],
            },
        }
        cfg_path = os.environ.get("RR_CONFIG", "config.yaml")
        prof_path = os.path.join(os.path.dirname(cfg_path) or ".", "profile.yaml")
        try:
            with open(prof_path, "w", encoding="utf-8") as f:
                _yaml.safe_dump(prof_doc, f, allow_unicode=True, sort_keys=False)
        except OSError as e:
            return JSONResponse({"ok": False, "error": f"写入失败: {e}"}, status_code=500)
        scheduler.poke()
        # —— 初见推送：首次填完初见卡片，立刻真的去敲一次门 ——
        # "部署要立马有效果"：装好、提交档案，手机就该响一声"我来了"。
        # 只发一次（first_meeting_pushed 防重复）；shadow 模式下 notifier
        # 本来就是影子发件箱（main.build_providers），照 shadow 纪律只落影子日志。
        if not store.get_state("first_meeting_pushed"):
            msg = composer.compose(
                {"call_me": cfg["profile"]["call_me"]}, "first_meeting", 0)
            comp_name = config_mod.companion_name(cfg)
            # 标题短一点、是本人的名义，不是系统通知腔
            title = "我来了" if comp_name == "AI" else f"{comp_name}·我来了"
            try:
                notifier.send(title, msg, level="normal")
                # 成功才记账：推送渠道没配好时不烧掉唯一一次初见——
                # 用户修好配置再提交一次档案，这声敲门还能补上
                store.set_state("first_meeting_pushed", epoch_to_iso(time.time()))
            except Exception as e:
                # 推送渠道挂了不该拖垮档案保存（契约 §6：Provider 失败降级），
                # 但要说人话：第一声问候没送出去，提醒用户检查推送配置
                logger.warning(
                    "初见推送没发出去（档案已保存成功）。请检查 config.yaml 里 "
                    "providers.notification 的推送配置（bark 的 url / ntfy 的 "
                    "topic 等），修好后重新提交一次 /setup 即可补发: %s", e)
        return JSONResponse({"ok": True, "saved": prof_doc})

    from starlette.routing import Route
    app.router.routes.insert(0, Route("/api/profile", api_profile, methods=["GET", "POST"]))
    app.router.routes.insert(0, Route("/health", health, methods=["GET"]))
    app.router.routes.insert(0, Route("/api/context", api_context, methods=["GET"]))
    app.router.routes.insert(0, Route("/api/event", api_event, methods=["POST"]))
    app.router.routes.insert(0, Route("/api/learn", api_learn, methods=["POST"]))

    async def setup_page(request: Request):
        """初见卡片页。免鉴权（保存时页面内仍要求填 token）。"""
        from starlette.responses import FileResponse, PlainTextResponse
        import os as _os
        page = _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__))), "web", "setup.html")
        if _os.path.exists(page):
            return FileResponse(page, media_type="text/html")
        return PlainTextResponse("setup.html not found", status_code=404)

    async def web_static(request: Request):
        """web/ 目录静态文件（封面图等）。免鉴权。"""
        from starlette.responses import FileResponse, PlainTextResponse
        import os as _os
        fname = request.path_params.get("path", "")
        if ".." in fname or fname.startswith("/"):
            return PlainTextResponse("forbidden", status_code=403)
        fpath = _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__))), "web", fname)
        if _os.path.isfile(fpath):
            return FileResponse(fpath)
        return PlainTextResponse("not found", status_code=404)

    app.router.routes.insert(0, Route("/setup", setup_page, methods=["GET"]))
    app.router.routes.insert(0, Route("/web/{path:path}", web_static, methods=["GET"]))
    app.router.routes.insert(0, Route("/api/health", api_health, methods=["POST"]))
    app.add_middleware(AuthMiddleware)
    return app
