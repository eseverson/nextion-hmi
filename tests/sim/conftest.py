from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HMI_PATH = REPO_ROOT / "source" / "nextion.hmi.HMI"
TFT_PATH = REPO_ROOT / "source" / "nextion.hmi.tft"


@pytest.fixture(scope="session")
def hmi_path() -> Path:
    assert HMI_PATH.exists(), f"missing reference HMI at {HMI_PATH}"
    return HMI_PATH


@pytest.fixture(scope="session")
def tft_path() -> Path:
    assert TFT_PATH.exists(), f"missing reference TFT at {TFT_PATH}"
    return TFT_PATH
