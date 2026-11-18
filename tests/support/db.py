from contextlib import contextmanager
from unittest.mock import MagicMock, patch


@contextmanager
def fake_connect(connect_path: str, *, fetchone=None, fetchall=None, rowcount=1):
    """Patch the `connect` a DB module looks up (`connect_path`) so its queries run against a mock cursor, which is yielded."""
    cur = MagicMock(fetchone=MagicMock(return_value=fetchone), fetchall=MagicMock(return_value=fetchall or []), rowcount=rowcount)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    with patch(connect_path, autospec=True) as connect:
        connect.return_value.__enter__.return_value = conn
        yield cur
