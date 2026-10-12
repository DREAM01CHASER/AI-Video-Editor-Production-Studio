"""Automatic sound design: short effects placed where the picture changes.

``TECH_STACK.md`` says not to bundle large SFX packs in Git, and ``FEATURES.md``
(Milestone 9) asks for effects *only where they improve the edit*, with a toggle
to turn the whole thing off. Both are honoured the same way: the effect is
**synthesised by FFmpeg at render time** into the project's own ``temp/``
folder, so no audio file is downloaded, committed, or redistributed.

Placement is deliberately conservative. A whoosh goes on a B-roll entrance and
exit, and at a transition, because those are the moments the picture changes and
an effect has something to explain. Nothing is added at arbitrary points, and the
total number is capped: sound design that draws attention to itself is worse than
none at all.

Effects are capped at the same gain ceiling as a hand-placed sound effect, so an
automatic one can never bury the speaker either.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from .errors import ApiError
from .timeline import DEFAULT_SFX_FADE_SECONDS, SFX_MAX_GAIN_DB

FFMPEG_BINARY = "ffmpeg"

#: The synthesised effects available. Both are deliberately dull: a whoosh marks a
#: cutaway, a pop marks a single cut. Neither has a pitch that clashes with speech,
#: so the mix does not need a ducking pass to stay clear of the voice.
AUTO_SFX_KINDS = ("whoosh", "pop")
DEFAULT_AUTO_KIND = "whoosh"

#: Automatic effects sit further under the speech than a chosen one: the user
#: asked for a hint, not for a mix.
AUTO_SFX_GAIN_DB = -18.0
AUTO_SFX_DURATION_SECONDS = 0.7

#: The most automatic effects any clip may carry, whatever the edit looks like.
#: Overload is the failure mode this number exists to prevent, so it is a hard
#: ceiling rather than a default the caller can raise.
MAX_AUTO_SFX = 4

#: How many automatic effects the user is willing to hear. The settings expose
#: this so an edit can be dialled back to one effect, or to none at all.
AUTO_SFX_COUNT_CHOICES = (0, 1, 2, 3, 4)

#: The shortest gap between two automatic effects, in seconds. Closer than this
#: and they read as one smear instead of separate moments.
MIN_GAP_SECONDS = 0.4

#: Lead time for an effect that marks a picture change: it starts just before the
#: change so the sound and the cut land together.
LEAD_SECONDS = 0.15

#: The shortest effect worth writing to a file at all.
MIN_SFX_SECONDS = 0.05

SAMPLE_RATE = 48000


def ffmpeg_available() -> bool:
    return shutil.which(FFMPEG_BINARY) is not None


# ----------------------------------------------------------------- settings

#: The stored sound-design settings. Every project starts with this, so the
#: renderer never has to guess whether the feature was switched on.
DEFAULT_SETTINGS = {
    "enabled": False,
    "kind": DEFAULT_AUTO_KIND,
    "max_effects": 2,
}


def normalise_settings(
    raw: dict | None, fallback: dict | None = None
) -> dict[str, object]:
    """Validate the saved sound-design settings.

    ``FEATURES.md`` asks for a toggle that turns *all* automatic sound design
    off, so ``enabled`` is read first and everything else is still normalised
    even while it is off. That way switching the feature on later cannot reveal
    a half-validated setting that only fails at render time.
    """

    source = dict(raw) if isinstance(raw, dict) else {}
    defaults = {**DEFAULT_SETTINGS, **(fallback or {})}

    enabled = source.get("enabled", defaults.get("enabled"))
    if not isinstance(enabled, bool):
        raise ApiError(
            "invalid_sound_design",
            f"sound_design.enabled must be true or false (got '{enabled}').",
            422,
        )

    kind = str(source.get("kind") or defaults.get("kind") or DEFAULT_AUTO_KIND)
    kind = kind.strip().lower()
    if kind not in AUTO_SFX_KINDS:
        raise ApiError(
            "invalid_sound_design",
            f"sound_design.kind must be one of {', '.join(AUTO_SFX_KINDS)} "
            f"(got '{kind}').",
            422,
        )

    requested = source.get("max_effects", defaults.get("max_effects"))
    try:
        max_effects = int(requested)
    except (TypeError, ValueError) as exc:
        raise ApiError(
            "invalid_sound_design",
            f"sound_design.max_effects must be a number (got '{requested}').",
            422,
        ) from exc

    if max_effects not in AUTO_SFX_COUNT_CHOICES:
        raise ApiError(
            "invalid_sound_design",
            "sound_design.max_effects must be one of "
            f"{', '.join(str(choice) for choice in AUTO_SFX_COUNT_CHOICES)} "
            f"(got {max_effects}).",
            422,
        )

    return {"enabled": enabled, "kind": kind, "max_effects": max_effects}


def gain_db() -> float:
    """The gain an automatic effect is mixed at, within the shared ceiling."""

    return min(AUTO_SFX_GAIN_DB, SFX_MAX_GAIN_DB)


def _moment(*, start: float, clip_duration: float, lead: float = LEAD_SECONDS) -> float:
    return round(max(0.0, min(start, clip_duration)), 3)


def propose_auto_sfx(
    timeline: dict,
    *,
    clip_duration: float,
    enabled: bool = True,
    kind: str = DEFAULT_AUTO_KIND,
    max_effects: int = MAX_AUTO_SFX,
) -> list[dict]:
    """Where an automatic effect could go, newest picture change first.

    ``timeline`` is the stored edit, so the proposals follow what is really on the
    tracks rather than a guess. The returned items are *drafts*: they are placed
    inside the clip, gain-capped, and ready to render, but the toggle can remove
    all of them again.
    """

    if not enabled or clip_duration <= 0 or max_effects <= 0:
        return []

    if kind not in AUTO_SFX_KINDS:
        raise ApiError(
            "invalid_sound_design",
            f"kind must be one of {', '.join(AUTO_SFX_KINDS)} (got '{kind}').",
            422,
        )

    moments: list[float] = []

    for item in timeline.get("broll") or []:
        if not isinstance(item, dict):
            continue
        moments.append(float(item.get("start") or 0.0))
        moments.append(float(item.get("end") or 0.0))

    for item in timeline.get("transitions") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("at") or "start") == "end":
            moments.append(float(clip_duration))

    # Only moments inside the clip, at least a beat apart, and never more than
    # the cap — an edit with twenty cutaways does not get twenty whooshes.
    limit = min(int(max_effects), MAX_AUTO_SFX)
    chosen: list[float] = []
    for moment in sorted(moment for moment in moments if 0.0 < moment < clip_duration):
        if any(abs(moment - kept) < MIN_GAP_SECONDS for kept in chosen):
            continue
        chosen.append(moment)
        if len(chosen) >= limit:
            break

    effects: list[dict] = []
    for position, moment in enumerate(chosen, start=1):
        start = _moment(start=moment - LEAD_SECONDS, clip_duration=clip_duration)
        end = round(min(clip_duration, start + AUTO_SFX_DURATION_SECONDS), 3)
        if end - start < MIN_SFX_SECONDS:
            continue
        effects.append(
            {
                "id": f"auto-{kind}-{position}",
                "kind": kind,
                "start": start,
                "end": end,
                "gain_db": gain_db(),
                "fade_seconds": min(DEFAULT_SFX_FADE_SECONDS, (end - start) / 2),
                "automatic": True,
                "label": f"Auto {kind} at {moment:.1f}s",
            }
        )

    return effects


def build_synth_command(kind: str, duration: float, output_path) -> list[str]:
    """The FFmpeg arguments that synthesise one effect into a small WAV.

    Writing a real file means an automatic effect goes through exactly the same
    mixing path as a hand-placed one, so there is one code path and one set of
    guarantees rather than two.

    The graph deliberately does **not** apply the gain: the mix graph applies
    ``item["gain_db"]`` to every effect, and baking it in here as well would
    attenuate an automatic effect twice.
    """

    if kind not in AUTO_SFX_KINDS:
        raise ApiError(
            "invalid_sound_design",
            f"kind must be one of {', '.join(AUTO_SFX_KINDS)} (got '{kind}').",
            422,
        )

    length = max(MIN_SFX_SECONDS, min(float(duration), 30.0))
    fade = max(0.02, min(DEFAULT_SFX_FADE_SECONDS, length / 2))
    tail = max(0.0, length - fade)
    # The envelope peaks halfway through, whichever length was asked for.
    half = max(0.01, length / 2)

    if kind == "whoosh":
        # A whoosh is band-limited noise under an envelope that swells in the
        # middle and falls away again.
        #
        # `eval=frame` is required: the default evaluates the expression while the
        # filter is configured, where `t` is not yet defined and the graph fails to
        # build. The commas inside the quoted expression are safe because the
        # quotes protect them from the filtergraph's own comma separator.
        source = (
            f"anoisesrc=color=white:sample_rate={SAMPLE_RATE}:"
            f"duration={length:.3f}:amplitude=0.6"
        )
        sweep = (
            f"volume=volume='min(1,0.2+0.8*t/{half:.3f})*"
            f"min(1,0.2+0.8*({length:.3f}-t)/{half:.3f})':eval=frame"
        )
    else:
        # A pop is a short pitched blip that rises slightly as it decays: it marks
        # a single cut instead of a whole move, and has no noise to smear.
        source = (
            f"aevalsrc='0.6*sin(2*PI*(180+320*t/{half:.3f})*t)':"
            f"s={SAMPLE_RATE}:d={length:.3f}"
        )
        sweep = "lowpass=f=2600"

    graph = (
        f"{source},lowpass=f=3400,{sweep},"
        f"afade=t=in:st=0:d={fade:.3f},"
        f"afade=t=out:st={tail:.3f}:d={fade:.3f},"
        f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo"
    )

    return [
        FFMPEG_BINARY,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        graph,
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]


def synth_is_needed(
    timeline: dict, *, clip_duration: float, enabled: bool, kind: str = DEFAULT_AUTO_KIND,
    max_effects: int = MAX_AUTO_SFX,
) -> bool:
    """Whether the render will need a synthesised effect at all."""

    return bool(
        propose_auto_sfx(
            timeline,
            clip_duration=clip_duration,
            enabled=enabled,
            kind=kind,
            max_effects=max_effects,
        )
    )


def materialise(
    effects: Sequence[dict], temp_dir
) -> list[dict]:
    """Write each proposed effect to a WAV and return it with a real ``path``.

    The returned entries are shaped exactly like hand-placed sound-effect items,
    so the mix graph cannot tell an automatic effect from a chosen one. That is
    the point: one mixing path means one set of guarantees about level and timing.

    A failure removes every file written by this call, so a refused render never
    leaves half-written audio in the project's temp folder.
    """

    written: list[Path] = []
    try:
        entries: list[dict] = []
        for position, effect in enumerate(effects, start=1):
            path = Path(temp_dir) / f"auto-sfx-{position}.wav"
            render_synth_into(
                str(effect.get("kind") or DEFAULT_AUTO_KIND),
                float(effect.get("end") or 0.0) - float(effect.get("start") or 0.0),
                path,
            )
            written.append(path)
            entries.append({**effect, "path": path, "automatic": True})
        return entries
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise


def describe(effects: Sequence[dict]) -> str:
    """A plain-language note for the export result."""

    if not effects:
        return ""
    count = len(effects)
    noun = str(effects[0].get("kind") or DEFAULT_AUTO_KIND)
    if count != 1:
        return (
            f"{count} automatic {noun} effects were synthesised at the picture "
            "changes and mixed under the speech."
        )
    return (
        f"1 automatic {noun} was synthesised at a picture change and mixed under "
        "the speech."
    )


def render_synth_into(
    kind: str,
    duration: float,
    output_path,
    *,
    should_cancel=None,
) -> None:
    """Synthesise one effect, failing loudly rather than silently skipping it."""

    if not ffmpeg_available():
        raise ApiError(
            "ffmpeg_unavailable",
            "FFmpeg was not found on PATH, so automatic sound effects cannot be "
            "created. Install FFmpeg and restart the app.",
            503,
        )

    command = build_synth_command(kind, duration, output_path)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            shell=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise ApiError(
            "ffmpeg_unavailable",
            "FFmpeg was not found on PATH, so automatic sound effects cannot be "
            "created. Install FFmpeg and restart the app.",
            503,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ApiError(
            "sound_design_failed",
            "Creating an automatic sound effect timed out.",
            500,
        ) from exc

    if should_cancel and should_cancel():
        output_path.unlink(missing_ok=True)
        raise ApiError(
            "render_cancelled",
            "The export was cancelled before it finished, so no file was kept.",
            409,
        )

    if completed.returncode != 0 or not output_path.is_file():
        output_path.unlink(missing_ok=True)
        raise ApiError(
            "sound_design_failed",
            "FFmpeg could not create the automatic sound effect. Last messages: "
            + (completed.stderr.strip()[-400:] or "no output"),
            500,
        )
