/* Bounded review/download of committed captures; never changes capture controls. */
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const positive = value => typeof value === 'number' && Number.isFinite(value) && value > 0;

export function validateCapturePage(page) {
  if (!page || page.order !== 'newest_first' || page.limit !== 20 || !Array.isArray(page.items) || page.items.length > 20 || (page.next_cursor !== null && !uuid(page.next_cursor))) throw new Error('Invalid bounded capture page.');
  const seen = new Set();
  const items = page.items.map(item => {
    const dims = item?.sensor_mode === 'sim-small' ? [128, 96] : item?.sensor_mode === 'sim-full' ? [6064, 4040] : null;
    if (!uuid(item?.request_id) || seen.has(item.request_id) || item.capture_id !== item.request_id || item.frame_id !== item.request_id || item.status !== 'complete' || item.filename !== item.request_id + '.dng' || !dims || item.width !== dims[0] || item.height !== dims[1] || item.bit_depth !== 14 || item.cfa !== 'RGGB' || item.simulated !== true || !Number.isInteger(item.actual_exposure_us) || item.actual_exposure_us <= 0 || item.actual_exposure_us > 0xffffffff || !positive(item.actual_analogue_gain)) throw new Error('Invalid committed capture identity or metadata.');
    seen.add(item.request_id);
    return Object.fromEntries(['request_id', 'filename', 'sensor_mode', 'width', 'height', 'actual_exposure_us', 'actual_analogue_gain'].map(key => [key, item[key]]));
  });
  return { items, next_cursor: page.next_cursor };
}

async function bytes(path, limit) {
  const response = await fetch(path, { cache: 'no-store', signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error(`Asset request failed (HTTP ${response.status}).`);
  const declared = Number(response.headers.get('Content-Length'));
  if (Number.isFinite(declared) && declared > limit) { await response.body?.cancel(); throw new Error('Asset response exceeds its bound.'); }
  const reader = response.body.getReader(); const chunks = []; let length = 0;
  try {
    while (true) {
      const part = await reader.read(); if (part.done) break;
      length += part.value.length;
      if (length > limit) { await reader.cancel(); throw new Error('Asset response exceeds its bound.'); }
      chunks.push(part.value);
    }
  } finally { reader.releaseLock(); }
  const data = new Uint8Array(length); let offset = 0;
  for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.length; }
  return data;
}

export function installRetainedBrowser() {
  const $ = id => document.getElementById(id);
  let loading = false; let next = null; let items = []; let selected = null; let imageUrl = null;
  function message(value) { $('library-message').textContent = value; }
  function controls() {
    $('library-refresh').disabled = loading;
    $('library-next').disabled = loading || !next;
    for (const button of $('library-items').querySelectorAll('button')) button.disabled = loading;
    $('library-download').hidden = !selected || loading;
  }
  function clearReview() {
    selected = null;
    if (imageUrl) URL.revokeObjectURL(imageUrl); imageUrl = null;
    $('library-image').hidden = true; $('library-image').removeAttribute('src');
    $('library-selected').textContent = 'Select a committed capture to review its retained DNG.';
    $('library-download').removeAttribute('href'); controls();
  }
  async function select(item) {
    if (loading || !items.some(value => value.request_id === item.request_id)) return;
    clearReview(); selected = item; loading = true; controls();
    $('library-selected').textContent = `${item.filename} | ${item.width} x ${item.height} | ${item.actual_exposure_us} us | ${item.actual_analogue_gain.toFixed(2)}x`;
    for (const button of $('library-items').querySelectorAll('button')) button.setAttribute('aria-pressed', String(button.dataset.requestId === item.request_id));
    message('Reading the retained DNG display conversion...');
    try {
      const data = await bytes(`/api/v1/captures/${encodeURIComponent(item.request_id)}/thumbnail.png`, 256 * 1024);
      const header = new DataView(data.buffer, data.byteOffset, data.byteLength);
      if (data.length < 33 || data[0] !== 137 || String.fromCharCode(...data.slice(1, 8)) !== 'PNG\r\n\x1a\n' || String.fromCharCode(...data.slice(12, 16)) !== 'IHDR' || header.getUint32(8) !== 13 || header.getUint32(16) < 1 || header.getUint32(16) > 320 || header.getUint32(20) < 1 || header.getUint32(20) > 240) throw new Error('Invalid bounded DNG display conversion.');
      imageUrl = URL.createObjectURL(new Blob([data], { type: 'image/png' }));
      $('library-image').src = imageUrl; await $('library-image').decode(); $('library-image').hidden = false;
      message('Retained DNG display conversion: synthetic and uncalibrated. This is not live preview.');
    } catch (error) {
      if (imageUrl) URL.revokeObjectURL(imageUrl); imageUrl = null;
      $('library-image').hidden = true; $('library-image').removeAttribute('src');
      message(`Review unavailable: ${error.message} The capture request is unchanged.`);
    }
    finally {
      loading = false;
      $('library-download').href = `/api/v1/captures/${encodeURIComponent(item.request_id)}/file.dng`;
      $('library-download').download = item.filename;
      controls();
    }
  }
  async function load(cursor = null) {
    if (loading) return;
    loading = true; controls(); message('Loading committed capture history...');
    try {
      const query = '?limit=20' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : '');
      const data = await bytes('/api/v1/captures' + query, 128 * 1024);
      const page = validateCapturePage(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(data)));
      items = page.items; next = page.next_cursor; clearReview();
      $('library-items').replaceChildren();
      for (const item of items) {
        const li = document.createElement('li'); const button = document.createElement('button');
        button.type = 'button'; button.className = 'secondary'; button.dataset.requestId = item.request_id;
        button.textContent = `${item.filename} - ${item.actual_exposure_us} us / ${item.actual_analogue_gain.toFixed(2)}x`;
        button.setAttribute('aria-pressed', 'false'); button.addEventListener('click', () => select(item));
        li.appendChild(button); $('library-items').appendChild(li);
      }
      if (!items.length) { const li = document.createElement('li'); li.textContent = 'No committed captures.'; $('library-items').appendChild(li); }
      message(`Showing ${items.length} committed captures, newest first. Unknown and failed requests remain available through original-ID query.`);
    } catch (error) { message(`Capture history unavailable: ${error.message}`); }
    finally { loading = false; controls(); }
  }
  $('library-refresh').addEventListener('click', () => load(null));
  $('library-next').addEventListener('click', () => load(next));
  addEventListener('pagehide', () => { if (imageUrl) URL.revokeObjectURL(imageUrl); });
  load(null);
  return { refresh: () => load(null) };
}
