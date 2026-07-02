"""导出全部 API 路由清单（method + path），用于拆分 gateway/api.py 时前后对比防 URL 漂移。

用法：.venv/bin/python scripts/dump_routes.py > /tmp/routes.txt
"""

from __future__ import annotations

from tianshu.app import create_app


def main() -> None:
    app = create_app()
    lines = []
    for r in app.routes:
        path = getattr(r, "path", None)
        if path is None:
            continue
        methods = getattr(r, "methods", None)
        if methods:
            for m in sorted(methods - {"HEAD", "OPTIONS"}):
                lines.append(f"{m} {path}")
        else:  # WebSocket / Mount
            lines.append(f"WS/MOUNT {path}")
    for line in sorted(lines):
        print(line)


if __name__ == "__main__":
    main()
