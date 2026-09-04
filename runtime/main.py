"""进程入口：MCP server + 决策调度器，单进程 asyncio。"""
import asyncio
import os
import sys

import uvicorn

from .config import load_config
from .mcp_server import build_app
from .policy.default import DefaultPolicy
from .providers.composer.template import TemplateComposer
from .providers.notify.bark import BarkNotifier
from .providers.notify.ntfy import NtfyNotifier
from .providers.notify.log import LogNotifier
from .scheduler import Scheduler
from .store import Store


def build_providers(cfg: dict):
    tz = cfg["runtime"]["display_timezone"]
    data_dir = os.path.dirname(os.path.abspath(cfg["runtime"]["db_path"]))
    notif_kind = cfg["providers"]["notification"]
    if cfg["runtime"]["mode"] == "shadow" or notif_kind == "log":
        if cfg["runtime"]["mode"] != "shadow":
            # 默认 live 但推送渠道还是 log：消息只会落在本地文件里，
            # 到不了手机。说清楚，别让用户以为"开箱即真发"坏了。
            print("[relationship-runtime] 提示：live 模式但 "
                  "providers.notification 还是 log，消息只写入 "
                  "data/shadow_outbox.log；改成 bark/ntfy 才会真的推到手机"
                  "（见 docs/push-channels.md）")
        notifier = LogNotifier(os.path.join(data_dir, "shadow_outbox.log"), tz)
    elif notif_kind == "bark":
        notifier = BarkNotifier(cfg["providers"]["bark"]["url"])
    elif notif_kind == "ntfy":
        n = cfg["providers"].get("ntfy", {})
        notifier = NtfyNotifier(n.get("url", "https://ntfy.sh"),
                                n["topic"], n.get("token", ""))
    else:
        raise SystemExit(f"unknown notification provider: {notif_kind}")
    composer = TemplateComposer()  # live 模式后续接 claude_headless
    policy = DefaultPolicy()
    return policy, composer, notifier


async def amain():
    cfg = load_config(os.environ.get("RR_CONFIG", "config.yaml"))
    if not cfg["mcp"].get("token"):
        print("ERROR: mcp.token 未配置，先跑 scripts/init.sh", file=sys.stderr)
        raise SystemExit(1)
    store = Store(cfg["runtime"]["db_path"])
    policy, composer, notifier = build_providers(cfg)
    scheduler = Scheduler(store, cfg, policy, composer, notifier)
    app = build_app(store, cfg, scheduler, policy, composer, notifier)

    host, port = cfg["mcp"]["listen"].rsplit(":", 1)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=int(port),
                                           log_level="info"))
    print(f"[relationship-runtime] mode={cfg['runtime']['mode']} "
          f"listening on {host}:{port}")
    await asyncio.gather(server.serve(), scheduler.run())


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
