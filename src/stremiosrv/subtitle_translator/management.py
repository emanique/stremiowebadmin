"""Local operator UI for manually selected subtitle files (development QA).

This is deliberately not presented as an automatic native Stremio subtitle hook.
"""
PAGE = r"""<!doctype html>
<html lang="pt-PT"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Subtitle Translator · gestão local</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif}
body{background:#101d16;color:#f5f3eb;margin:auto;padding:2rem;max-width:850px}
h1{font-size:1.6rem}fieldset{border:1px solid #426247;border-radius:10px;margin-top:1rem;padding:1.3rem}
label{display:block;margin-top:1rem}input,select,button{font:inherit;padding:.65rem;background:#192e22;color:white;border:1px solid #6d946b;border-radius:6px}
input,select{width:100%;box-sizing:border-box}button{cursor:pointer;margin-top:1rem}
small{color:#c4c8bd}#status{white-space:pre-wrap;background:#15261c;padding:1rem;border-radius:6px}
</style></head><body>
<h1>Subtitle Translator</h1>
<p>Motor local, destino português (Portugal). A legenda original não é alterada;
a tradução é reutilizada enquanto o título permanecer na cache principal.</p>
<p><small>Interface de desenvolvimento: selecciona-se um ficheiro SRT de origem.
A captura automática de uma legenda de outro addon ainda está em implementação.</small></p>
<fieldset><legend>Traduzir legenda seleccionada</legend>
<label for="title">Título na cache principal</label><select id="title"></select>
<label for="video">Identificador do vídeo no Stremio (filme ou episódio)</label>
<input id="video" placeholder="tt1234567 ou tt1234567:1:2" required maxlength="256">
<label for="language">Idioma da legenda original</label>
<select id="language">
<option value="en">Inglês</option><option value="es">Espanhol</option>
<option value="fr">Francês</option><option value="de">Alemão</option>
<option value="it">Italiano</option><option value="ru">Russo</option>
<option value="nl">Neerlandês</option><option value="pl">Polaco</option>
</select>
<label for="file">Ficheiro original SRT</label><input id="file" type="file" accept=".srt,text/plain">
<button id="submit" type="button">Traduzir e guardar</button>
</fieldset>
<h2>Estado</h2><pre id="status" role="status">A consultar modelos locais…</pre>
<script>
'use strict';
const $ = id => document.getElementById(id);
const root = location.pathname.slice(0, -'/manage'.length);
async function readJson(endpoint, init) {
  const response = await fetch(root + endpoint, {cache:'no-store', ...init});
  const payload = await response.json().catch(()=>({}));
  if (!response.ok) throw new Error(payload.detail || 'Erro HTTP '+response.status);
  return payload;
}
async function init() {
  try {
    const [titles, models] = await Promise.all([
      readJson('/titles.json'), readJson('/models.json')
    ]);
    $('title').replaceChildren();
    for (const item of titles.titles) {
      const option = document.createElement('option');
      option.value = item.infoHash; option.textContent = item.name;
      $('title').appendChild(option);
    }
    $('status').textContent =
      'Títulos disponíveis: ' + titles.titles.length + '\n' +
      'Motor Argos instalado: ' + (models.installed ? 'Sim' : 'Não') + '\n' +
      'Modelos disponíveis para português: ' + (models.sources.join(', ') || 'Nenhum') +
      '\nNota: os modelos genéricos pt podem não seguir a norma pt-PT.';
    for(const option of $('language').options) {
      option.disabled = !models.sources.includes(option.value);
    }
    const first = Array.from($('language').options).find(o => !o.disabled);
    if (first) $('language').value = first.value;
    $('submit').disabled = !first || !titles.titles.length;
  } catch (error) { $('status').textContent = error.message; $('submit').disabled = true; }
}
$('submit').addEventListener('click', async () => {
  const file = $('file').files[0];
  if(!file || !file.name.toLowerCase().endsWith('.srt') || file.size > 4*1024*1024) {
    $('status').textContent = 'Seleccione uma legenda SRT válida até 4 MiB.';return;
  }
  if (!$('video').value.trim()) {
    $('status').textContent = 'Indique o identificador do vídeo.';return;
  }
  $('submit').disabled = true;
  $('status').textContent = 'A traduzir…';
  try {
    const payload = {
      infoHash:$('title').value,
      videoId:$('video').value.trim(),
      sourceLanguage:$('language').value,
      text:await file.text()
    };
    const result = await readJson('/translate',{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)
    });
    $('status').textContent = result.cached
      ? 'Legenda já existente na cache. Sem nova tradução.'
      : 'Legenda traduzida e guardada na cache principal do título.';
  } catch (error) {
    $('status').textContent = 'Tradução falhou: ' + error.message;
  } finally { $('submit').disabled = false; }
});
init();
</script></body></html>
""";
