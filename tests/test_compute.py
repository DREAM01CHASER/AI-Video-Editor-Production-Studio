"""Checks for the GPU/CPU decision that sits behind local transcription.

None of these need a GPU. The decision is plain data, so a machine without NVIDIA
hardware can still prove three things: that a usable GPU is selected, that a GPU
which *cannot* actually be used is not selected, and that the reason is stated
rather than swallowed. The one part that does need hardware — a real CUDA
transcription — is reported separately in the milestone notes instead of being
asserted here, because a green test would otherwise prove nothing about the GPU.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import compute as compute_module
from app import transcription as transcription_module
from app.compute import (
    GPU_INSTALL_COMMAND,
    compute_diagnostics,
    enable_runtime_libraries,
    library_subdirectory,
    missing_runtime_libraries,
    nvidia_library_directories,
    runtime_libraries,
)
from app.config import Settings
from app.main import create_app
from app.transcription import TranscriptionService, build_transcript_document


# ------------------------------------------------------------------- fixtures


def _fake_site_packages(root: Path) -> Path:
    """A site-packages tree holding the directories the GPU wheels install."""

    site = root / "site-packages"
    for package in ("cublas", "cudnn", "cuda_runtime"):
        (site / "nvidia" / package / "bin").mkdir(parents=True)
    # A package with no binary directory, and a loose file, must be ignored.
    (site / "nvidia" / "cufft").mkdir(parents=True)
    (site / "nvidia" / "cudnn" / "LICENSE").write_text("x", encoding="utf-8")
    return site


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        project_root=Path(__file__).resolve().parents[1],
        workspace_dir=tmp_path / "workspace",
        max_upload_size_bytes=1024,
        **overrides,
    )


# ----------------------------------------------------------- runtime lookup


def test_runtime_libraries_differ_by_platform() -> None:
    assert "cublas64_12.dll" in runtime_libraries("win32")
    assert "cudnn64_9.dll" in runtime_libraries("win32")
    assert "libcublas.so.12" in runtime_libraries("linux")
    # Nothing is claimed for a platform whose runtime is not published.
    assert runtime_libraries("darwin") == ()


def test_library_subdirectory_is_bin_on_windows_and_lib_elsewhere() -> None:
    assert library_subdirectory("win32") == "bin"
    assert library_subdirectory("linux") == "lib"


def test_nvidia_library_directories_finds_only_existing_binary_directories(
    tmp_path: Path,
) -> None:
    site = _fake_site_packages(tmp_path)

    found = nvidia_library_directories(site, platform="win32")

    names = [path.parent.name for path in found]
    assert names == ["cuda_runtime", "cublas", "cudnn"]
    assert all(path.name == "bin" and path.is_dir() for path in found)


def test_nvidia_library_directories_is_empty_when_the_wheels_are_absent(
    tmp_path: Path,
) -> None:
    """A plain `pip install -r requirements.txt` must not look half-configured."""

    assert nvidia_library_directories(tmp_path / "nope", platform="win32") == []


def test_missing_runtime_libraries_reports_the_names_that_will_not_load() -> None:
    missing = missing_runtime_libraries(("a-library-that-does-not-exist.dll",))

    assert missing == ["a-library-that-does-not-exist.dll"]


def test_enable_runtime_libraries_adds_the_environment_directories_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wheel directories are added to the search path exactly once.

    Windows looks in the DLL directories and in ``PATH`` — CTranslate2's own C
    code resolves from ``PATH`` — so both are updated, and calling this again must
    not keep growing ``PATH``.
    """

    site = _fake_site_packages(tmp_path)

    monkeypatch.setattr(compute_module, "_ENABLED_DIRECTORIES", [])
    monkeypatch.setattr(compute_module, "_DLL_DIRECTORY_HANDLES", [])
    monkeypatch.setenv("PATH", "/usr/bin")

    first = enable_runtime_libraries(site)
    second = enable_runtime_libraries(site)

    assert first == second
    assert len(first) == 3
    for directory in first:
        assert directory in os.environ["PATH"]

    segments = os.environ["PATH"].split(os.pathsep)
    assert len(segments) == len(set(segments))

    # Leave the process exactly as it was found.
    monkeypatch.setattr(compute_module, "_ENABLED_DIRECTORIES", [])
    monkeypatch.setattr(compute_module, "_DLL_DIRECTORY_HANDLES", [])


# --------------------------------------------------------------- diagnostics


def test_diagnostics_without_a_visible_device_report_a_cpu_run() -> None:
    diagnostics = compute_diagnostics(device_count=0)

    assert diagnostics["usable"] is False
    assert diagnostics["gpu_visible"] is False
    assert diagnostics["cuda_runtime_ready"] is True
    assert "CPU" in diagnostics["summary"]
    # No GPU at all is not a problem to fix, so there is nothing to install.
    assert diagnostics["remedy"] is None


def test_diagnostics_report_a_visible_gpu_whose_runtime_is_missing() -> None:
    """The state this project actually hit: CTranslate2 saw the card, the runtime was gone."""

    diagnostics = compute_diagnostics(
        device_count=1, missing_libraries=("cublas64_12.dll", "cudnn64_9.dll")
    )

    assert diagnostics["gpu_visible"] is True
    assert diagnostics["cuda_runtime_ready"] is False
    assert diagnostics["usable"] is False
    assert diagnostics["missing_libraries"] == ["cublas64_12.dll", "cudnn64_9.dll"]
    assert "cublas64_12.dll" in diagnostics["summary"]
    assert GPU_INSTALL_COMMAND in str(diagnostics["remedy"])
    assert diagnostics["install_command"] == GPU_INSTALL_COMMAND


