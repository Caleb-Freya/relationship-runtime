"""Bark 推送（iOS）。Shadow 阶段不启用；live 模式经配置选择后才会实例化。"""
import urllib.parse
import urllib.request

from ..base import NotificationProvider, ProviderError


class BarkNotifier(NotificationProvider):
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def send(self, title: str, body: str, level: str = "normal") -> dict:
        target = f"{self.url}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"
        try:
            with urllib.request.urlopen(target, timeout=self.timeout) as resp:
                return {"delivered": resp.status == 200, "status": resp.status}
        except OSError as e:
            raise ProviderError(f"bark send failed: {e}") from e
