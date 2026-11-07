(() => {
  const esc = value => {
    const node = document.createElement('div');
    node.textContent = value == null ? '' : String(value);
    return node.innerHTML;
  };

  const style = document.createElement('style');
  style.textContent = `
    .transcodePanel .inner{display:grid;gap:14px}
    .transcodeCards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
    .transcodeCard{padding:12px;border:1px solid rgba(232,207,105,.25);border-radius:10px;background:rgba(13,30,21,.95)}
    .transcodeCard span{display:block;color:var(--muted);font-size:10px;text-transform:uppercase}
    .transcodeCard b{display:block;margin-top:5px;font-size:15px;overflow-wrap:anywhere}
    .transcodeSummary{padding:12px 14px;border:1px solid rgba(242,201,76,.25);border-radius:10px;background:rgba(4,18,10,.75);color:#f7edc1}
    .transcodeHw{display:flex;gap:7px;flex-wrap:wrap}
    .transcodeChip{border:1px solid #52613b;border-radius:99px;padding:4px 9px;color:var(--muted);font-size:10px}
    .transcodeChip.on{border-color:#3da864;color:var(--green);background:#123f25}
    .transcodeChip.warn{border-color:#a98735;color:#f4d35e;background:#473c16}
    .transcodeSessions{display:grid;gap:8px}
    .transcodeSession{padding:12px;border:1px solid rgba(232,207,105,.2);border-radius:9px;background:rgba(12,25,18,.92)}
    .transcodeSessionHead{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
    .transcodeSessionHead strong{font-size:13px}
    .transcodeSessionGrid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin-top:9px}
    .transcodeKv{padding:8px;border-radius:7px;background:rgba(28,49,34,.75)}
    .transcodeKv span{display:block;color:var(--muted);font-size:9px;text-transform:uppercase}
    .transcodeKv b{display:block;margin-top:3px;font-size:11px;overflow-wrap:anywhere}
    .transcodeLatest{display:grid;grid-template-columns:1fr 1fr;gap:10px}
    .transcodeLatest>div{padding:10px;border:1px solid rgba(232,207,105,.18);border-radius:8px;background:rgba(7,18,11,.72)}
    .transcodeLatest small{display:block;color:var(--muted);margin-bottom:4px}
    .transcodeLatest code{color:#fff7d6;white-space:normal;overflow-wrap:anywhere}
    .transcodePanel.idle .transcodeSummary,.transcodePanel.idle .transcodeLatest,.transcodePanel.idle .transcodeSessions{display:none}\n    .transcodePanel.idle .inner{gap:9px}\n    .transcodePanel.idle .transcodeCard{padding:9px 10px}\n    .transcodePanel.idle .transcodeHw{margin-top:1px}\n    .transcodePolicyIntro{margin:0 12px 12px;padding:14px;border:1px solid rgba(242,201,76,.28);border-radius:10px;background:linear-gradient(110deg,rgba(17,72,39,.82),rgba(67,21,25,.75))}
    .transcodePolicyIntro strong{display:block;color:#f4d35e;margin-bottom:5px}
    .transcodePolicyIntro p{margin:0 0 10px;color:#e6dfbd;font-size:12px}
    .transcodePolicyFlow{display:flex;gap:7px;align-items:center;flex-wrap:wrap;color:var(--muted);font-size:11px}
    .transcodePolicyFlow b{color:#fff7d6}
    .configItem.transcodingPolicy{box-shadow:inset 3px 0 0 rgba(242,201,76,.58)}
    .configItem select{width:100%;min-width:0;background:#24212f;border:1px solid #3a3547;color:var(--text);border-radius:7px;padding:9px}
    @media(max-width:1100px){.transcodeSessionGrid{grid-template-columns:repeat(3,minmax(0,1fr))}}\n    @media(max-width:900px){.transcodeCards,.transcodeSessionGrid{grid-template-columns:1fr 1fr}.transcodeLatest{grid-template-columns:1fr}}
    @media(max-width:540px){.transcodeCards,.transcodeSessionGrid{grid-template-columns:1fr}}
  `;
  document.head.appendChild(style);

  function installDashboardPanel() {
    if (document.getElementById('transcodingPanel')) return;
    const stacks = document.querySelectorAll('#dashboard > .stack');
    if (stacks.length < 2) return;
    const panel = document.createElement('article');
    panel.className = 'panel transcodePanel';
    panel.id = 'transcodingPanel';
    panel.innerHTML = `
      <div class="title row"><span>TRANSCODING POLICY &amp; ACCELERATION</span><span class="badge" id="tdPolicyState">Loading…</span></div>
      <div class="inner">
        <div class="transcodeCards">
          <div class="transcodeCard"><span>Policy mode</span><b id="tdMode">—</b></div>
          <div class="transcodeCard"><span>Active FFmpeg</span><b id="tdActive">—</b></div>
          <div class="transcodeCard"><span>Active engine</span><b id="tdEngine">—</b></div>
          <div class="transcodeCard"><span>Encoder load</span><b id="tdEncoderLoad">—</b></div>
        </div>
        <div class="transcodeSummary" id="tdSummary">Loading effective transcoding policy…</div>
        <div class="transcodeLatest">
          <div><small>Requested → effective</small><code id="tdRequestedEffective">—</code></div>
          <div><small>Actual runtime</small><code id="tdActualRuntime">—</code></div>
        </div>
        <div class="transcodeHw" id="tdHardware"></div>
        <div class="transcodeSessions" id="tdSessions"><div class="empty">No active FFmpeg sessions</div></div>
        <div class="transcodeLatest">
          <div><small>Latest policy decision</small><code id="tdDecision">—</code></div>
          <div><small>Latest FFmpeg progress</small><code id="tdProgress">—</code></div>
        </div>
      </div>
      <div class="foot">Live per-session monitor: Direct Stream vs transcoding, source → target codecs, VAAPI/NVENC/CPU engine, PID, CPU/RAM usage and FFmpeg progress.</div>`;
    const settings = stacks[1].querySelector('.panel.settings');
    stacks[1].insertBefore(panel, settings || null);
  }

  function chip(label, enabled, warning = false) {
    return `<span class="transcodeChip ${enabled ? 'on' : warning ? 'warn' : ''}">${esc(label)} · ${enabled ? 'ready' : warning ? 'missing' : 'no'}</span>`;
  }

  function sessionHtml(session, index) {
    const action = session.action === 'direct-stream' ? 'DIRECT STREAM' : session.action === 'transcoding' ? 'TRANSCODING' : 'FFMPEG';
    const progress = session.progress || {};
    const video = `${session.sourceVideo || '?'} → ${session.targetVideo || '?'}`;
    const audio = `${session.sourceAudio || '?'} → ${session.targetAudio || '?'}`;
    const speed = progress.speed || '—';
    const fps = progress.fps == null ? '—' : `${progress.fps.toFixed(1)} fps`;
    const resources = session.cpuPercent == null ? '—' : `CPU ${Number(session.cpuPercent).toFixed(1)}% · MEM ${Number(session.memoryPercent || 0).toFixed(1)}%`;
    const process = session.pid == null ? '—' : `PID ${session.pid}${session.elapsed ? ' · ' + session.elapsed : ''}`;
    return `<div class="transcodeSession">
      <div class="transcodeSessionHead"><strong>${session.jobId ? `Job ${esc(session.jobId)}` : `FFmpeg session ${index + 1}`}</strong><span class="badge">${action}</span></div>
      <div class="transcodeSessionGrid">
        <div class="transcodeKv"><span>Video</span><b>${esc(video)}</b></div>
        <div class="transcodeKv"><span>Audio</span><b>${esc(audio)}</b></div>
        <div class="transcodeKv"><span>Engine</span><b>${esc((session.engine || 'none').toUpperCase())}</b></div>
        <div class="transcodeKv"><span>Progress</span><b>${esc(fps)} · ${esc(speed)}</b></div>
        <div class="transcodeKv"><span>Process</span><b>${esc(process)}</b></div>
        <div class="transcodeKv"><span>Resources</span><b>${esc(resources)}</b></div>
      </div>
      ${session.policyDecision ? `<div class="transcodeKv" style="margin-top:8px"><span>Policy decision</span><b>${esc(session.policyDecision)}</b></div>` : ''}
    </div>`;
  }

  function renderTranscoding(data, playback) {
    if (!document.getElementById('transcodingPanel')) installDashboardPanel();
    if (!data || !data.available) {
      document.getElementById('tdPolicyState').textContent = 'Unavailable';
      document.getElementById('tdSummary').textContent = data?.message || 'Transcoding telemetry is unavailable.';
      return;
    }
    const policy = data.policy || {};
    const active = data.active || {};
    const hw = data.hardware || {};
    const sessions = active.sessions || [];
    const registryAvailable = !!(playback && Array.isArray(playback.active));
    const playbackRows = registryAvailable ? playback.active : [];
    const activeWorkloads = new Set(playbackRows.map(p => p.workloadId || p.jobId).filter(Boolean));
    const liveSessions = registryAvailable
      ? sessions.filter(session => activeWorkloads.has(session.jobId))
      : sessions;
    const waitingForGc = registryAvailable && playbackRows.length === 0 && sessions.length > 0;
    const liveEngines = [...new Set(liveSessions.map(session => session.engine).filter(Boolean))];
    const liveTranscoding = liveSessions.filter(session => session.action === 'transcoding').length;
    const liveDirect = liveSessions.filter(session => session.action === 'direct-stream').length;
    const panel = document.getElementById('transcodingPanel');
    if (panel) panel.classList.toggle('idle', liveSessions.length === 0);
    document.getElementById('tdPolicyState').textContent = `● ${(policy.transcoding_mode || 'auto').toUpperCase()}`;
    document.getElementById('tdMode').textContent = `${String(policy.transcoding_mode || 'auto').toUpperCase()} · ${String(policy.transcoding_hwaccel || 'auto').toUpperCase()}`;
    document.getElementById('tdActive').textContent = `${liveSessions.length} · ${liveTranscoding} transcode · ${liveDirect} direct`;
    document.getElementById('tdEngine').textContent = liveEngines.length ? liveEngines.map(x => x.toUpperCase()).join(' + ') : 'Idle';
    const utilisation = hw.encoderUtilizationPercent;
    if (utilisation != null) {
      document.getElementById('tdEncoderLoad').textContent = `${Number(utilisation).toFixed(1)}% · NVENC`;
    } else if (liveTranscoding > 0 && liveEngines.includes('vaapi')) {
      document.getElementById('tdEncoderLoad').textContent = 'Active · VAAPI';
    } else if (liveTranscoding > 0) {
      document.getElementById('tdEncoderLoad').textContent = 'Active · no HW metric';
    } else {
      document.getElementById('tdEncoderLoad').textContent = 'Idle';
    }
    document.getElementById('tdSummary').textContent = data.policySummary || '—';
    const state = data.state || {};
    const requested = state.requested || {};
    const effective = state.effective || {};
    const actual = state.actual || {};
    const requestedEffective = document.getElementById('tdRequestedEffective');
    const actualRuntime = document.getElementById('tdActualRuntime');
    if (requestedEffective) requestedEffective.textContent =
      `${String(requested.mode || 'auto').toUpperCase()} / ${String(requested.hwaccel || 'auto').toUpperCase()} / ${requested.videoCodec || '—'} → ${effective.runtimeReady ? 'READY' : 'NOT READY'}`;
    if (actualRuntime) actualRuntime.textContent = waitingForGc
      ? `WAITING FOR GC · ${sessions.length} FFmpeg process(es)`
      : liveSessions.length
        ? `${liveEngines.map(x => String(x).toUpperCase()).join(' + ') || 'FFMPEG'} · ${liveSessions.length} active session(s)`
        : 'IDLE · no active playback';
    document.getElementById('tdHardware').innerHTML = [
      chip(`VAAPI ${hw.vaapiDevice || ''}`, Boolean(hw.vaapiDevicePresent && (hw.h264Vaapi || hw.hevcVaapi)), !hw.vaapiDevicePresent),
      chip('H.264 VAAPI', Boolean(hw.h264Vaapi)),
      chip('HEVC VAAPI', Boolean(hw.hevcVaapi)),
      chip('H.264 NVENC', Boolean(hw.h264Nvenc)),
      chip('HEVC NVENC', Boolean(hw.hevcNvenc)),
      chip('libx264 fallback', Boolean(hw.libx264))
    ].join('');
    document.getElementById('tdSessions').innerHTML = liveSessions.length ? liveSessions.map(sessionHtml).join('') : '<div class="empty">No active playback transcoding sessions</div>';
    document.getElementById('tdDecision').textContent = data.latestDecision?.decision || 'No policy decision recorded yet';
    const progress = data.latestProgress;
    document.getElementById('tdProgress').textContent = progress
      ? `${progress.fps == null ? '—' : progress.fps.toFixed(1) + ' fps'} · ${progress.bitrate || '—'} · speed ${progress.speed || '—'}`
      : 'No FFmpeg progress recorded yet';
  }

  async function loadTranscoding() {
    try {
      const [response, statusResponse] = await Promise.all([
        fetch('/api/transcoding/status', {cache: 'no-store'}),
        fetch('/api/status', {cache: 'no-store'}).catch(() => null)
      ]);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      let playback = null;
      if (statusResponse && statusResponse.ok) {
        try {
          const status = await statusResponse.json();
          playback = status.playback;
        } catch (error) {}
      }
      renderTranscoding(data, playback);
    } catch (error) {
      renderTranscoding({available: false, message: 'Could not read transcoding telemetry.'});
    }
  }

  function installConfigurationControls() {
    if (typeof configGroups === 'undefined' || typeof configInput === 'undefined') return;

    const index = configGroups.findIndex(group => group.id === 'transcode');
    if (index >= 0) {
      configGroups.splice(index, 1,
        {id: 'transcoding-policy', title: 'Automatic transcoding & hardware', match: name => name.startsWith('transcoding_')},
        {id: 'transcode-runtime', title: 'Transcoding runtime & lifecycle', match: name => name.startsWith('transcode_')}
      );
    }

    const autoManaged = new Set([
      'transcoding_profile',
      'transcoding_resolved_profile',
      'transcoding_mode',
      'transcoding_hwaccel',
      'transcoding_vaapi_device',
      'transcoding_video_codec',
      'transcoding_hw_decode',
      'transcoding_fallback_codec'
    ]);

    const baseConfigInput = configInput;
    configInput = function(item) {
      if (autoManaged.has(item.name)) {
        let value = item.value;
        if (item.name === 'transcoding_profile' || item.name === 'transcoding_mode' || item.name === 'transcoding_hwaccel') {
          value = 'auto';
        } else if (item.name === 'transcoding_video_codec' || item.name === 'transcoding_resolved_profile') {
          value = 'Stremio automatic';
        }
        return `<input data-config="${esc(item.name)}" data-scale="1" type="text" value="${esc(value ?? 'Automatic')}" disabled>`;
      }
      if (item.name === 'transcoding_video_quality') {
        return `<input data-config="${esc(item.name)}" data-scale="1" type="number" min="0" max="51" step="1" value="${esc(item.displayValue ?? item.value ?? 22)}" ${item.editable?'':'disabled'}>`;
      }
      return baseConfigInput(item);
    };

    const baseRenderConfig = renderConfig;
    renderConfig = function() {
      baseRenderConfig();
      decorateTranscodingConfig();
    };
    const search = document.getElementById('configSearch');
    if (search) search.addEventListener('input', () => queueMicrotask(decorateTranscodingConfig));
  }

  function decorateTranscodingConfig() {
    const groups = [...document.querySelectorAll('.configGroup')];
    const policyGroup = groups.find(group => group.querySelector('summary')?.textContent.includes('Automatic transcoding & hardware'));
    if (!policyGroup) return;

    policyGroup.querySelectorAll('.configItem').forEach(card => card.classList.add('transcodingPolicy'));
    if (!policyGroup.querySelector('.transcodePolicyIntro')) {
      const intro = document.createElement('div');
      intro.className = 'transcodePolicyIntro';
      intro.innerHTML = `<strong>AUTO-only transcoding</strong><p>Hardware detection exposes VAAPI/NVENC capabilities to the runtime. Stremio remains authoritative for Direct Stream, transcoding and codec selection.</p><div class="transcodePolicyFlow"><b>Stremio decision</b> → copy or transcode <span>·</span> <b>AUTO backend</b> → available GPU acceleration <span>·</span> <b>No valid GPU</b> → Stremio/CPU default</div>`;
      const grid = policyGroup.querySelector('.configGroupGrid');
      policyGroup.insertBefore(intro, grid || null);
    }
  }

  installDashboardPanel();
  installConfigurationControls();
  loadTranscoding();
  setInterval(loadTranscoding, 5000);
})();
