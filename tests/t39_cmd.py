#!/usr/bin/env python3
"""T39 辅助：在服务端进程外直接调 process_command 验证企微指令管道。
用法: python3 tests/t39_cmd.py "<指令文本>" <sender_userid>
输出: 指令返回的回复文本（stdout 第一行起）
"""
import sys
import os

# 注入部署环境变量（DATABASE_URL 在 /opt/license/env.file，服务与直连同源）
_env_file = "/opt/license/env.file"
if os.path.exists(_env_file):
    with open(_env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.database import SessionLocal  # noqa: E402
from app.wecom import process_command  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: t39_cmd.py <text> <sender>", file=sys.stderr)
        return 2
    text, sender = sys.argv[1], sys.argv[2]
    db = SessionLocal()
    try:
        print(process_command(db, text, sender))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
