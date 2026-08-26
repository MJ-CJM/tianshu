"""Evolution candidate adapters."""

from tianshu.evolution.adapters.base import CandidateAdapter
from tianshu.evolution.adapters.code import CodeCandidateAdapter
from tianshu.evolution.adapters.executor import ExecutorCandidateAdapter
from tianshu.evolution.adapters.executor_promotion import ExecutorPromotionAdapter
from tianshu.evolution.adapters.memory import MemoryCandidateAdapter
from tianshu.evolution.adapters.persona import PersonaCandidateAdapter
from tianshu.evolution.adapters.policy import PolicyCandidateAdapter
from tianshu.evolution.adapters.skill import SkillCandidateAdapter

__all__ = [
    "CandidateAdapter",
    "CodeCandidateAdapter",
    "ExecutorCandidateAdapter",
    "ExecutorPromotionAdapter",
    "MemoryCandidateAdapter",
    "PersonaCandidateAdapter",
    "PolicyCandidateAdapter",
    "SkillCandidateAdapter",
]
