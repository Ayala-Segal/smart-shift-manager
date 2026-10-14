import threading
import weakref

from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
_schema_ready = weakref.WeakKeyDictionary()
_schema_locks = weakref.WeakKeyDictionary()


def run_once(flag, setup):
    """Run schema setup once per database engine.

    Repeating it on every request against SQL Server holds locks and turns
    the next page into Flask's generic internal-error page.
    """
    engine = db.engine
    ready = _schema_ready.get(engine)
    if ready is not None and flag in ready:
        return
    lock = _schema_locks.setdefault(engine, threading.Lock())
    with lock:
        ready = _schema_ready.get(engine)
        if ready is None:
            ready = set()
            _schema_ready[engine] = ready
        if flag in ready:
            return
        db.session.rollback()
        try:
            setup()
        except Exception:
            db.session.rollback()
            raise
        ready.add(flag)
