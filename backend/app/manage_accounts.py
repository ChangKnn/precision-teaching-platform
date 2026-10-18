"""Local-only admin bootstrap: no public registration or hard-coded passwords."""
import argparse
from pathlib import Path
import secrets
from uuid import uuid4

from .database import database, initialize_database, utc_now
from .teacher_auth import hash_password, normalize_username


def bootstrap_admin(username: str, output: Path) -> None:
    username = normalize_username(username)
    initialize_database()
    password = secrets.token_urlsafe(24)
    encoded = hash_password(password)
    # Exclusive creation avoids overwriting either an account or its credential file.
    handle = output.open('x', encoding='utf-8')
    try:
        output.chmod(0o600)
        with database() as connection:
            if connection.execute("SELECT 1 FROM user_accounts WHERE role = 'admin'").fetchone():
                raise RuntimeError('管理员已存在；不允许再次初始化或覆盖')
            now = utc_now()
            connection.execute("""INSERT INTO user_accounts (id, username, role, password_hash, created_at, updated_at)
                                  VALUES (?, ?, 'admin', ?, ?, ?)""", ('acct-' + uuid4().hex, username, encoded, now, now))
            handle.write(f'管理员入口：https://precisionteach.online/admin/\n账号：{username}\n临时密码：{password}\n首次登录必须修改密码。此文件请妥善保管，不要提交至 Git。\n')
            handle.flush()
    except Exception:
        handle.close()
        output.unlink(missing_ok=True)
        raise
    finally:
        handle.close()
    print(f'管理员已创建。临时凭据保存在 {output}，密码不会输出到日志。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='初始化唯一的管理员账号（仅服务器本机）')
    parser.add_argument('--username', default='admin')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    bootstrap_admin(args.username, args.output)
