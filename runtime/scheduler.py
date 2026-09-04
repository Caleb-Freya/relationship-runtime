"""Event-driven + one-shot wake 调度。

事件到达 → poke() → 立即重算；否则睡到 next_wake_at。
timer 是派生状态：next_wake_at 持久化，重启后重算重挂。
watchdog 由主循环的 6h 兜底上限承担；调度器自身永不直接发消息。
"""
import asyncio
import logging
import time

from . import decide
from .store import Store, epoch_to_iso

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, store: Store, cfg: dict, policy, composer, notifier):
        self.store = store
        self.cfg = cfg
        self.policy = policy
        self.composer = composer
        self.notifier = notifier
        self._poke = asyncio.Event()

    def poke(self):
        self._poke.set()

    async def run(self):
        while True:
            now = time.time()
            last_compact = float(self.store.get_state("last_compact") or 0)
            if now - last_compact > 86400:
                self.store.compact(self.cfg, now)
                self.store.set_state("last_compact", str(now))
            try:
                result = decide.run_decision(
                    self.store, self.cfg, self.policy, self.composer,
                    self.notifier, now=now)
            except Exception:
                # 决策环本身不该有整体崩溃的空间，但配置/policy 插件的意外
                # 抛错不能拖死调度循环——记日志，退避一段时间后重算，
                # 不让一次坏数据/坏配置把主动联系永久停摆。
                logger.exception("run_decision 本轮异常，跳过并 60s 后重试")
                self._poke.clear()
                try:
                    await asyncio.wait_for(self._poke.wait(), timeout=60.0)
                except asyncio.TimeoutError:
                    pass
                self._poke.clear()
                continue
            wake_at = decide.next_wake_at(self.store, self.cfg, result, now)
            self.store.set_state("next_wake_at", epoch_to_iso(wake_at))
            timeout = max(30.0, wake_at - time.time())
            try:
                await asyncio.wait_for(self._poke.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            self._poke.clear()
