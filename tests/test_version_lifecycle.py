import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBADMIN = ROOT / "webadmin"
sys.path.insert(0, str(WEBADMIN))

# version_lifecycle imports package_update; normal test environment provides its deps.
import version_lifecycle as v


def test_prerelease_does_not_advertise_older_channel_version():
    c = v._component("3.0.10-dev", "3.0.4")
    assert c["available"] is None
    assert c["updateAvailable"] is None
    assert c["versionState"] == "unknown"


def test_prerelease_can_see_newer_channel_version():
    c = v._component("3.0.10-dev", "3.0.11")
    assert c["available"] == "3.0.11"
    assert c["updateAvailable"] is True
    assert c["versionState"] == "update-available"


def test_release_still_reports_installed_newer():
    c = v._component("3.0.10", "3.0.4")
    assert c["available"] == "3.0.4"
    assert c["updateAvailable"] is False
    assert c["versionState"] == "installed-newer"
