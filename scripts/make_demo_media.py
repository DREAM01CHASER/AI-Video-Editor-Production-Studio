"""Create a licence-free demo project so the app can be tried immediately.

`FEATURES.md` (Milestone 10) asks for a sample project "only if licensing
permits". Downloading stock footage would put someone else's licence in the way,
so nothing is downloaded: the demo media is **synthesised locally** with FFmpeg
and Windows speech synthesis, which means it is ours to use, modify and ship.

What it makes, in `demo-media/`:

* ``demo-source.mp4`` — a "talking head" style clip with real spoken narration.
  Each sentence is a different flat colour, so trimming a clip and compositing a
  B-roll insert over it are both visible by eye.
* ``demo-broll.mp4`` — a short insert for the B-roll track.
* ``demo-sfx.wav`` — a short sound effect for the SFX track.

The narration is deliberately written as the kind of thing a creator clips into
shorts: a hook, a self-contained tip, and a closing line, so clip suggestion has
something real to work with.

Usage (from the project folder)::

    python scripts/make_demo_media.py
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

FFMPEG = "ffmpeg"
POWERSHELL = "powershell"

#: Each entry is (spoken line, hex colour). Distinct colours make the cut points,
#: the B-roll insert and the transitions obvious when the export is played.
SCENES: list[tuple[str, str]] = [
    (
        "Here is the thing nobody tells you about going viral. "
        "Everyone focuses on the first three seconds, and that is the wrong place "
        "to start.",
        "0x1f4e79",
    ),
    (
        "The five minute rule is simple. If a clip holds your attention for five "
        "minutes, it will hold it for five seconds. Attention is not shortened by "
        "the edit, it is earned by the idea.",
        "0x7b3f00",
    ),
    (
        "So before you record anything, ask one question. What is the single "
        "sentence I want a stranger to repeat back to me? Everything else in the "
        "video is support for that one sentence.",
        "0x1f6f4e",
    ),
    (
        "A hook is not a trick. It is a promise that the next ten seconds will pay "
        "it off. If you cannot finish that promise, the clip is not ready, and no "
        "transition will save it.",
        "0x6f1f4e",
    ),
    (
        "Finally, captions are not decoration. Most people watch a short on mute, "
        "so your captions are the edit. Write them for the ear, not for the eye.",
        "0x3f3f7b",
    ),
    (
        "That is the whole workflow. Import, transcribe, pick the moments that "
        "work on their own, cut them vertically, and let the captions do the "
        "talking. Go and try it.",
        "0x7b1f3f",
    ),
]

#: The insert's two scenes, so the B-roll window is obvious in the export.
BROLL_COLOURS = ("0xd8d8d8", "0x9a9a9a")

SECONDS_PER_SCENE = 12
BROLL_SECONDS = 3
SFX_SECONDS = 1


def require(tool: str, purpose: str) -> str:
    """Fail with an explanation rather than a traceback when a tool is missing."""

    found = shutil.which(tool)
    if not found:
        raise SystemExit(
            f"{tool} was not found on PATH, so {purpose} is impossible.\n"
            f"Install it and run this script again."
        )
    return found


def run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, shell=False)
    if completed.returncode != 0:
        raise SystemExit(
            f"{command[0]} failed with exit code {completed.returncode}.\n"
            f"{completed.stderr.strip()[-1500:]}"
        )


def speak(text: str, target: Path) -> None:
    """Render one line to a WAV using Windows' own speech synthesiser.

    Windows speech synthesis is a system component, so this needs no API key and
    no download — and the resulting audio is real speech, which is what lets the
    real Whisper model produce a genuinely readable transcript.
    """

    escaped = str(target).replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = -1; "
        f"$s.SetOutputToWaveFile('{escaped}'); "
        f"$s.Speak('{text.replace(chr(39), chr(39) * 2)}'); "
        "$s.Dispose();"
    )
    completed = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        raise SystemExit(
            "Windows speech synthesis failed, so no narration could be made.\n"
            f"{completed.stderr.strip()[-800:]}"
        )


def make_narration(target: Path) -> None:
    """Speak every scene into one WAV, padded so each scene is a known length."""

    with tempfile.TemporaryDirectory() as folder:
        parts: list[Path] = []
        for index, (line, _) in enumerate(SCENES, start=1):
            part = Path(folder) / f"scene-{index:02d}.wav"
            speak(line, part)
            # Pad or trim each scene to a fixed length so the video and the audio
            # line up, which is what makes the clip boundaries predictable.
            padded = Path(folder) / f"padded-{index:02d}.wav"
            run([
                FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(part),
                "-af", f"apad,atrim=end={SECONDS_PER_SCENE}",
                "-ar", "48000", "-ac", "2",
                str(padded),
            ])
            parts.append(padded)

        listing = Path(folder) / "concat.txt"
        listing.write_text(
            "".join(f"file '{part.name}'\n" for part in parts), encoding="utf-8"
        )
        run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c:a", "pcm_s16le", str(target),
        ])


def colour_video(colours: Sequence[str], seconds: int, output: Path) -> None:
    """Build a silent video whose scenes are distinct flat colours.

    A flat colour per scene is deliberate: it makes "did the B-roll actually get
    composited inside its own window?" answerable by eye, and it keeps the file
    tiny enough that a demo runs fast.

    Each scene also gets a white bar along the bottom whose length marks which
    scene it is, so a still frame from the export is identifiable.
    """

    with tempfile.TemporaryDirectory() as folder:
        inputs: list[str] = []
        filters: list[str] = []
        labels: list[str] = []
        total = len(colours)
        for index, colour in enumerate(colours):
            inputs += [
                "-f", "lavfi",
                "-i",
                f"color=c={colour}:size=640x360:rate=25:duration={seconds}",
            ]
            filters.append(
                f"[{index}:v]drawbox=x=0:y=ih-8:w=iw*{(index + 1) / total:.4f}:h=8:"
                "color=white:t=fill[v{0}]".format(index)
            )
            labels.append(f"[v{index}]")

        run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            *inputs,
            "-filter_complex", ";".join(filters) + ";" + "".join(labels)
            + f"concat=n={total}:v=1:a=0[out]",
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-pix_fmt", "yuv420p",
            str(output),
        ])


def make_source(target: Path) -> None:
    with tempfile.TemporaryDirectory() as folder:
        silent = Path(folder) / "silent.mp4"
        colour_video([colour for _, colour in SCENES], SECONDS_PER_SCENE, silent)
        narration = Path(folder) / "narration.wav"
        make_narration(narration)

        run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(silent), "-i", str(narration),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest",
            str(target),
        ])


def make_broll(target: Path) -> None:
    with tempfile.TemporaryDirectory() as folder:
        silent = Path(folder) / "silent.mp4"
        colour_video(BROLL_COLOURS, 2, silent)
        tone = Path(folder) / "tone.wav"
        run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={BROLL_SECONDS}",
            "-ar", "48000", "-ac", "2", str(tone),
        ])
        run([
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(silent), "-i", str(tone),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(target),
        ])


def make_sfx(target: Path) -> None:
    """A short whoosh-like effect, made the same way the app makes one."""

    length = SFX_SECONDS
    half = length / 2
    graph = (
        f"anoisesrc=color=white:sample_rate=48000:duration={length}:amplitude=0.5,"
        f"lowpass=f=3400,"
        f"volume=volume='min(1,0.2+0.8*t/{half:.3f})*"
        f"min(1,0.2+0.8*({length}-t)/{half:.3f})':eval=frame,"
        f"afade=t=in:st=0:d=0.1,afade=t=out:st={length - 0.1:.3f}:d=0.1,"
        f"aformat=sample_rates=48000:channel_layouts=stereo"
    )
    run([
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", graph,
        "-c:a", "pcm_s16le", str(target),
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "demo-media",
        help="where to write the demo files (default: ./demo-media)",
    )
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        raise SystemExit(
            "The demo narration uses Windows speech synthesis, so this script only "
            "runs on Windows. On other systems, import any video you have the right "
            "to use instead."
        )

    require(FFMPEG, "the demo video cannot be built")
    require(POWERSHELL, "the demo narration cannot be spoken")

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    print("Speaking the demo narration (this takes a moment)…")
    make_source(out / "demo-source.mp4")
    print("Building the B-roll insert…")
    make_broll(out / "demo-broll.mp4")
    print("Building the sound effect…")
    make_sfx(out / "demo-sfx.wav")

    print(f"\nDemo media written to {out}:")
    for name in ("demo-source.mp4", "demo-broll.mp4", "demo-sfx.wav"):
        path = out / name
        print(f"  {name:18s} {path.stat().st_size / 1024:8.1f} KiB")

    print(
        "\nEverything here was generated on this machine, so it is free of any\n"
        "third-party licence. Import demo-source.mp4 to try the whole workflow."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())