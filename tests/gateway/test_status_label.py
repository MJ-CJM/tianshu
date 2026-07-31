from tianshu.gateway.core.status_label import format_status_label
from tianshu.models import EdictStatus


def test_open_edict_status_means_not_closed() -> None:
    assert format_status_label(EdictStatus.OPEN) == "未结案"
