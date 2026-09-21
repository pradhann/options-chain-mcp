"""HTTP fetch with a dated raw snapshot of every body, and offline mode.

Every successful GET writes the body to
`.optionslab/snapshots/raw/{host}/{YYYY-MM-DD}/{urlhash}.body` with a meta
file (url, fetched_at, status). That makes every public-endpoint feed
replayable offline: with OPTIONSLAB_OFFLINE=1 the latest body on or before
OPTIONSLAB_ASOF is returned and the network is never touched.

Query parameters that carry secrets (api_key, token) are stripped before
hashing and before the URL is written anywhere.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime

from ..storage import snapshots as snap
from .envelope import ET, iso, now_utc

SECRET_PARAMS = ("api_key", "apikey", "token", "key")
DEFAULT_UA = "optionslab/0.2 (options research toolkit; python-urllib)"

# Minimum seconds between requests per host. SEC asks for <= 10 req/s.
_MIN_INTERVAL = {"sec.gov": 0.12, "data.sec.gov": 0.12, "efts.sec.gov": 0.12,
                 "www.sec.gov": 0.12}
_last_call: dict[str, float] = {}
_lock = threading.Lock()


class FetchError(RuntimeError):
    """Network fetch failed and no snapshot could stand in."""


@dataclass(frozen=True)
class Fetched:
    url: str                 # public URL (secrets stripped)
    body: bytes
    fetched_at: str          # ISO timestamp of the original network read
    snapshot_date: date
    from_snapshot: bool      # True when served from disk, not the network
    note: str | None = None  # e.g. why a snapshot stood in for the network

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body)


def public_url(url: str) -> str:
    """URL with secret query params removed."""
    parts = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
         if k.lower() not in SECRET_PARAMS]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q, safe="[]$(),'=:*")))


def _key(url: str) -> str:
    return hashlib.sha1(public_url(url).encode()).hexdigest()[:20]


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def _raw_dir(host: str, day: date):
    return snap.home() / snap.SNAPSHOT_DIR / "raw" / snap.safe(host) / day.isoformat()


def _read_snapshot(url: str, on_or_before: date | None) -> Fetched | None:
    host, k = _host(url), _key(url)
    base = snap.home() / snap.SNAPSHOT_DIR / "raw" / snap.safe(host)
    if not base.is_dir():
        return None
    limit = on_or_before or snap.asof_date()
    days = []
    for p in base.iterdir():
        try:
            days.append(date.fromisoformat(p.name))
        except ValueError:
            continue
    for d in sorted(days, reverse=True):
        if d > limit:
            continue
        body = base / d.isoformat() / f"{k}.body"
        if body.exists():
            meta_p = body.with_suffix(".meta.json")
            meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
            return Fetched(url=public_url(url), body=body.read_bytes(),
                           fetched_at=meta.get("fetched_at", d.isoformat()),
                           snapshot_date=d, from_snapshot=True)
    return None


def record(url: str, body: bytes, *, status: int = 200,
           day: date | None = None) -> Fetched:
    """Store `body` as the snapshot for `url` (network reads and test fixtures)."""
    day = day or datetime.now(ET).date()
    d = _raw_dir(_host(url), day)
    d.mkdir(parents=True, exist_ok=True)
    k = _key(url)
    fetched_at = iso(now_utc())
    (d / f"{k}.body").write_bytes(body)
    (d / f"{k}.meta.json").write_text(json.dumps(
        {"url": public_url(url), "fetched_at": fetched_at, "status": status}))
    return Fetched(url=public_url(url), body=body, fetched_at=fetched_at,
                   snapshot_date=day, from_snapshot=False)


def _throttle(host: str) -> None:
    gap = next((v for h, v in _MIN_INTERVAL.items() if host.endswith(h)), 0.0)
    if not gap:
        return
    with _lock:
        wait = _last_call.get(host, 0.0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()


def user_agent_for(url: str) -> str:
    """SEC requires a contact in the UA; take it from OPTIONSLAB_SEC_UA."""
    if _host(url).endswith("sec.gov"):
        return os.environ.get("OPTIONSLAB_SEC_UA") or DEFAULT_UA
    return os.environ.get("OPTIONSLAB_UA") or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _recent_snapshot(url: str, max_age_hours: float) -> Fetched | None:
    """Today's snapshot of `url` if it was fetched within `max_age_hours`."""
    hit = _read_snapshot(url, snap.today_et())
    if hit is None:
        return None
    try:
        age = (now_utc() - datetime.fromisoformat(hit.fetched_at)).total_seconds()
    except ValueError:
        return None
    return hit if age <= max_age_hours * 3600 else None


def get(url: str, *, max_age_hours: float | None = None,
        headers: dict | None = None, timeout: float = 30.0,
        fallback_to_snapshot: bool = True) -> Fetched:
    """GET `url`, snapshotting the body. Offline mode reads snapshots only.

    `max_age_hours`: reuse today's-or-recent snapshot younger than this
    instead of hitting the network (a cache, not a substitute for data).
    `fallback_to_snapshot`: on a network error, serve the latest snapshot
    and say so in `note`; the caller must downgrade quality to "snapshot".
    """
    if snap.is_offline():
        hit = _read_snapshot(url, None)
        if hit is None:
            raise snap.OfflineMiss(f"offline and no snapshot for {public_url(url)}")
        return hit

    if max_age_hours is not None:
        recent = _recent_snapshot(url, max_age_hours)
        if recent is not None:
            return recent

    host = _host(url)
    _throttle(host)
    h = {"User-Agent": user_agent_for(url), "Accept": "*/*",
         "Accept-Encoding": "gzip"}
    h.update(headers or {})
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return record(url, body, status=r.status)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        if fallback_to_snapshot:
            hit = _read_snapshot(url, None)
            if hit is not None:
                return Fetched(url=hit.url, body=hit.body, fetched_at=hit.fetched_at,
                               snapshot_date=hit.snapshot_date, from_snapshot=True,
                               note=f"network error ({e}); served snapshot of "
                                    f"{hit.snapshot_date}")
        raise FetchError(f"{public_url(url)}: {e}") from e
