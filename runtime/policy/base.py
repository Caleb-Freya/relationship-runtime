"""Policy 插件接口：urge/restraint 打分。

Runtime 拥有决策环与事实；打分是可替换的人格模块。
默认实现只依赖 Runtime 自身事实；Mind Provider 存在时可注入或整体接管。
"""
from dataclasses import dataclass, field


@dataclass
class Facts:
    """由 decide.gather_facts 汇总的、全部来自 Runtime 存储的事实。"""
    now: float
    hours_since_user_message: float | None
    active_episodes: list          # sqlite Rows: episodes
    unanswered_attempts: list      # sqlite Rows: contact_attempts（自上次用户消息后）
    departed: bool
    health: dict = field(default_factory=dict)  # 手环最新快照：hr/stress/spo2/sleep/updated_at
    extra: dict = field(default_factory=dict)


@dataclass
class Scores:
    urge: dict[str, float]         # 来源分项: missing_you / conflict_repair / ...
    urge_total: float
    restraint_raw: float
    restraint_effective: float
    notes: list[str] = field(default_factory=list)


class Policy:
    contract_version = "1"

    def score(self, facts: Facts, cfg: dict) -> Scores:
        raise NotImplementedError
