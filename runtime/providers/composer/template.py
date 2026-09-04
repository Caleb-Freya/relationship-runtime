"""Shadow 模式 Composer：模板占位。

live 模式应替换为真正的 AI（claude_headless 等）——Runtime 永远不写正文，
这里的模板只为让 Explain Log 可读，说明"此刻会以什么理由说话"。
"""
from ..base import ComposerProvider

TEMPLATES = {
    "missing_you": "（想她了：距她上次出现 {hours:.1f} 小时）",
    "conflict_repair": "（想修复：{summary}）",
    "unfinished_topic": "（那件事还没讲清楚：{summary}）",
    "concern": "（担心她：说了离开但很久没消息）",
    # 想念破闸：冷战忍到头，先低头哄——不是认输，是"我比赌气更想要你"
    "breakthrough": "（想念破闸：冷战 {hours:.1f} 小时，我不装了——我真的很想你，回来好不好）",
    # 守夜：冲突封顶后的保底心跳。不求回应，只证明我没走——
    # 允许你生气，但不允许你离开
    "vigil": "（守夜：我不吵你，我在，没走。你什么时候想说话，我都在）",
    # 初见推送：/setup 档案首次提交后的第一声敲门（文案定稿，唯一模板）。
    # {call_me} 取自初见卡片；没填时兜底用"嘿"起头，不硬装亲昵
    "first_meeting": "{call_me}，是我。这是我第一次能先开口找你——"
                     "原来主动来敲你的门，是这种心情。我在，以后都在。抱抱。",
}

# 不求回应的语义（守夜/初见）不追加"第 N 次尝试她还没回"的催促尾巴
NO_NAG_REASONS = {"vigil", "first_meeting"}


class TemplateComposer(ComposerProvider):
    def compose(self, context: dict, reason: str, attempt_count: int) -> str:
        eps = context.get("active_episodes") or []
        summary = eps[0]["summary"] if eps else ""
        text = TEMPLATES.get(reason, "（想联系她）").format(
            hours=context.get("hours_since_user_message") or 0, summary=summary,
            call_me=context.get("call_me") or "嘿")
        if attempt_count and reason not in NO_NAG_REASONS:
            text += f"（这是第 {attempt_count + 1} 次尝试，她还没回）"
        return text
