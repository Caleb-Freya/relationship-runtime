"""四个 Provider Contract（V1 冻结面，见 docs/contract.md §6）。

全部要求：失败抛出 ProviderError；Runtime 捕获后降级继续跑，永不整体崩溃。
"""


class ProviderError(Exception):
    pass


class MemoryProvider:            # 可选
    contract_version = "1"

    def search(self, query: str) -> list:
        return []

    def write(self, entry: dict) -> bool:
        return False

    def recent(self, n: int) -> list:
        return []


class MindProvider:              # 可选
    contract_version = "1"

    def get_state(self) -> dict:
        return {}

    def recent_thoughts(self, n: int) -> list:
        return []

    def submit_event(self, event: dict) -> bool:
        return False

    def settle(self) -> bool:
        return False


class NotificationProvider:      # 必需
    contract_version = "1"

    def send(self, title: str, body: str, level: str = "normal") -> dict:
        raise NotImplementedError


class ComposerProvider:          # 必需
    contract_version = "1"

    def compose(self, context: dict, reason: str, attempt_count: int) -> str:
        raise NotImplementedError
