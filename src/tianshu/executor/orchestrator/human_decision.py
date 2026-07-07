"""HumanDecision —— L3 审批的结构化结果。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from tianshu.models.acceptance import AcceptanceCriteria


class HumanDecision(BaseModel):
    action: Literal["continue", "accept_as_is", "abort", "modify_acceptance"]
    feedback: str | None = None
    new_acceptance: AcceptanceCriteria | None = None
