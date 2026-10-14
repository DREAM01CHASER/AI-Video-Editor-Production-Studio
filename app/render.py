"""FFmpeg rendering for one captioned vertical clip.

The pipeline is deliberately transparent: :func:`build_ffmpeg_command` returns the
exact argument list that runs, so the edit is inspectable and testable rather
than hidden inside a shell string. FFmpeg is a **system tool**, not a Python
package, and a missing binary is reported as a clear error instead of a crash.

Rendering is a long job, so :func:`run_ffmpeg_render` reports real progress from
FFmpeg's own ``-progress`` stream (never an animation), honours a cancel request
by ending the process, and keeps the tail of FFmpeg's log so a failure can be
explained in plain language.
"""

from __future__ import annotations

import collections
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Sequence

from .errors import ApiError
from .storage import is_image_filename
from .timeline import DEFAULT_FPS, normalise_fps
from .transitions import build_transition_chain

FFMPEG_BINARY = "ffmpeg"

#: Output sizes offered by the interface, and the aspect ratio each one belongs
#: to. A size that does not match the chosen ratio is rejected rather than
#: silently stretched.
RESOLUTION_ASPECT = {
    "1080x1920": "9:16",
    "1440x2560": "9:16",
    "1080x1080": "1:1",
    "1920x1080": "16:9",
}
ASPECT_RATIOS = ("9:16", "1:1", "16:9")
EXPORT_FORMATS = ("mp4",)
CAPTION_MODES = ("burn", "none")

#: 1440x2560 is an upscale for almost every source, so it is never the default
#: and the result says out loud that it was upscaled and slower.
HIGH_RESOLUTION = "1440x2560"

DEFAULT_EXPORT = {
    "aspect_ratio": "9:16",
    "resolution": "1080x1920",
    "format": "mp4",
    "fps": DEFAULT_FPS,
    "caption_mode": "burn",
    "write_srt": True,
}

VIDEO_ENCODER = "libx264"
ENCODER_PRESET = "veryfast"
QUALITY_CRF = 20
PIXEL_FORMAT = "yuv420p"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "160k"

#: How much of FFmpeg's log is kept. Enough to explain a failure, bounded so a
#: noisy render cannot grow without limit.
LOG_TAIL_LINES = 40

ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]

_GLYPH_WARNING = re.compile(r"glyph .* not found|fontselect: .*failed", re.IGNORECASE)


def ffmpeg_available() -> bool:
    """Report whether FFmpeg can be found on PATH."""

    return shutil.which(FFMPEG_BINARY) is not None


# ----------------------------------------------------------------- settings


def _size_for(resolution: str) -> tuple[int, int]:
    width, _, height = resolution.partition("x")
    return int(width), int(height)


