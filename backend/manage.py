#!/usr/bin/env python
"""Django 管理命令入口，例如 check、makemigrations 和运行开发服务器。"""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "未找到 Django。请先启用项目 .venv，并安装 requirements/dev.txt。"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
