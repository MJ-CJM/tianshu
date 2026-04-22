"""加密凭证子系统（藏兵阁后端）。Spec Section 4。"""

from tianshu.secrets.injector import (
    CredentialConflict,
    CredentialInjector,
    ForbiddenHeader,
    FORBIDDEN_USER_HEADERS,
    InjectionResult,
    redact_sensitive_headers,
)
from tianshu.secrets.models import (
    Credential,
    CredentialCreate,
    CredentialUpdate,
    CredentialView,
)
from tianshu.secrets.store import CredentialStore, resolve_provider_key
from tianshu.secrets.vault import SecretVault, get_vault

__all__ = [
    "Credential",
    "CredentialCreate",
    "CredentialUpdate",
    "CredentialView",
    "CredentialStore",
    "resolve_provider_key",
    "SecretVault",
    "get_vault",
    "CredentialConflict",
    "CredentialInjector",
    "ForbiddenHeader",
    "FORBIDDEN_USER_HEADERS",
    "InjectionResult",
    "redact_sensitive_headers",
]
