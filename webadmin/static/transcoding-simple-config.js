(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function yesNo(value) {
    return value ? 'VERIFIED' : 'NOT AVAILABLE';
  }

  function backendFrom(data) {
    const matrix = data.backendMatrix || {};
    const nvidia = matrix.nvenc || {};
    const vaapi = matrix.vaapi || {};

    if (nvidia.selectable) {
      return {
        vendor: 'NVIDIA',
        backend: 'NVENC',
        device: 'NVIDIA runtime',
        h264: !!nvidia.h264,
        hevc: !!nvidia.hevc,
        ready: !!nvidia.runtime,
      };
    }

    if (vaapi.selectable) {
      return {
        vendor: 'Intel / DRM',
        backend: 'VAAPI',
        device: vaapi.device || 'DRM render node',
        h264: !!vaapi.h264,
        hevc: !!vaapi.hevc,
        ready: !!vaapi.runtime,
      };
    }

    return {
      vendor: 'None',
      backend: 'CPU / Stremio default',
      device: '—',
      h264: false,
      hevc: false,
      ready: true,
    };
  }

  function ensurePanel() {
    if ($('simpleTranscodeAutoPanel')) return;

    const target =
      $('simpleTranscodeProfile')?.closest('.card') ||
      $('simpleTranscodeProfile')?.parentElement?.parentElement ||
      document.querySelector('[data-transcoding-simple]');

    if (!target) return;

    target.innerHTML = `
      <div id="simpleTranscodeAutoPanel">
        <h3>Video hardware acceleration</h3>

        <div class="simpleTranscodeGrid">
          <div class="simpleTranscodeField">
            <label>Mode</label>
            <div><b>Automatic</b></div>
          </div>

          <div class="simpleTranscodeField">
            <label>Detected GPU</label>
            <div id="autoGpuVendor">Detecting…</div>
          </div>

          <div class="simpleTranscodeField">
            <label>Backend</label>
            <div id="autoGpuBackend">Detecting…</div>
          </div>

          <div class="simpleTranscodeField">
            <label>Device</label>
            <div id="autoGpuDevice">Detecting…</div>
          </div>

          <div class="simpleTranscodeField">
            <label>Runtime</label>
            <div id="autoGpuRuntime">Detecting…</div>
          </div>

          <div class="simpleTranscodeField">
            <label>H.264 acceleration</label>
            <div id="autoGpuH264">Detecting…</div>
          </div>

          <div class="simpleTranscodeField">
            <label>HEVC acceleration</label>
            <div id="autoGpuHevc">Detecting…</div>
          </div>

          <div class="simpleTranscodeField">
            <label>Codec selection</label>
            <div>Stremio automatic</div>
          </div>

          <div class="simpleTranscodeField">
            <label>Direct stream</label>
            <div>Stremio automatic</div>
          </div>

          <div class="simpleTranscodeField">
            <label>Transcoding</label>
            <div>Stremio automatic</div>
          </div>
        </div>

        <div
          id="simpleTranscodeRule"
          style="margin-top: 10px; opacity: .8"
        >
          AUTO detects the available GPU backend. Stremio decides
          whether to copy or transcode and which codec to use.
        </div>
      </div>
    `;
  }

  function render(data) {
    ensurePanel();

    const hw = backendFrom(data);

    if ($('autoGpuVendor')) {
      $('autoGpuVendor').textContent = hw.vendor;
    }

    if ($('autoGpuBackend')) {
      $('autoGpuBackend').textContent = hw.backend;
    }

    if ($('autoGpuDevice')) {
      $('autoGpuDevice').textContent = hw.device;
    }

    if ($('autoGpuRuntime')) {
      $('autoGpuRuntime').textContent =
        hw.ready ? 'READY' : 'NOT READY';
    }

    if ($('autoGpuH264')) {
      $('autoGpuH264').textContent = yesNo(hw.h264);
    }

    if ($('autoGpuHevc')) {
      $('autoGpuHevc').textContent = yesNo(hw.hevc);
    }

    if ($('simpleTranscodeRule')) {
      $('simpleTranscodeRule').innerHTML =
        `<b>AUTO</b> · ${esc(hw.backend)} · ` +
        'Stremio controls copy/transcode/codec.';
    }
  }

  async function loadAuto() {
    ensurePanel();

    const endpoint = '/api/transcoding/profiles';

    try {
      const response = await fetch(endpoint, {
        cache: 'no-store',
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail || 'Hardware detection failed'
        );
      }

      render(data);
    } catch (error) {
      ensurePanel();

      if ($('simpleTranscodeRule')) {
        $('simpleTranscodeRule').textContent =
          `Hardware detection error: ${error.message}`;
      }
    }
  }

  function boot() {
    ensurePanel();
    loadAuto();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