def test_diagnostics_report_a_ready_gpu() -> None:
    diagnostics = compute_diagnostics(device_count=1)

    assert diagnostics["usable"] is True
    assert "float16" in diagnostics["summary"]
    assert diagnostics["remedy"] is None


# ------------------------------------------------------------- model loading


def test_a_usable_gpu_is_selected_for_transcription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transcription_module, "cuda_device_count", lambda: 1)
    monkeypatch.setattr(
        transcription_module,
        "inspect_compute",
        lambda count: compute_diagnostics(device_count=count),
    )
    monkeypatch.setattr(transcription_module, "_CUDA_RUNTIME_BROKEN", False)

    service = TranscriptionService(_settings(tmp_path))
    built: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service,
        "_build_model",
        lambda device, compute_type: built.append((device, compute_type)) or object(),
    )

    loaded = service.load_model()

    assert built == [("cuda", "float16")]
    assert loaded.device == "cuda"
    assert loaded.compute_type == "float16"
    assert loaded.diagnostics["usable"] is True


def test_a_gpu_without_its_runtime_falls_back_to_cpu_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A visible GPU is not attempted when its runtime cannot be loaded.

    Building a CUDA model in that state does not raise a clean error — CTranslate2
    initialises and tears the context down later, which stalled transcription — so
    the check happens up front and the reason is recorded.
    """

    monkeypatch.setattr(transcription_module, "cuda_device_count", lambda: 1)
    monkeypatch.setattr(
        transcription_module,
        "inspect_compute",
        lambda count: compute_diagnostics(
            device_count=count, missing_libraries=("cublas64_12.dll",)
        ),
    )
    monkeypatch.setattr(transcription_module, "_CUDA_RUNTIME_BROKEN", False)

    service = TranscriptionService(_settings(tmp_path))
    built: list[str] = []
    monkeypatch.setattr(
        service, "_build_model", lambda device, compute_type: built.append(device) or object()
    )

    loaded = service.load_model()

    assert built == ["cpu"]
    assert loaded.device == "cpu"
    assert loaded.compute_type == "int8"
    assert any(GPU_INSTALL_COMMAND in note for note in loaded.notes), loaded.notes
    assert loaded.diagnostics["missing_libraries"] == ["cublas64_12.dll"]


def test_compute_plan_reports_the_device_before_a_job_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transcription_module, "cuda_device_count", lambda: 1)
    monkeypatch.setattr(
        transcription_module,
        "inspect_compute",
        lambda count: compute_diagnostics(
            device_count=count, missing_libraries=("cudnn64_9.dll",)
        ),
    )
    monkeypatch.setattr(transcription_module, "_CUDA_RUNTIME_BROKEN", False)

    plan = TranscriptionService(_settings(tmp_path)).compute_plan()

    assert plan["device"] == "cpu"
    assert plan["compute_type"] == "int8"
    assert plan["configured_device"] == "auto"
    assert plan["usable"] is False
    assert plan["missing_libraries"] == ["cudnn64_9.dll"]


def test_the_transcript_records_why_the_cpu_was_used(tmp_path: Path) -> None:
    diagnostics = compute_diagnostics(
        device_count=1, missing_libraries=("cublas64_12.dll",)
    )

    document = build_transcript_document(
        project_id="abc123",
        source_video="clip.mp4",
        segments=[],
        info=type("Info", (), {"language": "en", "language_probability": 0.9})(),
        model="medium",
        device="cpu",
        compute_type="int8",
        duration=10.0,
        elapsed_seconds=1.0,
        requested_word_timestamps=True,
        compute=diagnostics,
    )

    assert document["compute"]["usable"] is False
    assert document["compute"]["missing_libraries"] == ["cublas64_12.dll"]


def test_health_endpoint_reports_the_device_and_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The device choice has to be visible before a job runs, not only after."""

    monkeypatch.setattr(transcription_module, "cuda_device_count", lambda: 0)

    client = TestClient(create_app(_settings(tmp_path)))
    payload = client.get("/api/health").json()

    compute = payload["transcription"]["compute"]
    assert compute["device"] == "cpu"
    assert compute["usable"] is False
    assert "CPU" in compute["summary"]
    assert compute["model"] == payload["transcription"]["model"]


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="The CUDA runtime wheels are not published for macOS, so there is nothing to detect.",
)
def test_this_machine_reports_its_cuda_runtime_honestly() -> None:
    """Whatever this machine has, the report must match the libraries it can load.

    This is the check that would have caught the real bug: CTranslate2 reported one
    CUDA device while ``cublas64_12.dll`` could not be loaded, so transcription ran
    on the CPU with nothing in the UI saying why.
    """

    from app.transcription import cuda_device_count

    diagnostics = compute_module.inspect_compute(cuda_device_count())

    assert diagnostics["device_count"] == cuda_device_count()
    # A GPU counts as usable only when a device is visible *and* every runtime
    # library it needs actually loads.
    assert diagnostics["usable"] == (
        bool(diagnostics["gpu_visible"]) and not diagnostics["missing_libraries"]
    )
    if diagnostics["gpu_visible"] and not diagnostics["usable"]:
        # A visible but unusable GPU must always explain itself.
        assert diagnostics["missing_libraries"]
        assert diagnostics["remedy"]
