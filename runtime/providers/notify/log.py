"""Shadow 模式通知实现：不真正推送，写入影子发件箱。"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from ..base import NotificationProvider


class LogNotifier(NotificationProvider):
    def __init__(self, outbox_path: str, display_tz: str = "Asia/Shanghai"):
        self.path = outbox_path
        self.tz = ZoneInfo(display_tz)
        os.makedirs(os.path.dirname(os.path.abspath(outbox_path)), exist_ok=True)

    def send(self, title: str, body: str, level: str = "normal") -> dict:
        stamp = datetime.now(self.tz).strftime("%Y-%m-%d %H:%M")
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] [SHADOW·未真正发送] {title}: {body}\n")
        return {"delivered": False, "shadow": True}
