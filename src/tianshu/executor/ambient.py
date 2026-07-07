"""兼容 shim——已迁移至 tianshu.kernel.ambient，本文件将在下一版本移除。"""

from tianshu.kernel.ambient import (  # noqa: F401
    bind_edict,
    bind_persona,
    get_current_edict,
    get_current_persona,
)
