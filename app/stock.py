"""Optional stock B-roll search through the Pexels API.

``TECH_STACK.md`` names the Pexels video API as the one approved stock provider,
and ``FEATURES.md`` (Milestone 8) requires that it be **optional**: the app must
stay fully usable with no key, no network, and no online asset.

That shapes this module in three ways:

* **Search and preview first, download only on request.** :func:`search_videos`
  returns metadata only. A file enters the project only when the user picks one,
  through :func:`download_file`, which is the sole place a byte is fetched.
* **The key stays server-side.** Only the standard library is used, so enabling
  this adds no dependency weight, and the key is read from the environment and
  never reaches the browser.
* **Provenance is kept.** Every imported clip records its provider, page URL,
  photographer, and license so the interface can show the credit the provider's
  terms require.

Downloads are restricted to HTTPS URLs on the provider's own hosts, so a
hand-written request cannot turn the importer into a fetcher for an arbitrary
address.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .errors import ApiError

PROVIDER = "pexels"
PROVIDER_NAME = "Pexels"
PROVIDER_URL = "https://www.pexels.com"
DEFAULT_BASE_URL = "https://api.pexels.com"
#: The credit the provider's terms require wherever its media is shown.
ATTRIBUTION = "Media provided by Pexels"
LICENSE_NAME = "Pexels License"
LICENSE_URL = "https://www.pexels.com/license/"

REQUEST_TIMEOUT_SECONDS = 20
DOWNLOAD_TIMEOUT_SECONDS = 120
DOWNLOAD_CHUNK_SIZE = 256 * 1024

MAX_QUERY_LENGTH = 120
DEFAULT_PER_PAGE = 12
MAX_PER_PAGE = 24
ORIENTATIONS = ("all", "portrait", "landscape", "square")

#: Pexels serves previews and video files from its own domains plus a couple of
#: CDNs it has used for years. Anything else is refused before a request is made.
PEXELS_HOST_SUFFIX = ".pexels.com"
ALLOWED_CDN_HOSTS = frozenset(
    {
        "videos.pexels.com",
        "images.pexels.com",
        "vod-progressive.akamaized.net",
        "player.vimeo.com",
    }
)

#: Pexels photo renditions, best first. The original is last on purpose: it is
#: often several thousand pixels wide, which is wasted bandwidth for a 1080-wide
#: export that crops with ``cover`` anyway. ``large2x`` keeps the photo's own
#: orientation at about 1880 px, which matches what the renderer needs.
PHOTO_SIZE_ORDER = ("large2x", "large", "medium", "portrait", "landscape", "original")
#: The preview shown in the interface — small, and safe to load many of.
PHOTO_PREVIEW_SIZES = ("medium", "large", "small")

#: The long side of each scaled rendition, used when the URL does not state the
#: size itself. The *rendition* is what gets downloaded, so scoring a photo by the
#: size of its original would claim a resolution the file does not have.
PHOTO_RENDITION_LONG_SIDE = {
    "large2x": 1880,
    "large": 940,
    "medium": 750,
    "small": 640,
    "tiny": 280,
}
#: Renditions that are cropped to a fixed shape rather than scaled.
PHOTO_RENDITION_FIXED_SIZE = {"portrait": (800, 1200), "landscape": (1200, 800)}

USER_AGENT = "AI-Video-Production-Studio/0.3 (local)"

#: Words that carry no search meaning. Kept deliberately short: dropping too many
#: would throw away useful terms like "money" or "phone" in an insurance topic.
STOPWORDS = frozenset(
    """
    about after again against almost along already also although always among
    another any any anyone anything are around because been before being below
    between both cannot could does doing done down during each either enough
    even ever every everything from further got have having here however into
    itself just keep kind know last least less like made make many might more
    most much must need never only other others over really right said same
    should since some something still such than that their theirs them then
    there these they thing things this those through under until very want
    well were what when where which while will with without would your yours
    you're it's don't doesn't isn't wasn't aren't that's there's here's
    basically actually okay yeah know like going gonna wanna
    """.split()
)

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class PexelsSettings:
    """Server-side Pexels configuration. Absent key means the feature is off."""

    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: int = REQUEST_TIMEOUT_SECONDS

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and str(self.api_key).strip())


#: Transport is injectable so tests never touch the network.
Opener = Callable[[urllib.request.Request, int], tuple[int, bytes]]


def _urlopen(request: urllib.request.Request, timeout: int) -> tuple[int, bytes]:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return int(response.status), response.read()


def _urlopen_stream(request: urllib.request.Request, timeout: int):
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310


# ------------------------------------------------------------------- queries


def normalise_query(value: object) -> str:
    """Clean a search string: no control characters, no runaway length."""

    text = _CONTROL.sub(" ", str(value or ""))
    text = " ".join(text.split())
    return text[:MAX_QUERY_LENGTH]


def derive_keywords(text: str, limit: int = 6) -> list[str]:
    """The most useful search words in a piece of transcript.

    Deliberately simple and inspectable: longer words that are not common
    filler, ranked by how often they occur and then by where they first appear,
    so the phrasing of the moment decides the search rather than a hidden score.
    """

    words = [word.lower() for word in _WORD.findall(str(text or ""))]
    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}

    for position, word in enumerate(words):
        if len(word) < 4 or word in STOPWORDS:
            continue
        if word not in first_seen:
            first_seen[word] = position
        counts[word] += 1

    ranked = sorted(counts, key=lambda word: (-counts[word], first_seen[word]))
    return ranked[: max(1, limit)]


def clip_window(segments: Sequence[dict], clip_start: float, clip_end: float) -> str:
    """The transcript text spoken between two points of the source video."""

    pieces: list[str] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        try:
            start = float(segment.get("start") or 0.0)
            end = float(segment.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        # A segment counts when it overlaps the clip at all, which keeps a
        # sentence that begins just before the cut from being lost.
        if end <= clip_start or start >= clip_end:
            continue
        pieces.append(" ".join(str(segment.get("text") or "").split()))
    return " ".join(piece for piece in pieces if piece)


def suggest_placement(
    segments: Sequence[dict],
    clip_start: float,
    clip_end: float,
    *,
    duration: float = 3.5,
) -> dict[str, object]:
    """Suggest where a cutaway could sit, based on the transcript's own turns.

    This is a *draft*, not a decision: it prefers the first moment inside the clip
    where the speaker starts a new segment, because that is a natural place for
    the picture to change, and falls back to the clip's beginning when the
    transcript offers nothing better.
    """

    clip_duration = max(0.0, float(clip_end) - float(clip_start))
    if clip_duration <= 0:
        return {
            "start": 0.0,
            "end": 0.0,
            "duration": 0.0,
            "reason": "The clip has no length yet, so there is nowhere to place an insert.",
        }

    length = round(min(duration, clip_duration), 3)

    for segment in segments:
        if not isinstance(segment, dict):
            continue
        try:
            start = float(segment.get("start") or 0.0)
            end = float(segment.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        relative = round(start - float(clip_start), 3)
        # A boundary at the very start is not a "turn", and one too near the end
        # leaves no room for the insert.
        if relative <= 0.5 or start + length > float(clip_end) + 0.01:
            continue
        if end <= start:
            continue
        text = " ".join(str(segment.get("text") or "").split())[:70]
        reason = f"Starts at {relative:.1f}s, where the transcript begins a new point"
        reason += f': "{text}…".' if text else "."
        return {"start": relative, "end": round(relative + length, 3), "duration": length, "reason": reason}

    return {
        "start": 0.0,
        "end": length,
        "duration": length,
        "reason": (
            "No clear turn in the transcript was found, so the suggestion starts at "
            "the clip's beginning; move it wherever the picture should change."
        ),
    }


# ------------------------------------------------------------------- parsing


def _positive_int(value: object) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _positive_float(value: object) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return round(parsed, 3) if parsed > 0 else 0.0


def normalise_file(raw: dict) -> dict[str, object] | None:
    """One downloadable rendition of a stock clip, or None if unusable."""

    link = _CONTROL.sub("", str(raw.get("link") or "")).strip()
    if not link:
        return None
    return {
        "id": _positive_int(raw.get("id")),
        "quality": str(raw.get("quality") or "unknown").strip().lower(),
        "file_type": str(raw.get("file_type") or "").strip().lower(),
        "width": _positive_int(raw.get("width")),
        "height": _positive_int(raw.get("height")),
        "link": link,
    }


def choose_file(
    files: Sequence[dict], *, max_width: int = 1920
) -> dict[str, object] | None:
    """Pick the rendition to use: an MP4 that fits, preferring HD.

    A stock provider offers the same clip at several sizes. Downloading the
    largest wastes bandwidth and disk on a local-first app, so the closest size
    that still covers the 1080-wide export is preferred, and the smallest usable
    file is the fallback when every option is smaller than that.
    """

    mp4 = [item for item in files if str(item.get("file_type") or "").endswith("mp4")]
    usable = mp4 or list(files)
    if not usable:
        return None

    within = [item for item in usable if 0 < int(item.get("width") or 0) <= max_width]
    pool = within or usable
    # Prefer an explicitly HD rendition, then the largest size that still fits.
    pool = sorted(
        pool,
        key=lambda item: (
            0 if str(item.get("quality")) == "hd" else 1,
            -int(item.get("width") or 0),
        ),
    )
    return dict(pool[0])


def normalise_video(video: dict) -> dict[str, object] | None:
    """Turn one Pexels video record into the shape this app uses."""

    if not isinstance(video, dict):
        return None

    video_id = _positive_int(video.get("id"))
    if video_id == 0:
        return None

    files = [
        normalised
        for normalised in (
            normalise_file(raw)
            for raw in video.get("video_files") or []
            if isinstance(raw, dict)
        )
        if normalised
    ]
    best = choose_file(files)
    if best is None:
        # Without a usable file the result cannot become an asset, so it is not
        # offered at all rather than shown and then refused.
        return None

    user = video.get("user") if isinstance(video.get("user"), dict) else {}
    page_url = _CONTROL.sub("", str(video.get("url") or "")).strip()
    preview_url = _CONTROL.sub("", str(video.get("image") or "")).strip()
    photographer = " ".join(
        str(user.get("name") or video.get("photographer") or "").split()
    )[:80]
    photographer_url = _CONTROL.sub(
        "", str(user.get("url") or video.get("photographer_url") or "")
    ).strip()

    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "media_type": "video",
        "id": str(video_id),
        "width": _positive_int(video.get("width")),
        "height": _positive_int(video.get("height")),
        "duration": _positive_float(video.get("duration")),
        "page_url": page_url,
        "preview_url": preview_url,
        "photographer": photographer,
        "photographer_url": photographer_url,
        "attribution": ATTRIBUTION,
        "license": {"name": LICENSE_NAME, "url": LICENSE_URL},
        "files": files,
        "best_file": best,
    }


def parse_search_results(body: bytes) -> list[dict[str, object]]:
    """Parse a search response, dropping anything unusable instead of trusting it."""

    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, TypeError, AttributeError):
        raise ApiError(
            "stock_unavailable",
            "The stock provider returned a response that could not be read.",
            502,
        )

    raw_videos = payload.get("videos") if isinstance(payload, dict) else None
    if not isinstance(raw_videos, list):
        return []

    results: list[dict[str, object]] = []
    for raw in raw_videos:
        normalised = normalise_video(raw) if isinstance(raw, dict) else None
        if normalised is not None:
            results.append(normalised)
    return results


# -------------------------------------------------------------------- photos


def photo_src(photo: dict) -> dict:
    """The rendition URLs of a photo record, or an empty mapping."""

    source = photo.get("src") if isinstance(photo.get("src"), dict) else {}
    return source


def rendition_size(url: str, quality: str, record: tuple[int, int]) -> tuple[int, int]:
    """The pixel size a rendition will really have.

    The transform parameters in the URL are the delivered size, so they win; the
    documented size for that rendition is the next best answer; the photo's own
    size is the last resort. When only one dimension is stated the photo's aspect
    ratio fills in the other, because the transformation scales rather than crops.
    """

    record_width, record_height = record
    aspect = (
        record_width / record_height if record_width > 0 and record_height > 0 else 0.0
    )

    if quality in PHOTO_RENDITION_FIXED_SIZE:
        return PHOTO_RENDITION_FIXED_SIZE[quality]

    try:
        parameters = urllib.parse.parse_qs(urllib.parse.urlparse(str(url)).query)
    except ValueError:
        parameters = {}

    dpr = _positive_float((parameters.get("dpr") or [1])[0]) or 1.0
    width = _positive_int((parameters.get("w") or [0])[0])
    height = _positive_int((parameters.get("h") or [0])[0])

    if width and height:
        return (round(width * dpr), round(height * dpr))
    if height and aspect > 0:
        return (round(height * dpr * aspect), round(height * dpr))
    if width and aspect > 0:
        return (round(width * dpr), round(width * dpr / aspect))

    long_side = PHOTO_RENDITION_LONG_SIDE.get(quality)
    if long_side and aspect > 0:
        # The long side is scaled; which side that is depends on the photo itself.
        if aspect >= 1:
            return (long_side, round(long_side / aspect))
        return (round(long_side * aspect), long_side)

    return (record_width, record_height)


def photo_files(photo: dict) -> list[dict[str, object]]:
    """The downloadable renditions of a stock photo, smallest decision first.

    A photo has no ``video_files`` array, so the renditions are built from ``src``
    in a fixed order rather than trusting whatever the response happens to hold.
    Each one carries the size it will really be.
    """

    source = photo_src(photo)
    record = (_positive_int(photo.get("width")), _positive_int(photo.get("height")))
    files: list[dict[str, object]] = []

    for rank, size in enumerate(PHOTO_SIZE_ORDER):
        link = _CONTROL.sub("", str(source.get(size) or "")).strip()
        if not link:
            continue
        width, height = rendition_size(link, size, record)
        files.append(
            {
                "id": 0,
                "quality": size,
                "file_type": "image/jpeg",
                "width": width,
                "height": height,
                "link": link,
                "rank": rank,
            }
        )
    return files


def choose_photo_file(files: Sequence[dict]) -> dict[str, object] | None:
    """Pick the rendition to download: the best one that is not the original."""

    if not files:
        return None
    ordered = sorted(files, key=lambda item: int(item.get("rank") or 0))
    return dict(ordered[0])


def normalise_photo(photo: dict) -> dict[str, object] | None:
    """Turn one Pexels photo record into the shape this app uses.

    The result has the same keys as a normalised video, so one ranking and one
    import path serve both kinds of cutaway. A photo's duration is zero because it
    has none: the timeline interval decides how long it stays on screen.
    """

    if not isinstance(photo, dict):
        return None

    photo_id = _positive_int(photo.get("id"))
    if photo_id == 0:
        return None

    files = photo_files(photo)
    best = choose_photo_file(files)
    if best is None:
        return None

    user = photo.get("user") if isinstance(photo.get("user"), dict) else {}
    source = photo_src(photo)
    # The live API answers a search with ``photographer`` at the top level and only
    # some responses carry the ``user`` object, so both are read. The credit is a
    # licence requirement, so it must not depend on which shape arrived. Some
    # records put a URL in that field; a link is then shown instead of a name.
    credited = " ".join(str(photo.get("photographer") or user.get("name") or "").split())[:80]
    photographer = "" if "://" in credited else credited
    photographer_url = _CONTROL.sub(
        "", str(photo.get("photographer_url") or user.get("url") or "")
    ).strip()
    preview = ""
    for size in PHOTO_PREVIEW_SIZES:
        preview = _CONTROL.sub("", str(source.get(size) or "")).strip()
        if preview:
            break

    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "media_type": "photo",
        "id": str(photo_id),
        "width": _positive_int(best.get("width")) or _positive_int(photo.get("width")),
        "height": _positive_int(best.get("height")) or _positive_int(photo.get("height")),
        "duration": 0.0,
        "alt": " ".join(str(photo.get("alt") or "").split())[:160],
        # The rendition's own size: it is the file that gets downloaded and
        # composited, so it is the size the ranking and the interface should see.
        "original_width": _positive_int(photo.get("width")),
        "original_height": _positive_int(photo.get("height")),
        "page_url": _CONTROL.sub("", str(photo.get("url") or "")).strip(),
        "preview_url": preview,
        "photographer": photographer,
        "photographer_url": photographer_url,
        "attribution": ATTRIBUTION,
        "license": {"name": LICENSE_NAME, "url": LICENSE_URL},
        "files": files,
        "best_file": best,
    }


def parse_photo_results(body: bytes) -> list[dict[str, object]]:
    """Parse a photo search response, dropping anything unusable."""

    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, TypeError, AttributeError):
        raise ApiError(
            "stock_unavailable",
            "The stock provider returned a response that could not be read.",
            502,
        )

    raw_photos = payload.get("photos") if isinstance(payload, dict) else None
    if not isinstance(raw_photos, list):
        return []

    results: list[dict[str, object]] = []
    for raw in raw_photos:
        normalised = normalise_photo(raw) if isinstance(raw, dict) else None
        if normalised is not None:
            results.append(normalised)
    return results


# ------------------------------------------------------------------ requests


def _require_settings(settings: PexelsSettings) -> None:
    if not settings.enabled:
        raise ApiError(
            "stock_not_configured",
            "Stock search needs a Pexels API key. Set PEXELS_API_KEY in .env and "
            "restart the app; local B-roll and sound effects keep working without it.",
            503,
        )


def _request_json(
    settings: PexelsSettings, path: str, *, opener: Opener | None = None
) -> bytes:
    """One authorised GET against the provider, with plain-language failures."""

    request = urllib.request.Request(  # noqa: S310
        f"{settings.base_url.rstrip('/')}{path}",
        headers={
            "Authorization": str(settings.api_key).strip(),
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )

    try:
        status, body = (opener or _urlopen)(request, settings.timeout_seconds)
    except urllib.error.HTTPError as exc:
        status, body = int(exc.code), b""
    except Exception as exc:  # noqa: BLE001 - a network extra must not crash the app
        raise ApiError(
            "stock_unavailable",
            f"Could not reach {PROVIDER_NAME}. Check your connection; local assets "
            f"still work. ({type(exc).__name__})",
            503,
        ) from exc

    if status == 200:
        return body
    if status in {401, 403}:
        raise ApiError(
            "stock_unauthorized",
            f"{PROVIDER_NAME} rejected the configured API key. Check PEXELS_API_KEY "
            "in .env.",
            502,
        )
    if status == 429:
        raise ApiError(
            "stock_rate_limited",
            f"{PROVIDER_NAME} is rate-limiting this key. Wait a moment and search "
            "again; local assets are unaffected.",
            429,
        )
    raise ApiError(
        "stock_unavailable",
        f"{PROVIDER_NAME} returned HTTP {status}. Local assets still work.",
        502,
    )


def search_videos(
    settings: PexelsSettings,
    query: str,
    *,
    per_page: int = DEFAULT_PER_PAGE,
    orientation: str = "all",
    opener: Opener | None = None,
) -> list[dict[str, object]]:
    """Search stock video by keyword, returning metadata only."""

    _require_settings(settings)

    text = normalise_query(query)
    if not text:
        raise ApiError(
            "invalid_stock_query",
            "A stock search needs a keyword. Type one, or select a clip so the "
            "search can use the transcript.",
            422,
        )

    wanted = str(orientation or "all").strip().lower()
    if wanted not in ORIENTATIONS:
        raise ApiError(
            "invalid_stock_query",
            f"orientation must be one of {', '.join(ORIENTATIONS)} (got '{orientation}').",
            422,
        )

    count = min(MAX_PER_PAGE, max(1, int(per_page or DEFAULT_PER_PAGE)))
    parameters = {"query": text, "per_page": str(count)}
    if wanted != "all":
        parameters["orientation"] = wanted

    path = f"/videos/search?{urllib.parse.urlencode(parameters)}"
    return parse_search_results(_request_json(settings, path, opener=opener))


def search_photos(
    settings: PexelsSettings,
    query: str,
    *,
    per_page: int = DEFAULT_PER_PAGE,
    orientation: str = "all",
    opener: Opener | None = None,
) -> list[dict[str, object]]:
    """Search stock photos by keyword, returning metadata only.

    A still image is often the better cutaway for a sentence about a place, an
    object, or a diagram; both kinds are searched so the best one wins.
    """

    _require_settings(settings)

    text = normalise_query(query)
    if not text:
        raise ApiError(
            "invalid_stock_query",
            "A stock search needs a keyword. Type one, or select a clip so the "
            "search can use the transcript.",
            422,
        )

    wanted = str(orientation or "all").strip().lower()
    if wanted not in ORIENTATIONS:
        raise ApiError(
            "invalid_stock_query",
            f"orientation must be one of {', '.join(ORIENTATIONS)} (got '{orientation}').",
            422,
        )

    count = min(MAX_PER_PAGE, max(1, int(per_page or DEFAULT_PER_PAGE)))
    parameters = {"query": text, "per_page": str(count)}
    if wanted != "all":
        parameters["orientation"] = wanted

    path = f"/v1/search?{urllib.parse.urlencode(parameters)}"
    return parse_photo_results(_request_json(settings, path, opener=opener))


def fetch_photo(
    settings: PexelsSettings, photo_id: object, *, opener: Opener | None = None
) -> dict[str, object]:
    """Read one Pexels photo by id, so only a provider URL is ever downloaded."""

    _require_settings(settings)

    identifier = _positive_int(photo_id)
    if identifier == 0:
        raise ApiError(
            "invalid_stock_selection", "Send the numeric 'photo_id' to import.", 422
        )

    body = _request_json(settings, f"/v1/photos/{identifier}", opener=opener)
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, TypeError, AttributeError):
        raise ApiError(
            "stock_unavailable",
            "The stock provider returned a response that could not be read.",
            502,
        )

    photo = normalise_photo(payload if isinstance(payload, dict) else {})
    if photo is None:
        raise ApiError(
            "stock_unavailable",
            "That stock photo is no longer available from the provider.",
            404,
        )
    return photo


def fetch_video(
    settings: PexelsSettings, video_id: object, *, opener: Opener | None = None
) -> dict[str, object]:
    """Read one Pexels video by id.

    Importing re-reads the video here instead of trusting details sent by the
    browser, so only a URL the provider itself returned can ever be downloaded.
    """

    _require_settings(settings)

    identifier = _positive_int(video_id)
    if identifier == 0:
        raise ApiError(
            "invalid_stock_selection", "Send the numeric 'video_id' to import.", 422
        )

    body = _request_json(settings, f"/videos/videos/{identifier}", opener=opener)
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, TypeError, AttributeError):
        raise ApiError(
            "stock_unavailable",
            "The stock provider returned a response that could not be read.",
            502,
        )

    video = normalise_video(payload if isinstance(payload, dict) else {})
    if video is None:
        raise ApiError(
            "stock_unavailable",
            "That stock video is no longer available from the provider.",
            404,
        )
    return video


# ----------------------------------------------------------------- downloads


def download_host_allowed(url: str) -> bool:
    """Whether a URL is one this app is willing to fetch."""

    try:
        parsed = urllib.parse.urlparse(str(url or ""))
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host == PROVIDER or host.endswith(PEXELS_HOST_SUFFIX):
        return True
    return host in ALLOWED_CDN_HOSTS


def download_file(
    url: str,
    destination: Path,
    *,
    max_bytes: int,
    timeout: int = DOWNLOAD_TIMEOUT_SECONDS,
    opener=None,
) -> int:
    """Stream one file to disk, enforcing the size limit while writing.

    The clip is never held in memory, and the destination is a generated path
    inside the project, so a download cannot land anywhere else.
    """

    if not download_host_allowed(url):
        raise ApiError(
            "invalid_stock_source",
            "That download address is not a Pexels media host, so it was refused.",
            422,
        )

    request = urllib.request.Request(  # noqa: S310
        url, headers={"User-Agent": USER_AGENT}, method="GET"
    )

    try:
        response = (opener or _urlopen_stream)(request, timeout)
    except Exception as exc:  # noqa: BLE001 - network extras must stay non-fatal
        raise ApiError(
            "stock_unavailable",
            f"Could not download the clip from {PROVIDER_NAME}. ({type(exc).__name__})",
            503,
        ) from exc

    written = 0
    try:
        status = int(getattr(response, "status", 200) or 200)
        if status != 200:
            raise ApiError(
                "stock_unavailable",
                f"{PROVIDER_NAME} returned HTTP {status} for the clip download.",
                502,
            )

        headers = getattr(response, "headers", None)
        declared = None
        if headers is not None:
            try:
                declared = headers.get("Content-Length")
            except AttributeError:
                declared = None
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ApiError(
                "file_too_large",
                f"That clip is larger than the {max_bytes // (1024 * 1024)} MB import "
                "limit.",
                413,
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ApiError(
                        "file_too_large",
                        f"That clip is larger than the {max_bytes // (1024 * 1024)} MB "
                        "import limit.",
                        413,
                    )
                handle.write(chunk)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    if written == 0:
        raise ApiError(
            "empty_download", "The stock provider returned an empty file.", 502
        )
    return written


# ---------------------------------------------------------------- provenance


def provenance_for(video: dict, file: dict | None) -> dict[str, object]:
    """The credit record saved with an imported stock clip.

    ``TECH_STACK.md`` requires the source URL and creator to be recorded, and the
    attribution string to be shown where the media appears.
    """

    chosen = file or video.get("best_file") or {}
    media_type = str(video.get("media_type") or "video")
    return {
        "provider": PROVIDER,
        "provider_name": PROVIDER_NAME,
        "provider_url": PROVIDER_URL,
        # "video" or "photo": the credit is the same either way, but the record
        # says which one it is so the interface can label it.
        "media_type": media_type,
        "id": str(video.get("id") or ""),
        "page_url": str(video.get("page_url") or ""),
        "photographer": str(video.get("photographer") or ""),
        "photographer_url": str(video.get("photographer_url") or ""),
        "attribution": ATTRIBUTION,
        "license": {"name": LICENSE_NAME, "url": LICENSE_URL},
        "file_id": _positive_int(chosen.get("id")) or None,
        "file_quality": str(chosen.get("quality") or ""),
        "file_width": _positive_int(chosen.get("width")),
        "file_height": _positive_int(chosen.get("height")),
    }


def select_file(video: dict, file_id: object) -> dict[str, object]:
    """The rendition to download: the requested one, or the best default."""

    files: Iterable[dict] = video.get("files") or []
    wanted = _positive_int(file_id)
    if wanted:
        for item in files:
            if _positive_int(item.get("id")) == wanted:
                return dict(item)
        raise ApiError(
            "invalid_stock_selection",
            f"That video has no downloadable file with id {wanted}.",
            422,
        )

    best = video.get("best_file")
    if not isinstance(best, dict):
        raise ApiError(
            "stock_unavailable", "That stock video has no downloadable file.", 404
        )
    return dict(best)


def describe_result(video: dict, chosen: dict | None = None) -> dict[str, object]:
    """One search result as the interface shows it, with provenance attached."""

    file = chosen if chosen is not None else video.get("best_file") or {}
    return {
        "provider": video.get("provider"),
        "provider_name": video.get("provider_name"),
        "media_type": video.get("media_type") or "video",
        "id": video.get("id"),
        "width": video.get("width"),
        "height": video.get("height"),
        "duration": video.get("duration"),
        "alt": video.get("alt"),
        "page_url": video.get("page_url"),
        "preview_url": video.get("preview_url"),
        "photographer": video.get("photographer"),
        "photographer_url": video.get("photographer_url"),
        "attribution": video.get("attribution"),
        "license": video.get("license"),
        "file": {
            "id": file.get("id"),
            "quality": file.get("quality"),
            "width": file.get("width"),
            "height": file.get("height"),
            "file_type": file.get("file_type"),
        },
    }
