"""ntfy 推送（安卓/桌面/自托管均可）。与 Bark 平级的 NotificationProvider。

ntfy 是开源推送服务：手机装 ntfy App 订阅一个 topic，服务端 POST 即达。
- 公共服务 https://ntfy.sh 免费可用（topic 名即密码，务必取长随机名）
- 也可完全自托管（docker 一行），零第三方
用 JSON 发布模式：中文标题/正文无编码问题。
"""
import json
import urllib.request

from ..base import NotificationProvider, ProviderError

_PRIORITY = {"normal": 3, "active": 4, "timeSensitive": 4, "critical": 5}


class NtfyNotifier(NotificationProvider):
    def __init__(self, url: str, topic: str, token: str = "",
                 timeout: float = 10.0):
        self.url = (url or "https://ntfy.sh").rstrip("/")
        self.topic = topic
        self.token = token
        self.timeout = timeout

    def send(self, title: str, body: str, level: str = "normal") -> dict:
        payload = {
            "topic": self.topic,
            "title": title,
            "message": body,
            "priority": _PRIORITY.get(level, 3),
        }
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return {"delivered": resp.status == 200, "status": resp.status}
        except OSError as e:
            raise ProviderError(f"ntfy send failed: {e}") from e
