"""加密凭证子系统（藏兵阁后端）。Spec Section 4。"""

from tianshu.secrets.vault import SecretVault, get_vault

__all__ = ["SecretVault", "get_vault"]
