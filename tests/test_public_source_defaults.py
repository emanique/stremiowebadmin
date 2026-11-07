"""Public installer must never depend on the private DEV repository."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PUBLIC='https://github.com/emanique/stremiowebadmin'
PRIVATE='https://github.com/emanique/StremioServerWebAdmin'

def test_sources_reference_public_repository():
    for name in ('.env.example','compose.yaml','webadmin/app.py','webadmin/fork_update.py'):
        value=(ROOT/name).read_text()
        assert PUBLIC in value
        assert PRIVATE not in value

def test_admin_remains_on_loopback():
    assert 'WEBADMIN_BIND_IP=127.0.0.1' in (ROOT/'.env.example').read_text()
    assert 'auth_basic_user_file /etc/stremio-webadmin/webadmin.htpasswd;' in (ROOT/'scripts/configure-host-https.py').read_text()
