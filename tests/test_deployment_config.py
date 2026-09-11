from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LYRIC_ALIGN_REVISION = "d378b6c3e04ff7bf3a35b15bfd2b4db8a99c9e8c"


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_backend_image_uses_pinned_public_lyric_align_package():
    requirements = read("requirements-model.txt")
    dockerfile = read("Dockerfile.backend")

    assert f"lyric_align/archive/{LYRIC_ALIGN_REVISION}.tar.gz" in requirements
    assert "lyric-align[models]" in requirements
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in requirements
    assert "@main" not in requirements
    assert "git+" not in requirements
    assert "python -m pip wheel --wheel-dir /wheels -r requirements-model.txt" in dockerfile
    assert "python -m pip install --no-index --find-links=/wheels" in dockerfile
    assert "import lyric_align" in dockerfile
    assert "build-essential" in dockerfile
    assert "pkg-config" in dockerfile
    assert "libopus-dev" in dockerfile
    assert "COPY --from=wheel-builder /wheels /wheels" in dockerfile

    builder, runtime = dockerfile.split("FROM python:3.11-slim-bookworm", maxsplit=2)[1:]
    assert "pkg-config" in builder
    assert "libopus-dev" in builder
    assert "pkg-config" not in runtime
    assert "libopus-dev" not in runtime


def test_model_runtime_versions_are_constrained():
    constraints = read("constraints-model.txt")

    for package in ("demucs", "sudachipy", "sudachidict-core", "torch", "torchaudio", "transformers"):
        assert f"{package}==" in constraints
    assert "torch==2.11.0+cpu" in constraints
    assert "torchaudio==2.11.0+cpu" in constraints


def test_local_compose_mounts_models_read_only_and_forces_offline_sudachi():
    compose = read("docker-compose.yml")

    assert "${LYRIC_MODELS_DIR:-./docker-data/models}:/models:ro" in compose
    assert "LYRIC_G2P_BACKEND: sudachi" in compose
    assert 'HF_HUB_OFFLINE: "1"' in compose
    assert 'TRANSFORMERS_OFFLINE: "1"' in compose
    assert "LYRIC_DEMUCS_MODEL_PATH:" in compose
    assert "LYRIC_CTC_MODEL_PATH:" in compose


def test_formal_backend_example_requires_read_only_model_mount():
    compose = read("deploy/compose.backend.yml")
    environment = read("deploy/backend.env.example")

    assert "${LYRIC_MODELS_DIR:?Set the host model directory}:/models:ro" in compose
    assert "LYRIC_G2P_BACKEND: sudachi" in compose
    assert "LYRIC_MODELS_DIR=/var/lib/cloudmusic2ktv/models" in environment
    assert "LYRIC_DEMUCS_MODEL_PATH=/models/" in environment
    assert "LYRIC_CTC_MODEL_PATH=/models/" in environment


def test_unit_test_install_does_not_pull_model_runtime():
    development = read("requirements-dev.txt")

    assert "requirements-model" not in development
    assert "torch" not in development
    assert "lyric-align" not in development


def test_model_dependency_changes_trigger_container_workflow():
    workflow = read(".github/workflows/docker.yml")

    assert workflow.count('- "requirements*.txt"') == 2
    assert workflow.count('- "constraints*.txt"') == 2
    assert "requirements-model.txt" in workflow
    assert "constraints-model.txt" in workflow
