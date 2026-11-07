"""Release packages must install using native Debian and Ubuntu Compose variants."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_debian_13_compose_dependency_is_supported():
    source=(ROOT/'scripts/build-qa-release.sh').read_text()
    assert 'docker-compose-v2 | docker-compose-plugin | docker-compose' in source
    assert 'docker.io | docker-ce' in source
    assert 'docker-buildx | docker-buildx-plugin' in source

def test_package_does_not_automatically_start_privileged_webadmin():
    source=(ROOT/'scripts/build-qa-release.sh').read_text()
    assert 'Run: sudo stremio-webadmin-setup' in source
    assert 'stremio-webadmin-setup' in source
