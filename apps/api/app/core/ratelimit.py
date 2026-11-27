import time
import uuid

from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.errors import ApiError

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


def set_redis(client: Redis | None) -> None:
    """Tests swap in fakeredis."""
    global _redis
    _redis = client


def parse_rule(rule: str) -> tuple[int, int]:
    count, seconds = rule.split("/")
    return int(count), int(seconds)


async def allow(key: str, rule: str) -> bool:
    """Counts one more event in the window and says whether it is still within the limit (no exception)."""
    limit, window = parse_rule(rule)
    r = get_redis()
    now = time.time()
    k = f"rl:{key}"
    async with r.pipeline(transaction=True) as p:
        p.zremrangebyscore(k, 0, now - window)
        p.zadd(k, {f"{now}:{uuid.uuid4().hex}": now})
        p.zcard(k)
        p.expire(k, window)
        _, _, count, _ = await p.execute()
    return count <= limit


async def hit(key: str, rule: str) -> None:
    """Sliding-window limiter on a Redis sorted set. Raises 429 when the window is full."""
    limit, window = parse_rule(rule)
    r = get_redis()
    now = time.time()
    k = f"rl:{key}"
    async with r.pipeline(transaction=True) as p:
        p.zremrangebyscore(k, 0, now - window)
        p.zadd(k, {f"{now}:{uuid.uuid4().hex}": now})
        p.zcard(k)
        p.expire(k, window)
        _, _, count, _ = await p.execute()
    if count > limit:
        oldest = await r.zrange(k, 0, 0, withscores=True)
        retry = int(window - (now - oldest[0][1])) + 1 if oldest else window
        raise ApiError(429, "rate_limited", "Too many attempts. Try again shortly.", retry_after=retry)


async def login_locked(email: str) -> int:
    """Seconds left on a lockout after repeated failed logins, or 0."""
    ttl = await get_redis().ttl(f"lock:login:{email}")
    return max(ttl, 0)


async def record_login_failure(email: str) -> None:
    s = get_settings()
    r = get_redis()
    k = f"fail:login:{email}"
    n = await r.incr(k)
    if n == 1:
        await r.expire(k, s.login_lockout_seconds)
    if n >= s.login_max_failures:
        await r.set(f"lock:login:{email}", 1, ex=s.login_lockout_seconds)
        await r.delete(k)


async def clear_login_failures(email: str) -> None:
    await get_redis().delete(f"fail:login:{email}")
