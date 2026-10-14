"""Which compute device transcription can really use, and why it cannot.

CTranslate2 answers ``get_cuda_device_count()`` from the driver alone, so a machine
with an NVIDIA GPU always *looks* ready. A CUDA model additionally needs the CUDA
runtime it was built against — cuBLAS and cuDNN 9 — and those are separate DLLs
that ``pip install -r requirements.txt`` does not install. Even when the GPU
wheels are installed they land *inside* the virtualenv, in
``Lib/site-packages/nvidia/<package>/bin``, instead of on ``PATH``, so on Windows
the loader still cannot find them.

That combination — a visible GPU, a runtime that cannot be loaded, and no error —
is exactly the state in which Whisper quietly runs on the CPU on a machine that
has a perfectly good GPU. This module owns the two things that fix it: making the
runtime loadable, and describing what is missing in plain language, so the
transcription service can pick a device it can really use and say why when it
cannot.
"""

from __future__ import annotations

import ctypes
import os
import sys
import sysconfig
from pathlib import Path
from typing import Sequence

#: CTranslate2 4.x is built against CUDA 12 and cuDNN 9. These are the libraries
#: that have to be loadable before a CUDA model can be created.
RUNTIME_LIBRARIES_BY_PLATFORM: dict[str, tuple[str, ...]] = {
    "win32": ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll"),
    "linux": ("libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9"),
}

#: Directory names inside the ``nvidia`` namespace package that carry a runtime.
NVIDIA_PACKAGES = (
    "cuda_runtime",
    "cublas",
    "cudnn",
    "cufft",
    "curand",
)

#: The one-line install that adds them, quoted in every message that needs it.
GPU_REQUIREMENTS_FILE = "requirements-gpu.txt"
GPU_INSTALL_COMMAND = f"python -m pip install -r {GPU_REQUIREMENTS_FILE}"


def runtime_libraries(platform: str | None = None) -> tuple[str, ...]:
    """The CUDA runtime libraries that must be loadable on this platform."""

    return RUNTIME_LIBRARIES_BY_PLATFORM.get(platform or sys.platform, ())


def library_subdirectory(platform: str | None = None) -> str:
    """Where a wheel keeps its binaries: ``bin`` on Windows, ``lib`` elsewhere."""

    return "bin" if (platform or sys.platform) == "win32" else "lib"


def site_packages_directory() -> Path:
    """The virtualenv's site-packages, which is where the GPU wheels install."""

    return Path(sysconfig.get_paths()["purelib"])


def nvidia_library_directories(
    site_packages: Path | None = None, *, platform: str | None = None
) -> list[Path]:
    """Existing ``nvidia/*/bin`` directories inside this environment.

    Nothing is guessed about a system-wide CUDA toolkit: the point is to find the
    runtime that ``pip install -r requirements-gpu.txt`` put inside the virtualenv,
    because that is the copy the application can rely on being there.
    """

    root = (site_packages or site_packages_directory()) / "nvidia"
    leaf = library_subdirectory(platform)

    found: list[Path] = []
    for package in NVIDIA_PACKAGES:
        directory = root / package / leaf
        if directory.is_dir():
            found.append(directory)
    return found


#: ``os.add_dll_directory`` returns a handle that has to stay referenced for as
#: long as the directory should be searched, so they are held here.
_DLL_DIRECTORY_HANDLES: list[object] = []
_ENABLED_DIRECTORIES: list[str] = []


def enable_runtime_libraries(site_packages: Path | None = None) -> list[str]:
    """Make the environment's CUDA runtime loadable by the process.

    Each wheel directory is added to the DLL search path and to ``PATH``, because
    one is used by the Windows loader and the other by libraries that resolve
    their own dependencies. Calling this twice is harmless: the directories are
    added once and the same list is returned.
    """

    if _ENABLED_DIRECTORIES:
        return list(_ENABLED_DIRECTORIES)

    directories = nvidia_library_directories(site_packages)
    added: list[str] = []

    for directory in directories:
        text = str(directory)
        try:
            if hasattr(os, "add_dll_directory"):
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(text))
        except OSError:
            # A missing or unreadable directory is not fatal: the library probe
            # reports the real problem a moment later.
            continue
        # CTranslate2 is loaded by C code that does its own lookup, so the plain
        # PATH entry matters as well as the DLL-directory handle.
        os.environ["PATH"] = text + os.pathsep + os.environ.get("PATH", "")
        added.append(text)

    _ENABLED_DIRECTORIES.extend(added)
    return list(_ENABLED_DIRECTORIES)


def missing_runtime_libraries(required: Sequence[str] | None = None) -> list[str]:
    """The CUDA runtime libraries this process cannot load right now.

    A visible device only proves the driver is present. Loading the libraries is
    the honest test, because a CUDA model cannot be created without them.
    """

    missing: list[str] = []
    for name in required if required is not None else runtime_libraries():
        try:
            ctypes.CDLL(name)
        except OSError:
            missing.append(name)
    return missing


def compute_diagnostics(
    *,
    device_count: int,
    missing_libraries: Sequence[str] = (),
    library_directories: Sequence[str] = (),
) -> dict[str, object]:
    """Describe the compute device transcription will use, and why.

    Pure: it takes the facts it reports on, so the wording can be tested without a
    GPU, a driver, or a CUDA install.
    """

    count = max(0, int(device_count))
    missing = [str(name) for name in missing_libraries]
    visible = count > 0
    runtime_ready = not missing
    usable = visible and runtime_ready

    if not visible:
        summary = (
            "No CUDA device is visible to CTranslate2, so transcription runs on "
            "CPU/int8."
        )
        remedy = None
    elif not runtime_ready:
        summary = (
            f"An NVIDIA GPU is visible ({count} device), but the CUDA runtime it "
            f"needs is not loadable ({', '.join(missing)}), so transcription runs "
            "on CPU/int8 instead."
        )
        remedy = (
            f"Install the GPU runtime into this environment: {GPU_INSTALL_COMMAND} "
            "(needs an NVIDIA driver; see the Windows CUDA setup notes in README.md)."
        )
    else:
        summary = (
            f"CUDA is ready ({count} device): transcription can run on the GPU with "
            "float16."
        )
        remedy = None

    return {
        "device_count": count,
        "gpu_visible": visible,
        "cuda_runtime_ready": runtime_ready,
        "usable": usable,
        "missing_libraries": missing,
        "library_directories": [str(item) for item in library_directories],
        "install_command": GPU_INSTALL_COMMAND,
        "summary": summary,
        "remedy": remedy,
    }


def inspect_compute(device_count: int) -> dict[str, object]:
    """Look at this machine's GPU setup and report what it means.

    The runtime directories are added first, so a machine where the GPU wheels are
    installed reports the GPU as usable rather than as "missing libraries".
    """

    directories = enable_runtime_libraries()
    return compute_diagnostics(
        device_count=device_count,
        missing_libraries=missing_runtime_libraries(),
        library_directories=directories,
    )
