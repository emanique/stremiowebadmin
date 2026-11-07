from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "webadmin" / "static" / "index.html"


def _function(script: str, name: str) -> str:
    marker = f"async function {name}"
    start = script.index(marker)
    brace = script.index("{", start)
    depth = 0
    for pos in range(brace, len(script)):
        char = script[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return script[start : pos + 1]
    raise AssertionError(f"unterminated JavaScript function {name}")


def _inline_script() -> str:
    html = INDEX.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
    assert scripts
    return scripts[-1]


def test_configuration_buttons_are_explicit_and_wired():
    html = INDEX.read_text(encoding="utf-8")
    script = _inline_script()
    assert 'id="saveAll" type="button"' in html
    assert 'id="restart" type="button"' in html
    assert "$('saveAll').addEventListener('click',saveAllConfiguration)" in script
    assert "$('restart').addEventListener('click',restartFromConfiguration)" in script
    assert "fetch('api/config',{method:'PUT'" in script
    assert "fetch('api/restart',{method:'POST'})" in script


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_configuration_button_actions_execute_real_frontend_handlers(tmp_path: Path):
    """Execute the same JS functions used by the buttons against a tiny fake DOM.

    This catches the regression class where backend curl tests pass but the browser button is not
    wired, throws before issuing fetch(), or remains disabled after the action.
    """
    script = _inline_script()
    functions = "\n".join(
        _function(script, name)
        for name in ("waitForRestart", "restartServer", "saveAllConfiguration", "restartFromConfiguration")
    )
    node = tmp_path / "ui-actions-test.cjs"
    node.write_text(
        f"""
const assert = require('assert');
const calls = [];
const events = [];
const elements = {{
  saveAll: {{disabled:false}},
  restart: {{disabled:false}},
  configState: {{textContent:''}}
}};
const configInput = {{
  dataset: {{config:'max_streams', scale:'1'}},
  type:'number',
  value:'4',
  disabled:false
}};
global.document = {{
  querySelectorAll: (selector) => selector.includes('[data-config]') ? [configInput] : []
}};
global.$ = (id) => elements[id];
global.toast = (message) => events.push(['toast', message]);
global.loadConfig = async () => events.push(['loadConfig']);
global.load = async () => events.push(['load']);
global.waitForServerHealthy = async () => true;
let confirmValue = false;
global.confirm = () => confirmValue;
global.fetch = async (url, options={{}}) => {{
  calls.push({{url, method:options.method || 'GET', body:options.body || null}});
  if (url === 'api/config') {{
    return {{ok:true,status:200,json:async()=>({{ok:true,restartRequired:false}})}};
  }}
  if (url === 'api/restart' && options.method === 'POST') {{
    return {{ok:true,status:202,json:async()=>({{ok:true,accepted:true}})}};
  }}
  if (url === 'api/restart') {{
    return {{ok:true,status:200,json:async()=>({{status:'succeeded',detail:'server restarted and configuration reloaded'}})}};
  }}
  if (url === 'api/status') {{
    return {{ok:true,status:200,json:async()=>({{server:{{healthy:true}}}})}};
  }}
  throw new Error('unexpected fetch '+url);
}};
let restartPromise = null;
{functions}

(async () => {{
  const saved = await saveAllConfiguration();
  assert.strictEqual(saved, true);
  const saveCall = calls.find(x => x.url === 'api/config');
  assert(saveCall, 'Save button did not call api/config');
  assert.strictEqual(saveCall.method, 'PUT');
  assert.deepStrictEqual(JSON.parse(saveCall.body), {{values:{{max_streams:4}}}});
  assert.strictEqual(elements.saveAll.disabled, false);
  assert.strictEqual(elements.configState.textContent, 'Saved and applied.');

  confirmValue = true;
  const restarted = await restartFromConfiguration();
  assert.strictEqual(restarted, true);
  const restartCall = calls.find(x => x.url === 'api/restart');
  assert(restartCall, 'Restart button did not call api/restart');
  assert.strictEqual(restartCall.method, 'POST');
  assert.strictEqual(elements.restart.disabled, false);
  assert.strictEqual(
    elements.configState.textContent,
    'Server restarted. Saved configuration is active.'
  );

  process.stdout.write(JSON.stringify({{calls,events}}));
}})().catch(err => {{ console.error(err); process.exit(1); }});
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", str(node)],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    data = json.loads(proc.stdout)
    assert any(call["url"] == "api/config" for call in data["calls"])
    assert any(call["url"] == "api/restart" for call in data["calls"])


def test_webadmin_index_is_never_cached_across_upgrades():
    source = (ROOT / "webadmin" / "app.py").read_text(encoding="utf-8")
    assert '"Cache-Control": "no-store, must-revalidate"' in source
    assert '"Pragma": "no-cache"' in source
