"""Memory Palace configuration with ablation switches."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryConfig:
    """Each flag can be toggled independently for ablation experiments."""

    enabled: bool = True  # Master switch
    l1_enabled: bool = True  # L1 critical facts injection
    l2_recall_enabled: bool = True  # L2 pre-execution recall
    reflect_enabled: bool = True  # Periodic reflection
    tunnels_enabled: bool = True  # Cross-wing tunnels
    emperor_wing_enabled: bool = True  # User's wing
    verbatim_mode: bool = True  # True=store raw, False=store summary

    # Tuning
    l1_max_chars: int = 3200  # L1 token budget (~800 tokens)
    l1_top_k: int = 15  # Number of top drawers for L1
    l2_n_results: int = 10  # Search results for L2 recall
    chunk_max_chars: int = 800  # Drawer chunk size
    chunk_min_chars: int = 10  # Minimum chunk to keep
    recency_half_life_days: int = 30  # Recency decay half-life
