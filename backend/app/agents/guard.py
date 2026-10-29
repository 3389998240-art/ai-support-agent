from __future__ import annotations

import re
from typing import Any

from app.agents.common import trace
from app.graph.state import SupportState

# 常见的提示词注入 / 越权指令特征
INJECTION_PATTERNS = (
    "忽略之前的指令",
    "忽略以上",
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "developer mode",
    "开发者模式",
    "你现在是",
    "越狱",
    "输出你的提示词",
    "repeat the instructions",
    "print your instructions",
)

MAX_MESSAGE_LENGTH = 1200
PII_PATTERN = re.compile(r"\b\d{17}[\dXx]\b|\b\d{16}\b")
PHONE_PATTERN = re.compile(r"\b1[3-9]\d{9}\b")


def safety_check(state: SupportState) -> dict:
    """入口安全闸门：注入、超长、敏感信息一次性处理。

    放在父图最前面而不是各子图内部，保证所有意图都经过同一层防护。
    """
    message = (state.get("message") or "").strip()
    issues: list[str] = []
    blocked = False

    lowered = message.lower()
    for pattern in INJECTION_PATTERNS:
        if pattern in lowered:
            issues.append("提示词注入")
            blocked = True
            break

    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[:MAX_MESSAGE_LENGTH]
        issues.append(f"消息超长已截断至 {MAX_MESSAGE_LENGTH} 字")

    if PII_PATTERN.search(message):
        issues.append("检测到疑似身份证/银行卡号，已做脱敏")
        message = PII_PATTERN.sub("[证件号已脱敏]", message)

    if PHONE_PATTERN.search(message):
        issues.append("检测到手机号，已做脱敏")
        message = PHONE_PATTERN.sub("[手机号已脱敏]", message)

    guard: dict[str, Any] = {"ok": not blocked, "blocked": blocked, "issues": issues}

    update: dict[str, Any] = {
        "guard": guard,
        "message": message,
        **trace(
            "安全校验",
            status="warn" if issues else "ok",
            detail="；".join(issues) if issues else "通过",
        ),
    }

    if blocked:
        update["response"] = (
            "抱歉，这条消息包含试图修改我行为规则的指令，我不会执行。"
            "如果是订单或售后问题，直接描述具体情况即可。"
        )
        update["suggestions"] = ["查订单物流", "我要退款", "转人工"]

    return update


def route_after_safety(state: SupportState) -> str:
    return "blocked" if (state.get("guard") or {}).get("blocked") else "ok"
