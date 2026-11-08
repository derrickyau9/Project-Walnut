import { installRetainedBrowser } from './retained.js';
import { RECIPE_FORMAT, MAX_RECIPE_BYTES, normalizeRecipe, parseRecipe, serializeRecipe } from './recipes.js';
const $ = (id) => document.getElementById(id);
let control = null;
let connected = false;
let serviceAvailable = false;
let stateOwner = null;
let workflowSubmitting = false;
let polling = null;
let inFlight = false;
let activeRequestId = null;
let previewRunning = false;
let previewGeneration = 0;
let previewTimer = null;
let previewObjectUrl = null;
const terminal = new Set(['complete', 'failed', 'cancelled', 'unknown']);
const savedKey = 'hasslfree-last-request-id';
const controlKey = 'hasslefree-console-control-v1';
const workflowKey = 'hasslefree-last-workflow-id';
let workflowTimer = null;
try {
  const saved = JSON.parse(sessionStorage.getItem(controlKey));
  if (saved && typeof saved.token === 'string' && Number.isInteger(saved.epoch)) control = saved;
  else sessionStorage.removeItem(controlKey);
} catch { sessionStorage.removeItem(controlKey); }
function clearControl() { control = null; sessionStorage.removeItem(controlKey); }

async function api(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', signal: AbortSignal.timeout(10000), ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  let body;
  try { body = await response.json(); } catch { throw new Error(`Invalid response (HTTP ${response.status})`); }
  if (!response.ok) {
    const error = new Error(typeof body.error === 'string' ? body.error : body.error?.message || JSON.stringify(body));
    error.code = body.error?.code;
    throw error;
  }
  return body;
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });
function say(message) { $('announcement').textContent = message; }
function showMessage(message) { $('result-message').textContent = message; say(message); }
function setText(id, value) { $(id).textContent = value === null || value === undefined || value === '' ? '—' : String(value); }
function busyControls() {
  $('acquire').disabled = !serviceAvailable || !!control;
  $('release').disabled = !serviceAvailable || !control;
  $('reconnect').disabled = !serviceAvailable || !control || connected;
  $('capture').disabled = !connected || !control || inFlight;
  $('cancel').disabled = !connected || !control || !$('lookup').value.trim();
  $('preview-start').disabled = !connected || !control || previewRunning;
  $('preview-stop').disabled = !connected || !control || !previewRunning;
  $('apply-settings').disabled = !connected || !control || inFlight;
  $('workflow-start').disabled = !connected || !!stateOwner || workflowSubmitting;
  $('capture-hint').textContent = control ? 'A capture is submitted once. Its request ID can be queried.' : 'Acquire control to enable capture.';
}
function hidePreview(message) {
  $('preview-image').hidden = true;
  $('preview-placeholder').hidden = false;
  $('preview-message').textContent = message;
  $('preview-frame').textContent = 'NO FRAME';
}
function setPreviewRunning(running) {
  if (running === previewRunning) return;
  previewRunning = running; previewGeneration += 1; clearTimeout(previewTimer);
  if (running) { hidePreview('Waiting for preview'); fetchPreview(previewGeneration); }
  else { hidePreview('Preview stopped'); if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl); previewObjectUrl = null; }
  busyControls();
}
async function fetchPreview(generation) {
  if (!previewRunning || generation !== previewGeneration) return;
  try {
    const response = await fetch('/api/v1/preview.png', { cache: 'no-store', signal: AbortSignal.timeout(3000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob = await response.blob();
    if (!previewRunning || generation !== previewGeneration) return;
    const next = URL.createObjectURL(blob);
    $('preview-image').src = next; $('preview-image').hidden = false; $('preview-placeholder').hidden = true;
    const frameTime = Number(response.headers.get('X-Preview-Timestamp'));
    $('preview-frame').textContent = `FRAME ${response.headers.get('X-Frame-Id') || '—'}${frameTime ? ` · ${new Date(frameTime * 1000).toLocaleTimeString()}` : ''}`;
    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = next;
  } catch {
    if (previewRunning && generation === previewGeneration) hidePreview('Preview paused');
  } finally {
    if (previewRunning && generation === previewGeneration) previewTimer = setTimeout(() => fetchPreview(generation), 200);
  }
}
function displayCapture(item) {
  if (!item) return;
  setText('request-id', item.request_id);
  setText('frame-id', item.frame_id);
  setText('requested-value', item.requested_exposure_us == null ? null : `${item.requested_exposure_us} µs`);
  setText('actual-value', item.actual_exposure_us == null ? null : `${item.actual_exposure_us} µs`);
  setText('requested-gain', item.requested_analogue_gain == null ? null : `${item.requested_analogue_gain}×`);
  setText('actual-gain', item.actual_analogue_gain == null ? null : `${item.actual_analogue_gain}×`);
  setText('file-path', item.file_path);
  $('capture-status').textContent = (item.status || 'unknown').toUpperCase();
  const messages = { submitting: 'Submitting request; acceptance has not yet been confirmed.', accepted: 'Request accepted; waiting for capture.', capturing: 'Capture in progress.', complete: 'Capture completed. The path below identifies the committed file.', failed: `Capture failed${item.error ? `: ${typeof item.error === 'string' ? item.error : JSON.stringify(item.error)}` : '.'}`, cancelled: 'Capture was cancelled before commit.', unknown: 'The outcome cannot be confirmed. Do not assume a capture failed or resend it automatically.' };
  showMessage(messages[item.status] || 'Capture status returned by the simulator.');
}
async function queryRequest(id, repeat = false) {
  if (!id) { showMessage('Enter a request ID to query.'); return; }
  if (id === activeRequestId) clearTimeout(polling);
  try {
    const item = await api(`/api/v1/captures/${encodeURIComponent(id)}`);
    displayCapture(item);
    if (repeat && !terminal.has(item.status)) {
      const timer = setTimeout(() => queryRequest(id, true), 1000);
      if (id === activeRequestId) polling = timer;
    } else if (id === activeRequestId) { inFlight = false; activeRequestId = null; busyControls(); }
  } catch (error) {
    if (id === activeRequestId) { inFlight = false; activeRequestId = null; busyControls(); }
    showMessage(`Unable to query this request: ${error.message}. Its outcome may be unknown; query the same ID again.`);
  }
}
async function refresh() {
  try {
    const state = await api('/api/v1/state');
    serviceAvailable = true;
    connected = !!state.connected;
    $('connection').className = 'connection online';
    $('connection').innerHTML = '<i></i>Service online';
    $('device-dot').className = `dot ${connected ? 'online' : 'offline'}`;
    setText('device-state', connected ? (state.camera_state || 'Online') : 'Disconnected');
    setText('owner', state.control?.owner || 'Unowned');
    stateOwner = state.control?.owner || null;
    $('epoch').textContent = state.control?.epoch == null ? 'Epoch —' : `Epoch ${state.control.epoch}`;
    setPreviewRunning(connected && !!state.preview?.running);
    if (control && (!state.control || state.control.epoch !== control.epoch)) clearControl();
  } catch {
    connected = false;
    serviceAvailable = false;
    $('connection').className = 'connection offline';
    $('connection').innerHTML = '<i></i>Offline';
    $('device-dot').className = 'dot offline';
    $('device-state').textContent = 'Service unavailable';
    setPreviewRunning(false);
    // Keep the token during a transient network outage. A subsequent state check validates its epoch.
  }
  busyControls();
}
async function heartbeat() {
  if (!control) return;
  const current = control;
  try {
    await post('/api/v1/control/heartbeat', { token: current.token, epoch: current.epoch });
  } catch (error) {
    if (control === current && error.code === 'control_denied') {
      clearControl(); busyControls();
      showMessage('Control lease expired or was revoked. Acquire control again.');
      await refresh();
    }
    // A transport failure alone cannot prove that control was revoked.
  }
}
async function loadCapabilities() {
  try {
    const capabilities = await api('/api/v1/capabilities');
    const list = $('capabilities'); list.replaceChildren();
    const values = capabilities.capabilities || capabilities.operations || [
      ...(capabilities.modes || []).map(mode => `${mode.id} · ${mode.width} × ${mode.height}`),
      ...(capabilities.output_formats || []).map(format => `Output · ${format.toUpperCase()}`),
      ...(capabilities.faults || []).map(fault => `Fault · ${fault}`)
    ];
    for (const value of Array.isArray(values) ? values : Object.keys(values)) {
      const li = document.createElement('li'); li.textContent = typeof value === 'string' ? value : (value.name || JSON.stringify(value)); list.appendChild(li);
    }
    if (!list.children.length) { const li = document.createElement('li'); li.textContent = 'No capability list returned'; list.appendChild(li); }
    const modes = capabilities.sensor_modes || capabilities.modes;
    if (Array.isArray(modes) && modes.length) {
      $('mode').replaceChildren();
      for (const mode of modes) { const option = document.createElement('option'); option.value = typeof mode === 'string' ? mode : mode.id; option.textContent = option.value; $('mode').appendChild(option); }
    }
  } catch { $('capabilities').replaceChildren(); const li = document.createElement('li'); li.textContent = 'Unavailable'; $('capabilities').appendChild(li); }
}
$('acquire').addEventListener('click', async () => {
  $('acquire').disabled = true;
  try {
    const result = await post('/api/v1/control/acquire', { owner: 'console' });
    control = { token: result.token, epoch: result.epoch };
    if (!control.token || control.epoch == null) { clearControl(); throw new Error('Service did not return a token and epoch'); }
    sessionStorage.setItem(controlKey, JSON.stringify(control));
    say('Control acquired.'); await refresh();
  } catch (error) { showMessage(`Could not acquire control: ${error.message}`); await refresh(); }
});
$('release').addEventListener('click', async () => {
  if (!control) return;
  $('release').disabled = true;
  try { await post('/api/v1/control/release', { token: control.token, epoch: control.epoch }); clearControl(); say('Control released.'); }
  catch (error) { showMessage(`Could not release control: ${error.message}`); }
  await refresh();
});
$('reconnect').addEventListener('click', async () => {
  if (!serviceAvailable || !control || connected) return;
  try { await post('/api/v1/device/reconnect', { token: control.token, epoch: control.epoch }); showMessage('Simulated device reconnected.'); }
  catch (error) { showMessage(`Could not reconnect simulated device: ${error.message}`); }
  await refresh();
});
$('apply-settings').addEventListener('click', async () => {
  if (!control || !$('capture-form').reportValidity()) return;
  try {
    await post('/api/v1/configure', { token: control.token, epoch: control.epoch, settings: { sensor_mode: $('mode').value, requested_exposure_us: Number($('exposure').value), requested_analogue_gain: Number($('gain').value), output_format: 'dng' } });
    showMessage('Settings applied to the simulated preview.');
  } catch (error) { showMessage(`Could not apply settings: ${error.message}`); }
});
for (const action of ['start', 'stop']) {
  $(`preview-${action}`).addEventListener('click', async () => {
    if (!control) return;
    try { await post(`/api/v1/preview/${action}`, { token: control.token, epoch: control.epoch }); setPreviewRunning(action === 'start'); }
    catch (error) { showMessage(`Could not ${action} preview: ${error.message}`); }
    await refresh();
  });
}
$('capture-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!control || !connected) return;
  const id = crypto.randomUUID();
  clearTimeout(polling); inFlight = true; activeRequestId = id;
  if (previewRunning) { previewGeneration += 1; clearTimeout(previewTimer); hidePreview('Preview paused'); }
  localStorage.setItem(savedKey, id); $('lookup').value = id;
  $('capture').disabled = true;
  displayCapture({ request_id: id, status: 'submitting', requested_exposure_us: Number($('exposure').value), requested_analogue_gain: Number($('gain').value) });
  try {
    const result = await post('/api/v1/captures', { request_id: id, token: control.token, control_epoch: control.epoch, sensor_mode: $('mode').value, requested_exposure_us: Number($('exposure').value), requested_analogue_gain: Number($('gain').value), output_format: 'dng', fault: $('fault').value || null });
    displayCapture(result);
    if (!terminal.has(result.status)) queryRequest(id, true);
    else { inFlight = false; activeRequestId = null; }
  } catch (error) {
    inFlight = false; activeRequestId = null;
    showMessage(`Capture response was not confirmed: ${error.message}. Query request ${id} before deciding what to do next.`);
    $('capture-status').textContent = 'UNCONFIRMED';
  } finally {
    if (previewRunning) { clearTimeout(previewTimer); previewTimer = setTimeout(() => fetchPreview(previewGeneration), 200); }
    busyControls(); refresh();
  }
});
$('query').addEventListener('click', () => queryRequest($('lookup').value.trim(), true));
$('lookup').addEventListener('input', busyControls);
$('cancel').addEventListener('click', async () => {
  const id = $('lookup').value.trim();
  if (!control || !id) return;
  try { await post(`/api/v1/captures/${encodeURIComponent(id)}/cancel`, { token: control.token, epoch: control.epoch }); showMessage('Cancellation requested; checking the recorded status.'); await queryRequest(id, true); }
  catch (error) { showMessage(`Cancellation could not be confirmed: ${error.message}. Query the same request ID.`); }
});
function displayWorkflow(job) {
  $('workflow-status').textContent = String(job.status || 'unknown').toUpperCase();
  $('workflow-message').textContent = job.error ? `Workflow reported: ${typeof job.error === 'string' ? job.error : JSON.stringify(job.error)}` : 'Inspect the job record below for step status and capture IDs.';
  $('workflow-details').textContent = JSON.stringify(job, null, 2);
}
async function queryWorkflow(id, repeat = false) {
  if (!id) { $('workflow-message').textContent = 'Enter a workflow job ID.'; return; }
  clearTimeout(workflowTimer);
  try {
    const job = await api(`/api/v1/workflows/${encodeURIComponent(id)}`);
    displayWorkflow(job);
    if (repeat && !['complete', 'completed', 'failed', 'cancelled', 'unknown', 'paused'].includes(job.status)) workflowTimer = setTimeout(() => queryWorkflow(id, true), 1000);
  } catch (error) { $('workflow-message').textContent = `Could not query workflow: ${error.message}. Query this same ID again.`; }
}
function recipeFromControls() {
  const raw = $('workflow-exposures').value.split(',').map(value => value.trim());
  if (raw.some(value => !/^\d+$/.test(value))) throw new Error('Use comma-separated integer exposures.');
  const kind = $('workflow-kind').value;
  const plan = { kind, exposures_us: raw.map(Number), sensor_mode: $('mode').value,
    requested_analogue_gain: Number($('gain').value), output_format: 'dng' };
  if (kind === 'interval') plan.interval_ms = Number($('workflow-interval').value);
  return normalizeRecipe({ format: RECIPE_FORMAT, name: $('recipe-name').value.trim(), plan });
}
function applyRecipe(recipe) {
  $('recipe-name').value = recipe.name;
  $('workflow-kind').value = recipe.plan.kind || 'bracket';
  $('workflow-exposures').value = recipe.plan.exposures_us.join(', ');
  $('workflow-interval').value = String(recipe.plan.interval_ms ?? 1000);
  $('mode').value = recipe.plan.sensor_mode;
  $('gain').value = String(recipe.plan.requested_analogue_gain);
}
$('recipe-import').addEventListener('click', () => $('recipe-file').click());
$('recipe-file').addEventListener('change', async () => {
  const file = $('recipe-file').files?.[0];
  if (!file) return;
  try {
    if (file.size > MAX_RECIPE_BYTES) throw new Error('Recipe exceeds 64 KiB.');
    const recipe = parseRecipe(await file.text()); applyRecipe(recipe);
    $('recipe-message').textContent = 'Recipe loaded into settings. No job was started; Start sequence creates a new job ID.';
  } catch (error) { $('recipe-message').textContent = `Recipe was not imported: ${error.message}`; }
  finally { $('recipe-file').value = ''; }
});
$('recipe-export').addEventListener('click', () => {
  try {
    const recipe = recipeFromControls(); const text = serializeRecipe(recipe);
    const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url;
    anchor.download = (recipe.name.replace(/[^A-Za-z0-9_-]+/g, '-').slice(0, 50) || 'workflow-recipe') + '.json';
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    $('recipe-message').textContent = 'Recipe exported. It contains no token, job ID, results, or file paths.';
  } catch (error) { $('recipe-message').textContent = `Recipe was not exported: ${error.message}`; }
});
let workflowListLoading = false;
async function refreshWorkflowList() {
  if (workflowListLoading) return;
  workflowListLoading = true; $('workflow-list-refresh').disabled = true;
  try {
    const jobs = await api('/api/v1/workflows');
    if (!Array.isArray(jobs) || jobs.length > 100) throw new Error('Invalid bounded workflow list.');
    const select = $('workflow-recent'); const previous = select.value; select.replaceChildren();
    for (const job of jobs) {
      if (typeof job.job_id !== 'string' || !/^[A-Za-z0-9._-]{1,100}$/.test(job.job_id) || typeof job.status !== 'string') throw new Error('Invalid workflow identity.');
      const option = document.createElement('option'); option.value = job.job_id; option.textContent = `${job.job_id} - ${job.status}`; select.appendChild(option);
    }
    if (!select.options.length) { const option = document.createElement('option'); option.value = ''; option.textContent = 'No retained jobs'; select.appendChild(option); }
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
  } catch (error) { $('workflow-message').textContent = `Recent jobs unavailable: ${error.message}. A known job ID can still be queried.`; }
  finally { workflowListLoading = false; $('workflow-list-refresh').disabled = false; }
}
$('workflow-list-refresh').addEventListener('click', refreshWorkflowList);
$('workflow-list-open').addEventListener('click', () => {
  const id = $('workflow-recent').value;
  if (id) { $('workflow-id').value = id; queryWorkflow(id, true); }
});
$('workflow-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!connected) { $('workflow-message').textContent = 'Connect to the simulator first.'; return; }
  if (control) { $('workflow-message').textContent = 'Release console control before starting a workflow.'; return; }
  let recipe;
  try { recipe = recipeFromControls(); }
  catch (error) { $('workflow-message').textContent = error.message; return; }
  const id = crypto.randomUUID(); const plan = recipe.plan;
  localStorage.setItem(workflowKey, id); $('workflow-id').value = id;
  clearTimeout(workflowTimer); workflowSubmitting = true; busyControls();
  $('workflow-status').textContent = 'SUBMITTING'; $('workflow-message').textContent = 'Submitting job; acceptance has not yet been confirmed.';
  try { const job = await post('/api/v1/workflows', { job_id: id, plan }); displayWorkflow(job); queryWorkflow(id, true); refreshWorkflowList(); }
  catch (error) { $('workflow-status').textContent = 'UNCONFIRMED'; $('workflow-message').textContent = `Job response not confirmed: ${error.message}. Query the same job ID before deciding what to do next.`; }
  finally { workflowSubmitting = false; await refresh(); }
});
$('workflow-query').addEventListener('click', () => queryWorkflow($('workflow-id').value.trim(), true));
for (const action of ['pause', 'resume', 'cancel']) {
  $(`workflow-${action}`).addEventListener('click', async () => {
    const id = $('workflow-id').value.trim();
    if (!id) { $('workflow-message').textContent = 'Enter a workflow job ID.'; return; }
    try { displayWorkflow(await post(`/api/v1/workflows/${encodeURIComponent(id)}/${action}`, {})); queryWorkflow(id, true); }
    catch (error) { $('workflow-message').textContent = `Could not ${action} workflow: ${error.message}. Query the same job ID.`; }
  });
}
$('workflow-id').value = localStorage.getItem(workflowKey) || '';
$('lookup').value = localStorage.getItem(savedKey) || '';
installRetainedBrowser(); refresh(); loadCapabilities(); refreshWorkflowList(); setInterval(refresh, 2500); setInterval(heartbeat, 10000);
