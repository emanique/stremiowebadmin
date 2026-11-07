"""A public source clone must self-provision the supported host dependencies."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT/'packaging/stremio-webadmin-setup.sh'
BOOTSTRAP = ROOT/'packaging/stremio-webadmin-prerequisites.sh'


def test_setup_runs_prerequisites_before_creating_state():
    setup=SETUP.read_text()
    assert 'sh "$ROOT/packaging/stremio-webadmin-prerequisites.sh"' in setup
    assert setup.index('stremio-webadmin-prerequisites.sh"') < setup.index('mkdir -p /var/lib/stremio-webadmin')
    assert 'Missing prerequisite: $executable' not in setup


def test_bootstrap_covers_required_packages_and_distro():
    source=BOOTSTRAP.read_text()
    for package in ('docker.io','nginx','openssl','iproute2','python3',
                    'docker-compose-v2','docker-compose-plugin','docker-compose','docker-buildx','docker-buildx-plugin'):
        assert package in source
    assert 'apt-get update' in source
    assert 'apt-get install -y --no-install-recommends' in source
    assert 'STREMIO_SKIP_DEP_INSTALL' in source
    assert 'debian|ubuntu' in source
    assert 'systemctl enable --now docker' in source
    assert 'docker compose version' in source
    assert 'docker buildx version' in source


def test_setup_and_bootstrap_are_portable_sh():
    for path in (SETUP,BOOTSTRAP):
        subprocess.run(['sh','-n',str(path)],check=True)


def test_readme_contains_copyable_clone_command():
    text=(ROOT/'README.md').read_text()
    assert 'https://github.com/emanique/stremiowebadmin.git' in text
    assert 'sudo sh /opt/stremio-server-webadmin/packaging/stremio-webadmin-setup.sh\n```' in text