def normalise_export(
    raw: dict | None,
    *,
    source_width: int = 0,
    source_height: int = 0,
    source_fps: float = 0.0,
) -> dict[str, object]:
    """Validate export settings and describe what the render will do.

    ``upscaled`` and ``notes`` exist so the interface can state plainly when a
    choice means enlarging the source or waiting longer.
    """

    source = dict(raw or {})

    resolution = str(source.get("resolution") or DEFAULT_EXPORT["resolution"]).strip()
    if resolution not in RESOLUTION_ASPECT:
        raise ApiError(
            "invalid_export_settings",
            "resolution must be one of "
            f"{', '.join(RESOLUTION_ASPECT)} (got '{resolution}').",
            422,
        )

    aspect_ratio = str(source.get("aspect_ratio") or RESOLUTION_ASPECT[resolution]).strip()
    if aspect_ratio not in ASPECT_RATIOS:
        raise ApiError(
            "invalid_export_settings",
            f"aspect_ratio must be one of {', '.join(ASPECT_RATIOS)} "
            f"(got '{aspect_ratio}').",
            422,
        )

    expected = RESOLUTION_ASPECT[resolution]
    if expected != aspect_ratio:
        raise ApiError(
            "invalid_export_settings",
            f"{resolution} is a {expected} size, so it cannot be exported as "
            f"{aspect_ratio}. Pick a matching output resolution.",
            422,
        )

    export_format = str(source.get("format") or DEFAULT_EXPORT["format"]).strip().lower()
    if export_format not in EXPORT_FORMATS:
        raise ApiError(
            "invalid_export_settings",
            f"format must be one of {', '.join(EXPORT_FORMATS)} (got '{export_format}'). "
            "MP4 is the only format implemented so far.",
            422,
        )

    # The frame rate is part of the edit, so the export reshapes the source to the
    # rate the timeline asked for instead of silently keeping the source's.
    fps = normalise_fps(
        source.get("fps"),
        int(DEFAULT_EXPORT["fps"]),
        code="invalid_export_settings",
    )

    caption_mode = str(source.get("caption_mode") or DEFAULT_EXPORT["caption_mode"]).strip().lower()
    if caption_mode not in CAPTION_MODES:
        raise ApiError(
            "invalid_export_settings",
            f"caption_mode must be one of {', '.join(CAPTION_MODES)} "
            f"(got '{caption_mode}').",
            422,
        )

    write_srt = source.get("write_srt")
    if write_srt is None:
        write_srt = DEFAULT_EXPORT["write_srt"]
    elif not isinstance(write_srt, bool):
        raise ApiError(
            "invalid_export_settings",
            f"write_srt must be true or false (got '{write_srt}').",
            422,
        )

    width, height = _size_for(resolution)
    upscaled = bool(source_height and height > source_height)
    notes: list[str] = []
    if upscaled:
        notes.append(
            f"This export is {width}×{height}, which is larger than the "
            f"{source_width}×{source_height} source, so it is upscaled and encodes "
            "more slowly."
        )
    elif resolution == HIGH_RESOLUTION:
        notes.append("2K output was chosen explicitly; 1080 × 1920 is the faster default.")

    if source_fps and abs(float(source_fps) - fps) > 0.01:
        notes.append(
            f"The source runs at {float(source_fps):g} fps and this export is {fps} fps, "
            "so frames are resampled and fast movement may look slightly different."
        )

    if caption_mode == "none":
        notes.append("Captions are switched off for this export.")

    return {
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "width": width,
        "height": height,
        "format": export_format,
        "fps": fps,
        "caption_mode": caption_mode,
        "write_srt": bool(write_srt),
        "upscaled": upscaled,
        "notes": notes,
    }


# --------------------------------------------------------------- command


def escape_filter_path(path: str) -> str:
    """Escape a path for use as an FFmpeg filter argument.

    FFmpeg's filtergraph parser treats ``\\``, ``:``, and ``'`` specially, so a
    Windows path such as ``C:\\tmp\\a.ass`` has to be escaped or the filter fails.
    Callers that can run FFmpeg from the subtitle's own directory avoid this
    entirely, which is what the render job does.
    """

    text = str(path).replace("\\", "/").replace("'", r"\'").replace(":", r"\:")
    return f"'{text}'"


def subtitle_filter_value(subtitle_name: str) -> str:
    """A value usable inside ``ass=...`` for a bare filename or a full path."""

    if any(character in subtitle_name for character in ("/", "\\", ":", "'", " ")):
        return escape_filter_path(subtitle_name)
    return subtitle_name


def build_filter_chain(
    width: int,
    height: int,
    subtitle_name: str | None = None,
    *,
    duration: float = 0.0,
    transitions: Sequence[dict] = (),
) -> str:
    """Scale to cover the output, crop the centre, draw the captions, transition.

    ``force_original_aspect_ratio=increase`` followed by a centred crop keeps the
    source's aspect ratio and fills the frame without letterboxing, which is what
    a vertical short needs. No filter ever changes the audio.

    Transitions come last so the clip arrives and leaves as one finished picture:
    a fade has to cover the captions too, not just the raw source.
    """

    chain = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )
    if subtitle_name:
        chain = f"{chain},ass={subtitle_filter_value(subtitle_name)}"

    transition_chain = build_transition_chain(
        transitions, width=width, height=height, clip_duration=duration
    )
    if transition_chain:
        chain = f"{chain},{transition_chain}"
    return chain


#: FFmpeg wants an explicit layout when mixing, so effects are converted to it.
SFX_SAMPLE_RATE = 48000


def broll_box(item: dict, width: int, height: int) -> tuple[int, int, int]:
    """The pixel box a B-roll insert fills, and the y position it sits at."""

    placement = str(item.get("placement") or "full")
    if placement == "top":
        return width, height // 2, 0
    if placement == "bottom":
        box_h = height // 2
        return width, box_h, height - box_h
    return width, height, 0


