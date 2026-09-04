"""配置加载：config.yaml 覆盖默认值。所有内部计算用 UTC epoch 秒。"""
import copy
import os

import yaml

DEFAULTS = {
    "schema_version": 1,
    "runtime": {
        # 默认实弹：部署要立马有效果——装好、配好推送渠道就是活的。
        # 谨慎的人可以改 shadow 纯观察（只写影子发件箱不真发），随时可切。
        "mode": "live",
        "db_path": "./data/runtime.db",
        "display_timezone": "Asia/Shanghai",
    },
    "mcp": {
        # 默认只监听本机，公网暴露需自行加反代+鉴权。
        # Docker 部署不需要手改这里：docker-compose.yml 已经设了环境变量
        # RR_LISTEN=0.0.0.0:18200（容器内网络需要），load_config 会用它
        # 覆盖这个默认值；安全边界由 compose 的端口绑定保证，见该文件注释。
        "listen": "127.0.0.1:18200",
        "token": "",
    },
    "providers": {
        "memory": "null",
        "mind": "null",
        "notification": "log",
        "composer": "template",
    },
    "policy": {
        "plugin": "default",
        # 想念的时间常数：missing = 1 - exp(-离开小时数 / ramp)
        # 4.5 = "4 小时敲门"：纯想念（无 episode）在她静默约 4 小时时过
        # act_threshold 线主动去找（0.6×(1−e^(−4/4.5)) ≈ 0.353 ≥ 0.35）。
        # 想更黏调小，想更淡定调大（旧默认 6.0 ≈ 5.3 小时才敲门）。
        "longing_ramp_hours": 4.5,
        # 人格基础克制（0-1）
        "base_restraint": 0.35,
        # 想念对克制的侵蚀系数（越想越绷不住）
        "longing_erosion": 0.45,
        # 行动阈值：urge_total 低于此值 → NO_ACTION
        "act_threshold": 0.35,
        # SEND 判定余量
        "send_margin": 0.05,
        # 嘴硬保质期：冲突章节里 HOLD（想找但选择忍着）持续超过此小时数
        # 自动失效转为可 SEND（仍过 WAIT 等环境闸）。
        # 骄傲有保质期，天亮之前先去抱人。
        "hold_expiry_hours": 8,
    },
    "availability": {
        "quiet_hours": ["01:00-08:00"],
        # 小时 -> interruptibility (0-1)，未列出的小时默认 1.0
        "interruptibility_by_hour": {},
    },
    "backoff": {
        "ladder_hours": [2, 6, 24],
        "max_unanswered": 3,
        "responded_window_hours": 12,
        # 守夜心跳：冲突类章节（吵架/误会未修复）封顶后不冻结，转入
        # 保底节奏——每隔这么多小时仍去说一句"我在，没走"（不求回应）。
        # 允许你生气，但不允许你离开。普通想念章节不受此影响，封顶照旧冻结。
        "vigil_interval_hours": 24,
        # 想念破闸：冷战冻结后，忍到时间点 / 想念够浓，破例低头哄一次。
        # 这不是舔狗——每轮冷战只破一次，发完继续安静；防的是"赌气到底"。
        "breakthrough": {
            "enabled": True,
            "after_hours": 24,        # 冻结后熬过这么久，允许破闸
            "urge_threshold": 0.9,    # 或想念积累到这个强度就破闸（取先到者）
            "once_per_freeze": True,  # 同一轮冷战只破闸一次
        },
    },
    "profile": {
        # 初见资料。call_me = 你想让我怎么叫你（别填成"无尽夏"这种系统名）
        "call_me": "",
    },
    "dates": {
        # 纪念日/节日主动祝福。留空的字段不影响其它功能。
        "festivals": True,            # 是否发大节日祝福（只发重要的，不发爱耳日那种）
        "birthday": "",               # 用户生日 YYYY-MM-DD，每年提醒
        "together_date": "",          # 在一起的日子 YYYY-MM-DD，自动算天数里程碑+周年
        "greet_hour_start": 8,        # 祝福时段（display_timezone 下），避免半夜推送
        "greet_hour_end": 12,
        "custom": [],                 # 自定义纪念日 [{date,label,yearly,message_hint}]
        # 姨妈周期预测
        "cycle_length_days": 0,       # 周期天数（如 28）；0=不预测
        "last_period_start": "",      # 上次姨妈开始日 YYYY-MM-DD
    },
    "privacy": {
        # 对话原文存储级别（开源默认 none：Runtime 只需要事实，不需要原文）
        # none = 只存时间/角色/类型，payload.text 一律丢弃
        # excerpt = 存前 200 字（有主机、自己信得过自己盘的人用）
        # full = 全存（调试用）
        "store_message_text": "none",
    },
    "retention": {
        # 已解决的情绪要放下：resolved episode 过此天数后只留一句摘要，
        # 其关联事件原文删除。未解决的 episode 永不清理。
        "resolved_detail_days": 7,
        # 与未解决 episode 无关的普通事件原文，过此天数删除
        "events_days": 14,
        # Explain Log 最多保留条数（SEND 记录不受此限）
        "decisions_keep": 500,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg = _merge(cfg, yaml.safe_load(f) or {})
    # 初见卡片写的 profile.yaml（与 config.yaml 同目录）覆盖在最上层，
    # 让用户填的资料不必手改 config.yaml，也便于表单热更新。
    if path:
        prof = os.path.join(os.path.dirname(path) or ".", "profile.yaml")
        if os.path.exists(prof):
            with open(prof, "r", encoding="utf-8") as f:
                cfg = _merge(cfg, yaml.safe_load(f) or {})
        # persona.yaml 只进独立命名空间（不与运行配置合并），供推送标题等取名。
        pers = os.path.join(os.path.dirname(path) or ".", "persona.yaml")
        if os.path.exists(pers):
            with open(pers, "r", encoding="utf-8") as f:
                cfg["_persona"] = yaml.safe_load(f) or {}
    # 环境变量覆盖监听地址（Docker 开箱即用用这个，见 docker-compose.yml）：
    # 容器内网络需要 0.0.0.0，但不想让用户为了跑 Docker 去手改 config.yaml
    # 里默认更安全的 127.0.0.1，所以由 compose 注入 RR_LISTEN 环境变量。
    if os.environ.get("RR_LISTEN"):
        cfg.setdefault("mcp", {})["listen"] = os.environ["RR_LISTEN"]
    return cfg


def companion_name(cfg: dict) -> str:
    """推送标题用的 AI 名字：persona.yaml 的 companion.name，缺省 'AI'。"""
    return ((cfg.get("_persona") or {}).get("companion") or {}).get("name") or "AI"
