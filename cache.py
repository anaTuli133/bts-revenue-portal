"""
Thread-safe in-memory TTL cache.

Flask's dev server (and most simple WSGI deployments of this app) run as a
single process with a thread pool, so a process-local dict guarded by a lock
is enough — no Redis/memcached needed for this workload. If this app is ever
run behind multiple worker processes (gunicorn -w N > 1), this cache would
need to move to a shared store (Redis) since each process would otherwise
keep its own copy; noted here for future reference.

Cache keys are plain tuples built by the caller from EVERY parameter that
affects the query result (category/table selector, month, division,
district, etc.) — this module does not know or care what the key means, it
just stores/expires whatever it's given.
"""
import threading
import time

_lock = threading.Lock()
_store = {}  # key -> (expires_at_epoch_seconds, value)

# Revenue data changes at most daily (ETL loads), so a moderate TTL is safe
# and keeps repeated category switches fast without serving stale-forever data.
TTL_REVENUE_DATA = 15 * 60      # 15 minutes — pivot/dashboard query results
TTL_REFERENCE_DATA = 60 * 60    # 60 minutes — month list / division-district filter options


def cache_get(key):
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            del _store[key]
            return None
        return value


def cache_set(key, value, ttl):
    with _lock:
        _store[key] = (time.time() + ttl, value)
    return value


def cache_stats():
    """Lightweight introspection, handy for debugging via a shell/console."""
    with _lock:
        now = time.time()
        live = sum(1 for expires_at, _ in _store.values() if expires_at > now)
        return {'total_entries': len(_store), 'live_entries': live}


def cache_clear():
    with _lock:
        _store.clear()