def broll_fit_filter(item: dict, box_width: int, box_height: int) -> str:
    """Scale an insert into its box: filling it, or fitting inside it.

    ``cover`` scales the insert up until it covers the box and crops the overhang,
    which is what a full-frame cutaway wants. ``contain`` fits the whole insert
    inside the box and pads the difference, which is what a portrait clip inserted
    into a 9:16 frame wants when nothing may be cut off.
    """

    if str(item.get("fit") or "cover") == "contain":
        return (
            f"scale={box_width}:{box_height}:force_original_aspect_ratio=decrease,"
            f"pad={box_width}:{box_height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )
    return (
        f"scale={box_width}:{box_height}:force_original_aspect_ratio=increase,"
        f"crop={box_width}:{box_height},setsar=1"
    )


def build_composite_filters(
    *,
    width: int,
    height: int,
    duration: float,
    subtitle_name: str | None = None,
    broll: Sequence[dict] = (),
    sfx: Sequence[dict] = (),
    transitions: Sequence[dict] = (),
    source_has_audio: bool = True,
) -> dict[str, str]:
    """Build the filtergraph that composites one clip, its captions, and its items.

    ``broll`` and ``sfx`` entries carry the FFmpeg input index each item was opened
    as, so the graph only refers to inputs that really exist. Returning the graph
    and the two output labels (instead of a finished argument list) keeps it
    readable in a test and impossible to confuse with a shell command.
    """

    lines: list[str] = []
    base = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    if subtitle_name:
        base = f"{base},ass={subtitle_filter_value(subtitle_name)}"
    lines.append(f"[0:v]{base}[vbase]")

    current = "vbase"
    for position, entry in enumerate(broll, start=1):
        item = entry["item"]
        box_width, box_height, box_y = broll_box(item, width, height)
        lines.append(f"[{entry['index']}:v]{broll_fit_filter(item, box_width, box_height)}[b{position}]")
        start = float(item.get("start") or 0.0)
        end = float(item.get("end") or 0.0)
        lines.append(
            f"[{current}][b{position}]overlay=0:{box_y}:"
            f"enable='between(t,{start:.3f},{end:.3f})'[v{position}]"
        )
        current = f"v{position}"

    # Transitions are applied to the *finished* picture, after the inserts are
    # composited, so a fade covers the B-roll too instead of letting it hang on
    # screen over black.
    transition_chain = build_transition_chain(
        transitions, width=width, height=height, clip_duration=duration
    )
    lines.append(f"[{current}]{transition_chain or 'null'}[vout]")

    audio_labels: list[str] = []
    if source_has_audio:
        lines.append("[0:a]anull[abase]")
        audio_labels.append("[abase]")

    for position, entry in enumerate(sfx, start=1):
        item = entry["item"]
        start = max(0.0, float(item.get("start") or 0.0))
        length = max(0.05, float(item.get("end") or 0.0) - start)
        gain_db = float(item.get("gain_db", -12.0))
        gain = 10 ** (gain_db / 20.0)
        fade = max(0.0, min(float(item.get("fade_seconds") or 0.0), length / 2))

        chain = (
            f"atrim=0:{length:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates={SFX_SAMPLE_RATE}:channel_layouts=stereo,"
            f"volume={gain:.4f}"
        )
        if fade > 0:
            chain += (
                f",afade=t=in:st=0:d={fade:.3f},"
                f"afade=t=out:st={max(0.0, length - fade):.3f}:d={fade:.3f}"
            )
        chain += f",adelay={int(round(start * 1000))}:all=1"
        lines.append(f"[{entry['index']}:a]{chain}[s{position}]")
        audio_labels.append(f"[s{position}]")

    audio: str | None = None
    if audio_labels:
        if len(audio_labels) == 1:
            lines.append(f"{audio_labels[0]}anull[mixed]")
        else:
            # normalize=0 keeps the speech at its recorded level: amix would
            # otherwise divide every input by the number of inputs and quieten the
            # speaker, which is the opposite of what an effect should do.
            #
            # `duration=first` is right when the source audio is one of the inputs,
            # because that stream spans the whole clip. With no source audio the
            # first input is one effect, which is only as long as the effect, so
            # `first` would cut the mix off part-way through — silently dropping a
            # later effect that the timeline said would be there.
            duration_mode = "first" if source_has_audio else "longest"
            lines.append(
                f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:"
                f"duration={duration_mode}:normalize=0[mixed]"
            )

        if source_has_audio:
            lines.append("[mixed]anull[aout]")
        else:
            # Without speech the mix only spans the last effect, so pad it out to
            # the clip's length. The export then carries silent audio rather than a
            # track that stops early and leaves the player guessing.
            lines.append(f"[mixed]apad,atrim=end={duration:.3f}[aout]")
        audio = "[aout]"

    # A source with no audio track still gets its effects: the export then carries
    # only the sound design, which is better than refusing to add any.
    return {"graph": ";".join(lines), "video": "[vout]", "audio": audio}


def build_ffmpeg_command(
    *,
    source_path: Path,
    output_path: Path,
    start: float,
    end: float,
    width: int,
    height: int,
    fps: int | None = None,
    subtitle_name: str | None = None,
    broll: Sequence[dict] = (),
    sfx: Sequence[dict] = (),
    transitions: Sequence[dict] = (),
    source_has_audio: bool = True,
) -> list[str]:
    """Return the exact FFmpeg argument list used to render one clip.

    Seeking with ``-ss`` **before** ``-i`` and re-encoding means the output starts
    on the requested frame without decoding the whole source first, and ``-t``
    bounds the clip even if the timestamps drift slightly.

    With no timeline items the command stays the simple single-input form. Items
    add inputs and switch to a ``filter_complex`` graph, because overlaying an
    insert and mixing an effect needs the streams combined rather than mapped.
    A transition on its own stays on the simple path: it is a plain video filter,
    so it needs no extra inputs and no audio mixing.
    """

    duration = max(0.001, float(end) - float(start))

    if broll or sfx:
        return _build_composite_command(
            source_path=source_path,
            output_path=output_path,
            start=start,
            end=end,
            width=width,
            height=height,
            fps=fps,
            subtitle_name=subtitle_name,
            duration=duration,
            broll=broll,
            sfx=sfx,
            transitions=transitions,
            source_has_audio=source_has_audio,
        )

    chain = build_filter_chain(
        width, height, subtitle_name, duration=duration, transitions=transitions
    )

    command = [
        FFMPEG_BINARY,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{max(0.0, float(start)):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        chain,
    ]

    if fps:
        # The requested rate is the timeline's, not the source's.
        command += ["-r", str(int(fps))]

    command += _output_arguments(output_path)
    return command


def _output_arguments(output_path: Path) -> list[str]:
    """The encoder and container settings shared by every render."""

    return [
        "-c:v",
        VIDEO_ENCODER,
        "-preset",
        ENCODER_PRESET,
        "-crf",
        str(QUALITY_CRF),
        "-pix_fmt",
        PIXEL_FORMAT,
        "-c:a",
        AUDIO_CODEC,
        "-b:a",
        AUDIO_BITRATE,
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output_path),
    ]


def _build_composite_command(
    *,
    source_path: Path,
    output_path: Path,
    start: float,
    end: float,
    width: int,
    height: int,
    fps: int | None,
    subtitle_name: str | None,
    duration: float,
    broll: Sequence[dict],
    sfx: Sequence[dict],
    transitions: Sequence[dict],
    source_has_audio: bool,
) -> list[str]:
    """Open the clip, its inserts, and its effects as inputs to one filtergraph.

    Each insert is seeked on its own input, so FFmpeg decodes only the seconds of
    the asset that are actually shown, and each effect keeps its own file because
    two effects may come from different assets.
    """

    inputs: list[str] = [
        "-ss",
        f"{max(0.0, float(start)):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source_path),
    ]

    broll_entries: list[dict] = []
    sfx_entries: list[dict] = []
    index = 1

    for item in broll:
        path = item.get("path")
        if not path:
            raise ApiError(
                "asset_file_missing",
                f"The B-roll item '{item.get('id')}' has no file to render.",
                404,
            )
        asset_start = max(0.0, float(item.get("source_start") or 0.0))
        length = max(0.05, float(item.get("end") or 0.0) - float(item.get("start") or 0.0))

        if is_image_filename(path):
            # A still image has no timeline of its own, so it is looped over the
            # whole interval. Without `-loop 1` the overlay would show a single
            # frame at the start of the cutaway and then nothing.
            inputs += [
                "-loop",
                "1",
                "-framerate",
                str(int(fps or DEFAULT_FPS)),
                "-t",
                f"{length:.3f}",
                "-i",
                str(path),
            ]
        else:
            inputs += [
                "-ss",
                f"{asset_start:.3f}",
                "-t",
                f"{length:.3f}",
                "-i",
                str(path),
            ]
        broll_entries.append({"item": item, "index": index})
        index += 1

    for item in sfx:
        path = item.get("path")
        if not path:
            raise ApiError(
                "asset_file_missing",
                f"The sound-effect item '{item.get('id')}' has no file to render.",
                404,
            )
        inputs += ["-i", str(path)]
        sfx_entries.append({"item": item, "index": index})
        index += 1

    filters = build_composite_filters(
        width=width,
        height=height,
        duration=duration,
        subtitle_name=subtitle_name,
        broll=broll_entries,
        sfx=sfx_entries,
        transitions=transitions,
        source_has_audio=source_has_audio,
    )

    command = [
        FFMPEG_BINARY,
        "-hide_banner",
        "-nostdin",
        "-y",
        *inputs,
        "-filter_complex",
        filters["graph"],
        "-map",
        filters["video"],
    ]

    if filters["audio"]:
        command += ["-map", filters["audio"]]

    if fps:
        command += ["-r", str(int(fps))]

    command += _output_arguments(output_path)
    return command


# --------------------------------------------------------------- progress


def parse_progress_line(line: str) -> tuple[str, str] | None:
    """Parse one ``key=value`` line from FFmpeg's ``-progress`` stream."""

    text = str(line or "").strip()
    if not text or "=" not in text:
        return None
    key, _, value = text.partition("=")
    key = key.strip()
    if not key:
        return None
    return key, value.strip()


def progress_percent(out_time_us: float, clip_seconds: float) -> float:
    """Fraction of the clip encoded so far, clamped to 0–99.

    The bar stops at 99 until the file is really written, so it never claims to
    be finished while FFmpeg is still flushing.
    """

    if clip_seconds <= 0:
        return 0.0
    fraction = max(0.0, float(out_time_us)) / 1_000_000.0 / clip_seconds
    return round(min(99.0, fraction * 100.0), 2)


def render_message(percent: float, clip_seconds: float) -> str:
    """Plain-language progress text, in seconds of finished video."""

    done = min(clip_seconds, clip_seconds * percent / 100.0)
    return f"Rendering {done:.0f}s of {clip_seconds:.0f}s…"


# ------------------------------------------------------------------ runner


def _drain_stderr(stream, sink: collections.deque, lock: threading.Lock) -> None:
    """Keep the tail of FFmpeg's log without ever blocking the render."""

    try:
        for raw in iter(stream.readline, ""):
            line = raw.rstrip()
            if not line:
                continue
            with lock:
                sink.append(line)
    except (ValueError, OSError):  # stream closed while shutting down
        return


def run_ffmpeg_render(
    *,
    command: list[str],
    clip_seconds: float,
    work_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> dict[str, object]:
    """Run FFmpeg, reporting real progress and honouring a cancel request."""

    if not ffmpeg_available():
        raise ApiError(
            "ffmpeg_unavailable",
            "FFmpeg was not found on PATH. Install FFmpeg and restart the app.",
            503,
        )

    log: collections.deque = collections.deque(maxlen=LOG_TAIL_LINES)
    lock = threading.Lock()

    try:
        process = subprocess.Popen(
            command,
            cwd=str(work_dir) if work_dir else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except FileNotFoundError as exc:
        raise ApiError(
            "ffmpeg_unavailable",
            "FFmpeg was not found on PATH. Install FFmpeg and restart the app.",
            503,
        ) from exc
    except OSError as exc:
        raise ApiError(
            "render_failed", f"FFmpeg could not be started: {exc}", 500
        ) from exc

    reader = threading.Thread(
        target=_drain_stderr, args=(process.stderr, log, lock), daemon=True
    )
    reader.start()

    started = time.time()
    cancelled = False

    try:
        for line in iter(process.stdout.readline, ""):
            if should_cancel and should_cancel():
                cancelled = True
                break

            parsed = parse_progress_line(line)
            if parsed is None:
                continue
            key, value = parsed
            if key == "out_time_us" and progress:
                try:
                    percent = progress_percent(float(value), clip_seconds)
                except ValueError:
                    continue
                progress(percent, render_message(percent, clip_seconds))
        process.stdout.close()
    finally:
        if cancelled or (should_cancel and should_cancel()):
            cancelled = True
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        else:
            process.wait()
        reader.join(timeout=5)

    if cancelled:
        raise ApiError(
            "render_cancelled",
            "The export was cancelled before it finished, so no file was kept.",
            409,
        )

    with lock:
        tail = list(log)

    warnings = [line for line in tail if _GLYPH_WARNING.search(line)]

    if process.returncode != 0:
        raise ApiError(
            "render_failed",
            "FFmpeg could not render this clip. Last messages: "
            + (" | ".join(tail[-5:]) if tail else "no output"),
            500,
        )

    return {
        "elapsed_seconds": round(time.time() - started, 3),
        "font_warnings": warnings[:5],
        "log_tail": tail[-5:],
    }
