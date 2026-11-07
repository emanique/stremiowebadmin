(() => {
  const style = document.createElement('style');
  style.textContent = `
    .addonConnect{margin-top:20px;padding-top:18px;border-top:1px solid rgba(232,207,105,.22)}
    .addonSelect{width:100%;margin:6px 0 12px;padding:10px 11px;border:1px solid rgba(229,198,74,.3);border-radius:8px;background:rgba(13,30,21,.95);color:var(--text)}
    .addonMeta{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 10px}
    .addonMeta .badge{padding:3px 8px}
    .addonDescription{color:var(--muted);font-size:11px;margin:0 0 12px}
    .addonResources{color:#f4d35e;font-size:10px;text-transform:uppercase;letter-spacing:.05em;margin:-4px 0 12px}
    .addonActions{display:flex;gap:8px;flex-wrap:wrap;margin-top:-7px;align-items:center}
    .addonActions a{text-decoration:none;display:inline-block}
    .addonActions button{font-size:11px}
    .addonUpdateInfo{margin:10px 0 12px;padding:10px 11px;border:1px solid rgba(232,207,105,.18);border-radius:8px;background:rgba(13,30,21,.55);font-size:11px;color:var(--muted)}
    .addonUpdateInfo b{color:var(--text)}
    .addonDeleteHint{margin-top:8px;color:var(--muted);font-size:10px}
    .addonUnavailable{padding:11px 12px;border:1px dashed rgba(232,207,105,.28);border-radius:8px;color:var(--muted);font-size:11px}
  `;
  document.head.appendChild(style);

  const host = document.querySelector('#dashboard .connect > div:first-child');
  if (!host || document.getElementById('addonConnect')) return;

  const box = document.createElement('div');
  box.id = 'addonConnect';
  box.className = 'addonConnect';
  box.innerHTML = `
    <div class="eyebrow">Addons available on this server</div>
    <select id="addonSelect" class="addonSelect">
      <option value="">Select an addon…</option>
    </select>
    <div id="addonDetails" class="addonUnavailable">Loading addons…</div>
  `;
  host.appendChild(box);

  const select = document.getElementById('addonSelect');
  const details = document.getElementById('addonDetails');
  let addons = [];

  function escapeHtml(value) {
    const node = document.createElement('div');
    node.textContent = String(value ?? '');
    return node.innerHTML;
  }

  function escapeAttr(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('"', '&quot;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;');
  }

  function versionLine(update) {
    if (!update || (!update.installed && !update.available)) return 'Runtime version unavailable';
    const installed = update.installed || 'unknown';
    const available = update.available || 'unknown';
    return `Runtime: ${installed} · GitHub: ${available}`;
  }

  function updateButton(addon) {
    const update = addon.update || {};
    if (!update.endpoint) return '';
    if (update.running) {
      return '<button class="mini" type="button" disabled>Updating…</button>';
    }
    if (update.updateAvailable === true) {
      return `<button class="mini" type="button" data-addon-update="${escapeAttr(addon.id)}">Update addon</button>`;
    }
    if (update.updateAvailable === false) {
      return '<button class="mini" type="button" disabled>Addon up to date</button>';
    }
    return `<button class="mini" type="button" data-addon-check="${escapeAttr(addon.id)}">Check updates</button>`;
  }

  function render() {
    const addon = addons.find(item => item.id === select.value);
    if (!addon) {
      details.className = 'addonUnavailable';
      details.textContent = addons.length ? 'Select an addon to show its Stremio installation link.' : 'No addons reported by this server.';
      return;
    }

    const state = addon.ready ? 'Ready' : addon.enabled ? 'HTTPS required' : 'Unavailable';
    const resources = Array.isArray(addon.resources) && addon.resources.length
      ? addon.resources.join(' · ')
      : 'No resources reported';

    if (!addon.manifestUrl) {
      details.className = 'addonUnavailable';
      details.innerHTML = `<strong>${escapeHtml(addon.name)}</strong><br>${escapeHtml(addon.status || state)}`;
      return;
    }

    details.className = '';
    details.innerHTML = `
      <div class="addonMeta">
        <strong>${escapeHtml(addon.name)}</strong>
        <span class="badge">${escapeHtml(state)}</span>
        ${addon.version ? `<small>addon v${escapeHtml(addon.version)}</small>` : ''}
      </div>
      <div class="addonDescription">${escapeHtml(addon.description || '')}</div>
      <div class="addonResources">${escapeHtml(resources)}</div>
      <div class="eyebrow">Stremio addon manifest</div>
      <div class="field">
        <code id="addonManifestUrl">${escapeHtml(addon.manifestUrl)}</code>
        <button class="copy" data-copy="addonManifestUrl" title="Copy addon URL">⧉</button>
      </div>
      <div class="hint">Copy this URL into Stremio → Addons → Add addon.</div>
      <div class="addonUpdateInfo">
        <b>Addon updates</b><br>
        ${escapeHtml(versionLine(addon.update))}<br>
        ${escapeHtml((addon.update && addon.update.message) || 'Update status unavailable.')}
      </div>
      <div class="addonActions">
        ${addon.managementUrl ? `<a class="mini" href="${escapeAttr(addon.managementUrl)}" target="_blank" rel="noopener">Manage / Delete titles</a>` : ''}
        ${updateButton(addon)}
        <button class="mini" type="button" data-addon-check="${escapeAttr(addon.id)}">Refresh status</button>
      </div>
      ${addon.deleteSupported ? '<div class="addonDeleteHint">Delete cached movies and episodes from the Library management page. Stremio addon cards themselves do not expose destructive action buttons.</div>' : ''}
    `;
  }

  async function loadAddons(preferredId = select.value) {
    details.className = 'addonUnavailable';
    details.textContent = 'Loading addons…';
    try {
      const response = await fetch('/api/addons', { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.json();
      addons = Array.isArray(body.addons) ? body.addons : [];
      select.innerHTML = '<option value="">Select an addon…</option>' + addons.map(addon => {
        const suffix = addon.enabled ? '' : ' (unavailable)';
        return `<option value="${escapeAttr(addon.id)}">${escapeHtml(addon.name)}${suffix}</option>`;
      }).join('');
      if (preferredId && addons.some(addon => addon.id === preferredId)) select.value = preferredId;
      render();
    } catch (error) {
      addons = [];
      select.innerHTML = '<option value="">Select an addon…</option>';
      details.textContent = 'Could not load addon information from WebAdmin.';
    }
  }

  async function updateAddon(id) {
    const addon = addons.find(item => item.id === id);
    const endpoint = addon && addon.update && addon.update.endpoint;
    if (!endpoint) return;
    const button = details.querySelector(`[data-addon-update="${CSS.escape(id)}"]`);
    if (button) { button.disabled = true; button.textContent = 'Starting update…'; }
    try {
      const response = await fetch(endpoint, { method: 'POST' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || body.message || `HTTP ${response.status}`);
      details.querySelector('.addonUpdateInfo').innerHTML = `<b>Addon updates</b><br>${escapeHtml(body.message || 'Update started.')}`;
      setTimeout(() => loadAddons(id), 1800);
    } catch (error) {
      if (button) { button.disabled = false; button.textContent = 'Update addon'; }
      const info = details.querySelector('.addonUpdateInfo');
      if (info) info.innerHTML = `<b>Addon updates</b><br>${escapeHtml(error.message || 'Update failed.')}`;
    }
  }

  select.addEventListener('change', render);
  box.addEventListener('click', event => {
    const update = event.target.closest('[data-addon-update]');
    if (update) { updateAddon(update.dataset.addonUpdate); return; }
    const check = event.target.closest('[data-addon-check]');
    if (check) loadAddons(check.dataset.addonCheck);
  });
  loadAddons();
})();