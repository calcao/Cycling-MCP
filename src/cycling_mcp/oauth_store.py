"""加密持久化 OAuth 客户端、临时授权与令牌族。"""

from contextlib import contextmanager
import hashlib
import json
import sqlite3
import time

from cryptography.fernet import Fernet, InvalidToken


class OAuthStore:
    """使用哈希索引及加密载荷保存 OAuth 状态，事务内完成一次性消费。"""

    def __init__(self, path, key):
        self.path = path
        self.cipher = Fernet(key.encode())
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS oauth_records (
                kind TEXT NOT NULL, id_hash TEXT NOT NULL, payload BLOB NOT NULL,
                expires_at INTEGER, family TEXT, PRIMARY KEY(kind,id_hash))""")
            db.execute("CREATE INDEX IF NOT EXISTS oauth_family ON oauth_records(family)")
            db.execute("CREATE INDEX IF NOT EXISTS oauth_expiry ON oauth_records(expires_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS oauth_limits (
                id_hash TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at INTEGER NOT NULL)""")
        path.chmod(0o600)

    @contextmanager
    def transaction(self):
        """串行化授权码消费、刷新令牌轮换和并发限流计数。"""
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    @staticmethod
    def _index(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def get(self, db, kind, identifier, *, consume=False):
        """读取未过期载荷，可在同一事务中消费记录。"""
        row = db.execute("SELECT * FROM oauth_records WHERE kind=? AND id_hash=?",
                         (kind, self._index(identifier))).fetchone()
        if not row or (row["expires_at"] is not None and row["expires_at"] <= time.time()):
            return None
        try:
            result = json.loads(self.cipher.decrypt(row["payload"]))
        except InvalidToken as exc:
            raise RuntimeError("OAuth storage key does not match persisted data") from exc
        if consume:
            db.execute("DELETE FROM oauth_records WHERE kind=? AND id_hash=?", (kind, self._index(identifier)))
        return result

    def put(self, db, kind, identifier, payload, expires_at=None, family=None):
        """加密保存载荷，索引中不包含原始令牌或授权码。"""
        encrypted = self.cipher.encrypt(json.dumps(payload, ensure_ascii=False).encode())
        db.execute("INSERT OR REPLACE INTO oauth_records VALUES (?,?,?,?,?)",
                   (kind, self._index(identifier), encrypted, expires_at, family))

    def revoke_family(self, db, family):
        """同时撤销同一授权的访问令牌及刷新令牌。"""
        db.execute("DELETE FROM oauth_records WHERE family=?", (family,))

    def allow(self, key, limit, period):
        """持久化固定窗口限流，服务重启不会清空计数。"""
        now = int(time.time())
        identifier = self._index(key)
        with self.transaction() as db:
            db.execute("DELETE FROM oauth_limits WHERE expires_at<=?", (now,))
            db.execute("DELETE FROM oauth_records WHERE expires_at<=?", (now,))
            row = db.execute("SELECT count FROM oauth_limits WHERE id_hash=?", (identifier,)).fetchone()
            if row and row["count"] >= limit:
                return False
            db.execute("""INSERT INTO oauth_limits VALUES (?,1,?)
                ON CONFLICT(id_hash) DO UPDATE SET count=count+1""", (identifier, now + period))
        return True
