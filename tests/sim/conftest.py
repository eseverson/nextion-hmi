from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"


@pytest.fixture(scope="session")
def hmi_path() -> Path:
    assert HMI_PATH.exists(), f"missing reference HMI at {HMI_PATH}"
    return HMI_PATH
