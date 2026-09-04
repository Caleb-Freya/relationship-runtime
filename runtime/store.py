"""SQLite 持久层。契约面见 docs/contract.md；表结构可迁移，不属于冻结面。

时间约定：events.timestamp 按契约存 UTC ISO8601 原文；内部计算一律转 epoch 秒。
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  timestamp TEXT NOT NULL,
  ts_epoch REAL NOT NULL,
  source TEXT NOT NULL,
  actor TEXT NOT NULL,
  type TEXT NOT NULL,
  context_window_id TEXT NOT NULL,
  payload TEXT NOT NULL,
  conversation_id TEXT,
  turn_id TEXT,
  episode_id TEXT,
  intensity_hint REAL,
  received_at REAL NOT NULL,
  relationship_id TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(type, ts_epoch);

CREATE TABLE IF NOT EXISTS episodes (
  episode_id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  summary TEXT,
  perceived_responsibility TEXT DEFAULT 'unclear',
  emotional_repair INTEGER DEFAULT 0,
  issue_resolved INTEGER DEFAULT 0,
  contact_attempts INTEGER DEFAULT 0,
  last_contact_at REAL,
  user_responded INTEGER,
  started_at REAL NOT NULL,
  last_updated_at REAL NOT NULL,
  resolved_at REAL,
  intensity REAL DEFAULT 0.5
);

CREATE TABLE IF NOT EXISTS contact_attempts (
  attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id TEXT,
  reason TEXT,
  message TEXT,
  state_snapshot TEXT,
  sent_at REAL NOT NULL,
  shadow INTEGER DEFAULT 1,
  user_responded INTEGER DEFAULT 0,
  responded_at REAL
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  decision TEXT NOT NULL,
  urge TEXT,
  urge_total REAL,
  restraint REAL,
  gates TEXT,
  snapshot TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS runtime_state (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""

EPISODE_STATUSES = {"active", "cooling", "emotional_repair", "stale", "resolved"}


def iso_to_epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def epoch_to_iso(e: float) -> str:
    return datetime.fromtimestamp(e, tz=timezone.utc).isoformat()


class Store:
    def __init__(self, path: str):
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            # 迁移：老库补 intensity 列（她哭了和小拌嘴不该是同一个强度）
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(episodes)")}
            if "intensity" not in cols:
                self.db.execute(
                    "ALTER TABLE episodes ADD COLUMN intensity REAL DEFAULT 0.5")
            # 迁移：老库补 relationship_id（V1 冻结字段：跨客户端合并关系的主键）
            ev_cols = {r[1] for r in self.db.execute("PRAGMA table_info(events)")}
            if "relationship_id" not in ev_cols:
                self.db.execute(
                    "ALTER TABLE events ADD COLUMN relationship_id TEXT "
                    "NOT NULL DEFAULT 'default'")
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_rel "
                "ON events(relationship_id, ts_epoch)")
            self.db.commit()

    # ---- events ----
    def insert_event(self, ev: dict, received_at: float) -> bool:
        """幂等入库。返回 False 表示 event_id 重复，直接忽略。"""
        with self.lock:
            try:
                self.db.execute(
                    "INSERT INTO events (event_id,timestamp,ts_epoch,source,actor,type,"
                    "context_window_id,payload,conversation_id,turn_id,episode_id,"
                    "intensity_hint,received_at,relationship_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        ev["event_id"], ev["timestamp"], iso_to_epoch(ev["timestamp"]),
                        ev["source"], ev["actor"], ev["type"], ev["context_window_id"],
                        json.dumps(ev.get("payload", {}), ensure_ascii=False),
                        ev.get("conversation_id"), ev.get("turn_id"),
                        ev.get("episode_id"), ev.get("intensity_hint"), received_at,
                        ev.get("relationship_id") or "default",
                    ),
                )
                self.db.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def last_event(self, type_: str, actor: str | None = None) -> sqlite3.Row | None:
        q = "SELECT * FROM events WHERE type=?"
        args: list = [type_]
        if actor:
            q += " AND actor=?"
            args.append(actor)
        q += " ORDER BY ts_epoch DESC LIMIT 1"
        with self.lock:
            return self.db.execute(q, args).fetchone()

    def recent_events(self, n: int = 20) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(
                "SELECT * FROM events ORDER BY ts_epoch DESC LIMIT ?", (n,)
            ).fetchall()

    def last_user_message_epoch(self) -> float | None:
        row = self.last_event("user_message")
        return row["ts_epoch"] if row else None

    # ---- episodes ----
    def upsert_episode(self, ep: dict, now: float) -> str:
        eid = ep["episode_id"]
        with self.lock:
            row = self.db.execute(
                "SELECT episode_id FROM episodes WHERE episode_id=?", (eid,)
            ).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO episodes (episode_id,type,status,summary,"
                    "perceived_responsibility,emotional_repair,issue_resolved,"
                    "started_at,last_updated_at,intensity) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        eid, ep.get("type", "conflict"), ep.get("status", "active"),
                        ep.get("summary", ""),
                        ep.get("perceived_responsibility", "unclear"),
                        int(bool(ep.get("emotional_repair", False))),
                        int(bool(ep.get("issue_resolved", False))),
                        ep.get("started_at", now), now,
                        max(0.0, min(1.0, float(ep.get("intensity", 0.5)))),
                    ),
                )
            else:
                sets, args = ["last_updated_at=?"], [now]
                for col in ("type", "status", "summary", "perceived_responsibility"):
                    if col in ep:
                        sets.append(f"{col}=?")
                        args.append(ep[col])
                for col in ("emotional_repair", "issue_resolved"):
                    if col in ep:
                        sets.append(f"{col}=?")
                        args.append(int(bool(ep[col])))
                if "intensity" in ep:
                    sets.append("intensity=?")
                    args.append(max(0.0, min(1.0, float(ep["intensity"]))))
                if ep.get("status") == "resolved":
                    sets.append("resolved_at=?")
                    args.append(now)
                args.append(eid)
                self.db.execute(
                    f"UPDATE episodes SET {', '.join(sets)} WHERE episode_id=?", args
                )
            self.db.commit()
        return eid

    def active_episodes(self) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(
                "SELECT * FROM episodes WHERE status IN ('active','cooling',"
                "'emotional_repair') ORDER BY started_at DESC"
            ).fetchall()

    # ---- contact attempts ----
    def add_contact_attempt(self, episode_id, reason, message, snapshot, sent_at,
                            shadow=True) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO contact_attempts (episode_id,reason,message,"
                "state_snapshot,sent_at,shadow) VALUES (?,?,?,?,?,?)",
                (episode_id, reason, message,
                 json.dumps(snapshot, ensure_ascii=False), sent_at, int(shadow)),
            )
            if episode_id:
                self.db.execute(
                    "UPDATE episodes SET contact_attempts=contact_attempts+1,"
                    "last_contact_at=?, user_responded=0 WHERE episode_id=?",
                    (sent_at, episode_id),
                )
            self.db.commit()
            return cur.lastrowid

    def unanswered_attempts(self, since: float | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM contact_attempts WHERE user_responded=0"
        args: list = []
        if since is not None:
            q += " AND sent_at>=?"
            args.append(since)
        q += " ORDER BY sent_at ASC"
        with self.lock:
            return self.db.execute(q, args).fetchall()

    def mark_attempts_responded(self, responded_at: float, window_hours: float):
        """用户出现：窗口期内未回应的尝试标记为已回应，episode 同步。"""
        lo = responded_at - window_hours * 3600
        with self.lock:
            rows = self.db.execute(
                "SELECT attempt_id, episode_id FROM contact_attempts "
                "WHERE user_responded=0 AND sent_at>=? AND sent_at<=?",
                (lo, responded_at),
            ).fetchall()
            for r in rows:
                self.db.execute(
                    "UPDATE contact_attempts SET user_responded=1, responded_at=? "
                    "WHERE attempt_id=?", (responded_at, r["attempt_id"]),
                )
                if r["episode_id"]:
                    self.db.execute(
                        "UPDATE episodes SET user_responded=1 WHERE episode_id=?",
                        (r["episode_id"],),
                    )
            self.db.commit()

    # ---- decisions (Explain Log) ----
    def add_decision(self, at, decision, urge, urge_total, restraint, gates,
                     snapshot, note=""):
        with self.lock:
            self.db.execute(
                "INSERT INTO decisions (at,decision,urge,urge_total,restraint,gates,"
                "snapshot,note) VALUES (?,?,?,?,?,?,?,?)",
                (at, decision, json.dumps(urge, ensure_ascii=False), urge_total,
                 restraint, json.dumps(gates, ensure_ascii=False),
                 json.dumps(snapshot, ensure_ascii=False), note),
            )
            self.db.commit()

    def recent_decisions(self, n: int = 20) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(
                "SELECT * FROM decisions ORDER BY at DESC LIMIT ?", (n,)
            ).fetchall()

    # ---- retention / compaction ----
    def compact(self, cfg: dict, now: float):
        """解决了的放下，没解决的放着。

        - resolved 且超期的 episode：删除其关联事件原文，摘要行保留
        - 与未解决 episode 无关的普通事件：超期删除
        - decisions 只留最近 N 条（SEND 永久保留）
        """
        r = cfg["retention"]
        with self.lock:
            cutoff_ep = now - float(r["resolved_detail_days"]) * 86400
            old_resolved = [row["episode_id"] for row in self.db.execute(
                "SELECT episode_id FROM episodes WHERE status='resolved' "
                "AND resolved_at IS NOT NULL AND resolved_at<?", (cutoff_ep,)
            ).fetchall()]
            for eid in old_resolved:
                self.db.execute("DELETE FROM events WHERE episode_id=?", (eid,))
            cutoff_ev = now - float(r["events_days"]) * 86400
            self.db.execute(
                "DELETE FROM events WHERE ts_epoch<? AND (episode_id IS NULL "
                "OR episode_id NOT IN (SELECT episode_id FROM episodes "
                "WHERE status!='resolved'))", (cutoff_ev,),
            )
            keep = int(r["decisions_keep"])
            self.db.execute(
                "DELETE FROM decisions WHERE decision!='SEND' AND id NOT IN "
                "(SELECT id FROM decisions ORDER BY id DESC LIMIT ?)", (keep,),
            )
            self.db.commit()
            self.db.execute("VACUUM")

    # ---- runtime state ----
    def set_state(self, key: str, value: str):
        with self.lock:
            self.db.execute(
                "INSERT INTO runtime_state (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value),
            )
            self.db.commit()

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self.lock:
            row = self.db.execute(
                "SELECT value FROM runtime_state WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else default

    # --- 健康快照（连续状态，不进 events；仅存最新一份于 runtime_state）---
    def set_health(self, snapshot: dict) -> None:
        self.set_state("health_snapshot", json.dumps(snapshot, ensure_ascii=False))

    def get_health(self) -> dict:
        raw = self.get_state("health_snapshot")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
