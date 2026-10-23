// Recorder page: capture 16 kHz PCM, upload, place 1 s label boxes, save clips.
// Also serves /review/<recording_id>: loads an existing original with its
// already-saved clips (read-only, dashed) and lets the user add new boxes.
(() => {
  'use strict';

  const TARGET_SR = 16000;
  const CLIP_SEC = 1.0;
  const SNAP_SEC = 0.05;
  const WARN_SEC = 600;          // 10 min RAM warning
  const CANVAS_HEIGHT = 200;
  const MIN_PX_PER_SEC = 120;    // keeps a 1 s box wide enough for its controls
  const RECENT_COUNT = 10;
  const WORKLET_URL = '/static/worklet.js';

  const { labels, colors, defaultLabel, reviewId, piMicAvailable } = window.APP;
  const SOURCE_KEY = 'voicecollector.source';
  const PI_POLL_MS = 200;
  const { fmt, toast, confirmDialog, apiJson } = window.UI;
  const $ = (id) => document.getElementById(id);

  const els = {
    error: $('error'), errorTitle: $('error-title'), errorDetail: $('error-detail'),
    recent: $('recent'),
    defaultLabel: $('default-label'), name: $('rec-name'),
    recordBtn: $('record-btn'), timerBox: $('timer-box'), timer: $('timer'),
    status: $('status'), longWarning: $('long-warning'),
    editor: $('editor'), editorTitle: $('editor-title'), editorLoading: $('editor-loading'),
    recInfo: $('rec-info'), playheadTime: $('playhead-time'),
    boxCount: $('box-count'), savedCount: $('saved-count'),
    wave: $('wave'),
    addBoxBtn: $('add-box-btn'), playAllBtn: $('play-all-btn'),
    discardBtn: $('discard-btn'), deleteAllBtn: $('delete-all-btn'),
    newRecordingBtn: $('new-recording-btn'), acceptBtn: $('accept-btn'),
    sourceBox: $('source-box'), sourceBrowserLabel: $('source-browser-label'), piTestLink: $('pi-test-link'),
    piBadge: $('pi-badge'), piMeter: $('pi-meter'), piMeterBar: $('pi-meter-bar'),
  };

  const state = {
    mode: 'idle', // idle | recording | processing | editing | saving
    reviewMode: !!reviewId,
    source: 'browser',      // 'browser' | 'pi_i2s'
    piPollId: null,
    piPolling: false,
    capture: null,          // { ctx, stream, source, node }
    chunks: [],
    totalSamples: 0,
    startedAt: 0,
    timerId: null,
    samples: null,          // Float32Array @ 16 kHz
    duration: 0,
    recordingId: null,
    recordingName: '',
    view: null,             // WaveformView
    boxes: [],              // NEW (unsaved) boxes: { id, start, label, el }
    saved: [],              // already-saved clips: { clip_id, label, start_sec, el }
    nextBoxId: 1,
    selectedId: null,
    playhead: 0,
    playCtx: null,
    audioBuffer: null,
    player: null,           // { src, from, length, startedAt, followPlayhead }
    rafId: null,
  };

  // ------------------------------------------------------------------ utils

  const pad2 = (n) => String(n).padStart(2, '0');
  const snap = (sec) => Math.round(sec / SNAP_SEC) * SNAP_SEC;
  const clampStart = (sec) => Math.min(Math.max(sec, 0), Math.max(state.duration - CLIP_SEC, 0));
  const round2 = (x) => Math.round(x * 100) / 100;

  function todayStr() {
    const d = new Date();
    return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  }

  function showError(title, detail = '') {
    els.errorTitle.textContent = title;
    els.errorDetail.textContent = detail;
    els.error.classList.remove('hidden');
  }

  function hideError() {
    els.error.classList.add('hidden');
  }

  function setStatus(text) {
    els.status.textContent = text;
  }

  function labelOptions(selected) {
    return Object.entries(labels)
      .map(([key, display]) => `<option value="${key}"${key === selected ? ' selected' : ''}>${UI.escapeHtml(display)}</option>`)
      .join('');
  }

  // Parse a 16-bit PCM mono WAV (as written by app.py) into Float32 samples.
  function parseWav(arrayBuffer) {
    const view = new DataView(arrayBuffer);
    const tag = (offset) => String.fromCharCode(...new Uint8Array(arrayBuffer, offset, 4));
    if (tag(0) !== 'RIFF' || tag(8) !== 'WAVE') throw new Error('Not a WAV file');
    let offset = 12;
    let format = null;
    while (offset + 8 <= view.byteLength) {
      const id = tag(offset);
      const size = view.getUint32(offset + 4, true);
      const body = offset + 8;
      if (id === 'fmt ') {
        format = {
          channels: view.getUint16(body + 2, true),
          rate: view.getUint32(body + 4, true),
          bits: view.getUint16(body + 14, true),
        };
      } else if (id === 'data') {
        if (!format || format.channels !== 1 || format.bits !== 16 || format.rate !== TARGET_SR) {
          throw new Error('Expected 16-bit mono 16 kHz WAV');
        }
        const count = Math.floor(Math.min(size, view.byteLength - body) / 2);
        const out = new Float32Array(count);
        for (let i = 0; i < count; i++) out[i] = view.getInt16(body + i * 2, true) / 32768;
        return out;
      }
      offset = body + size + (size % 2);
    }
    throw new Error('WAV has no data chunk');
  }

  // -------------------------------------------------------------- capture

  function supportCheck() {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return 'Microphone API unavailable. Open the app via http://localhost:5000 (not an IP address) in Chrome or Edge.';
    }
    if (!window.AudioContext || !window.AudioWorkletNode) {
      return 'Use Chrome/Edge, Firefox/Safari may not work (AudioWorklet is not supported in this browser).';
    }
    return null;
  }

  function showMicError(err) {
    const name = err && err.name;
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      showError(
        'Microphone permission denied.',
        'Click the microphone / lock icon at the left of Chrome\'s address bar → Microphone → Allow, then reload the page. ' +
        'Also check Windows Settings → Privacy & security → Microphone: "Microphone access" and ' +
        '"Let desktop apps access your microphone" must be On.'
      );
    } else if (name === 'NotFoundError' || name === 'OverconstrainedError') {
      showError('No microphone found.', 'Connect a microphone and check Windows Settings → System → Sound → Input.');
    } else if (name === 'NotReadableError') {
      showError('Microphone is busy.', 'Another application may be using it exclusively. Close it and try again.');
    } else {
      showError('Could not access the microphone.', String((err && err.message) || err));
    }
  }

  // Try a native 16 kHz context first; if Chrome refuses, fall back to the
  // device rate and let the worklet resample.
  async function createCapture(stream) {
    let lastErr = null;
    for (const opts of [{ sampleRate: TARGET_SR }, {}]) {
      let ctx = null;
      try {
        ctx = new AudioContext(opts);
        await ctx.audioWorklet.addModule(WORKLET_URL);
        const source = ctx.createMediaStreamSource(stream);
        const node = new AudioWorkletNode(ctx, 'pcm-capture', {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          channelCount: 1,
          channelCountMode: 'explicit',
          channelInterpretation: 'speakers',
          processorOptions: { targetRate: TARGET_SR },
        });
        source.connect(node);
        node.connect(ctx.destination); // outputs silence; keeps the node pulled
        await ctx.resume();
        return { ctx, stream, source, node };
      } catch (err) {
        lastErr = err;
        if (ctx) ctx.close().catch(() => {});
      }
    }
    throw lastErr;
  }

  async function startRecording() {
    hideError();
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: TARGET_SR,
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
    } catch (err) {
      showMicError(err);
      return;
    }

    try {
      state.capture = await createCapture(stream);
    } catch (err) {
      stream.getTracks().forEach((t) => t.stop());
      showError('Audio initialisation failed.', String(err && err.message || err));
      return;
    }

    state.chunks = [];
    state.totalSamples = 0;
    state.capture.node.port.onmessage = (event) => {
      if (event.data.type === 'data') {
        state.chunks.push(event.data.samples);
        state.totalSamples += event.data.samples.length;
      }
    };

    state.mode = 'recording';
    state.startedAt = performance.now();
    state.timerId = setInterval(updateTimer, 200);
    updateTimer();

    els.recordBtn.textContent = '■ STOP';
    els.recordBtn.classList.add('ring-4', 'ring-red-200');
    els.name.disabled = true;
    els.defaultLabel.disabled = true;
    els.recent.disabled = true;
    const rate = state.capture.ctx.sampleRate;
    setStatus(`Recording… (${rate === TARGET_SR ? '16 kHz native' : `${rate} Hz → 16 kHz resampled`})`);
  }

  function updateTimer() {
    const sec = (performance.now() - state.startedAt) / 1000;
    els.timer.textContent = fmt.timer(sec);
    els.longWarning.classList.toggle('hidden', sec <= WARN_SEC);
  }

  function flushWorklet(node) {
    return new Promise((resolve) => {
      const timeout = setTimeout(resolve, 500);
      const prev = node.port.onmessage;
      node.port.onmessage = (event) => {
        if (event.data.type === 'flushed') {
          clearTimeout(timeout);
          resolve();
        } else if (prev) {
          prev(event);
        }
      };
      node.port.postMessage('flush');
    });
  }

  async function stopRecording() {
    state.mode = 'processing';
    clearInterval(state.timerId);
    els.recordBtn.disabled = true;
    els.recordBtn.classList.remove('ring-4', 'ring-red-200');
    setStatus('Processing…');

    const { ctx, stream, source, node } = state.capture;
    await flushWorklet(node);
    stream.getTracks().forEach((t) => t.stop());
    source.disconnect();
    node.disconnect();
    await ctx.close().catch(() => {});
    state.capture = null;

    const samples = new Float32Array(state.totalSamples);
    let offset = 0;
    for (const chunk of state.chunks) {
      samples.set(chunk, offset);
      offset += chunk.length;
    }
    state.chunks = [];

    if (samples.length < TARGET_SR * 0.1) {
      showError('Recording too short.', 'Record at least a second of audio.');
      resetRecorder();
      return;
    }

    state.samples = samples;
    state.duration = samples.length / TARGET_SR;
    await uploadRecording();
  }

  function floatToInt16(float32) {
    const out = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
      const s = Math.max(-1, Math.min(1, float32[i]));
      out[i] = Math.max(-32768, Math.min(32767, Math.round(s < 0 ? s * 32768 : s * 32767)));
    }
    return out;
  }

  // The original WAV is written to disk by this upload, immediately after Stop,
  // so it survives a closed tab / accidental navigation.
  async function uploadRecording() {
    showEditorShell();
    state.view.layout(state.duration);

    const params = new URLSearchParams({
      name: els.name.value.trim(),
      default_label: els.defaultLabel.value,
      peaks: String(state.view.peakCount),
    });

    try {
      const pcm = floatToInt16(state.samples);
      const data = await apiJson(`/api/upload-recording?${params}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: pcm.buffer,
      });
      enterEditor({
        recording_id: data.recording_id,
        recording_name: data.recording_name,
        duration_sec: state.duration,
        clips: [],
      }, data.waveform);
      loadRecent();
    } catch (err) {
      showError('Upload failed.', String(err.message || err));
      resetRecorder();
    }
  }

  // Review mode: load an existing original + its saved clips.
  async function loadExisting(recordingId) {
    state.mode = 'processing';
    els.recordBtn.classList.add('hidden');
    els.timerBox.classList.add('hidden');
    els.name.disabled = true;
    setStatus(`Loading ${recordingId}…`);
    showEditorShell();

    try {
      const meta = await apiJson(`/api/recording/${encodeURIComponent(recordingId)}?peaks=0`);
      els.name.value = meta.recording_name;
      if (meta.default_label in labels) els.defaultLabel.value = meta.default_label;
      await openInEditor(meta);
    } catch (err) {
      els.editor.classList.add('hidden');
      state.mode = 'idle';
      showError('Could not load the recording.', String(err.message || err));
    }
  }

  // Load a recording that already exists on the server (review mode, finished Pi recording).
  async function openInEditor(meta) {
    showEditorShell();
    const res = await fetch(`/recordings/${meta.original_path.split('/').map(encodeURIComponent).join('/')}`);
    if (!res.ok) throw new Error(`Could not load ${meta.original_path} (HTTP ${res.status})`);
    state.samples = parseWav(await res.arrayBuffer());
    state.audioBuffer = null;
    state.duration = state.samples.length / TARGET_SR;
    state.view.layout(state.duration);
    enterEditor(meta, computePeaks(state.samples, state.view.peakCount));
  }

  // ------------------------------------------------------- Pi I2S source
  // Audio is captured on the server (Raspberry Pi); the browser only starts/stops
  // it, shows the level, and then loads the finished WAV into the same editor.

  function stopPiPolling() {
    clearInterval(state.piPollId);
    state.piPollId = null;
    els.piMeter.classList.add('hidden');
  }

  function enterPiRecording(recordingId, soFar = 0) {
    state.recordingId = recordingId;
    state.mode = 'recording';
    els.recordBtn.textContent = '■ STOP';
    els.recordBtn.classList.add('ring-4', 'ring-red-200');
    els.recordBtn.disabled = false;
    els.name.disabled = true;
    els.defaultLabel.disabled = true;
    els.recent.disabled = true;
    setSourceLocked(true);
    els.timer.textContent = fmt.timer(soFar);
    els.piMeterBar.style.width = '0%';
    els.piMeter.classList.remove('hidden');
    setStatus(`Salvestan Pi I2S mikrofonist (${recordingId})…`);
    clearInterval(state.piPollId);
    state.piPollId = setInterval(piPoll, PI_POLL_MS);
  }

  async function piStart() {
    hideError();
    try {
      const data = await apiJson('/api/pi/start-recording', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: els.name.value.trim(), default_label: els.defaultLabel.value }),
      });
      enterPiRecording(data.recording_id);
    } catch (err) {
      showError('Pi mikrofoni salvestus ei alanud.', String(err.message || err));
    }
  }

  async function piPoll() {
    if (state.piPolling) return;  // previous request still in flight
    state.piPolling = true;
    try {
      const s = await apiJson('/api/pi/recording-status');
      if (state.mode !== 'recording') return;
      if (!s.active || s.recording_id !== state.recordingId) {
        stopPiPolling();
        showError('Pi salvestus pole enam aktiivne.', 'Serveris ei käi seda salvestust (rakendus taaskäivitati?).');
        resetRecorder();
        return;
      }
      els.timer.textContent = fmt.timer(s.duration_so_far);
      els.longWarning.classList.toggle('hidden', s.duration_so_far <= WARN_SEC);
      els.piMeterBar.style.width = `${Math.min(1, s.peak_amplitude) * 100}%`;
      els.piMeterBar.classList.toggle('bg-red-500', s.peak_amplitude > 0.9);
      els.piMeterBar.classList.toggle('bg-emerald-500', s.peak_amplitude <= 0.9);
      if (s.failed) {
        showError('Pi mikrofoni salvestus katkes.', `${s.error} Salvestan seni salvestatud osa.`);
        await piStop();
      }
    } catch (err) {
      console.warn('Pi status poll failed (will retry)', err);  // transient network error
    } finally {
      state.piPolling = false;
    }
  }

  async function piStop() {
    stopPiPolling();
    state.mode = 'processing';
    els.recordBtn.disabled = true;
    els.recordBtn.classList.remove('ring-4', 'ring-red-200');
    setStatus('Processing…');
    try {
      const data = await apiJson('/api/pi/stop-recording', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ recording_id: state.recordingId }),
      });
      if (data.warning) {
        showError('Salvestus katkes enneaegselt.', `${data.warning}. Salvestatud osa (${data.duration_sec.toFixed(1)} s) on alles.`);
      }
      await openInEditor({ ...data, clips: [] });
      loadRecent();
    } catch (err) {
      showError('Pi salvestuse lõpetamine ebaõnnestus.', String(err.message || err));
      resetRecorder();
    }
  }

  // A Pi recording keeps running on the server if this page is reloaded: pick it up again.
  async function resumePiRecording() {
    try {
      const s = await apiJson('/api/pi/recording-status');
      if (s.active && state.mode === 'idle') {
        setSource('pi_i2s', false);
        enterPiRecording(s.recording_id, s.duration_so_far);
      }
    } catch (err) {
      console.warn('Could not check Pi recording status', err);
    }
  }

  // ------------------------------------------------------- source choice

  // getUserMedia only exists on HTTPS or localhost, so opened as http://<pi-ip>
  // the browser mic is hidden and the Pi mic is the only source.
  function availableSources() {
    const list = [];
    if (window.isSecureContext) list.push('browser');
    if (piMicAvailable) list.push('pi_i2s');
    return list;
  }

  function preferredSource() {
    const available = availableSources();
    let saved = null;
    try { saved = localStorage.getItem(SOURCE_KEY); } catch (_) { /* storage blocked */ }
    if (available.includes(saved)) return saved;
    return available.includes('pi_i2s') ? 'pi_i2s' : 'browser';
  }

  function setSource(source, persist = true) {
    state.source = source;
    if (persist) {
      try { localStorage.setItem(SOURCE_KEY, source); } catch (_) { /* storage blocked */ }
    }
    document.querySelectorAll('input[name="source"]').forEach((r) => { r.checked = r.value === source; });
    const pi = source === 'pi_i2s';
    els.piBadge.classList.toggle('hidden', !pi);
    if (els.piTestLink) els.piTestLink.classList.toggle('hidden', !pi);
    hideError();
    const unsupported = pi ? null : supportCheck();  // browser capabilities matter only for the browser mic
    if (unsupported) showError('Browser not supported.', unsupported);
    if (state.mode === 'idle') els.recordBtn.disabled = !!unsupported;
  }

  function setSourceLocked(locked) {
    document.querySelectorAll('input[name="source"]').forEach((r) => { r.disabled = locked; });
  }

  // --------------------------------------------------------------- editor

  function showEditorShell() {
    els.editor.classList.remove('hidden');
    els.editorLoading.classList.remove('hidden');
    if (!state.view) {
      state.view = new WaveformView(els.wave, {
        height: CANVAS_HEIGHT,
        minPxPerSec: MIN_PX_PER_SEC,
        onSeek: seekTo,
      });
    }
  }

  function enterEditor(meta, peaks) {
    state.mode = 'editing';
    state.recordingId = meta.recording_id;
    state.recordingName = meta.recording_name;
    state.playhead = 0;
    els.editorLoading.classList.add('hidden');
    els.recordBtn.disabled = true;
    els.defaultLabel.disabled = false; // can still change the label for new boxes
    els.recent.disabled = false;

    state.view.draw(peaks);
    state.view.clearBoxes();
    state.saved = [];
    (meta.clips || []).forEach(addSavedBox);
    renderPlayhead(0);

    els.recInfo.textContent = `${state.recordingId} · ${state.recordingName} · ${fmt.precise(state.duration)}`;
    els.editorTitle.textContent = state.reviewMode ? 'Review: add more boxes' : 'Place 1-second boxes';
    setStatus(state.reviewMode
      ? 'Review mode — dashed boxes are already saved. Add new boxes in the gaps, then save.'
      : 'Recording saved to disk. Place boxes over the keywords, then Accept.');
    updateControls();
  }

  function addSavedBox(clip) {
    const entry = { clip_id: clip.clip_id, label: clip.label, start_sec: Number(clip.start_sec), el: null };
    entry.el = state.view.addSavedBox(entry, {
      onPlay: () => playFrom(entry.start_sec, CLIP_SEC, false),
      onBodyClick: (event) => seekTo(state.view.eventToSec(event)),
    });
    state.saved.push(entry);
  }

  function renderPlayhead(sec) {
    state.view.setPlayhead(sec);
    els.playheadTime.textContent = fmt.precise(sec);
  }

  function seekTo(sec) {
    const wasPlaying = state.player && state.player.followPlayhead;
    state.playhead = sec;
    if (wasPlaying) playFrom(sec);
    else {
      stopPlayback();
      renderPlayhead(sec);
    }
  }

  function updateControls() {
    const editing = state.mode === 'editing';
    const hasSaved = state.saved.length > 0;
    const showDeleteAll = hasSaved || state.reviewMode;

    els.addBoxBtn.disabled = !editing;
    els.playAllBtn.disabled = !editing;
    els.discardBtn.disabled = !editing;
    els.deleteAllBtn.disabled = !editing;
    els.newRecordingBtn.disabled = !(editing || state.mode === 'idle');
    els.acceptBtn.disabled = !editing || state.boxes.length === 0;

    els.discardBtn.classList.toggle('hidden', showDeleteAll);
    els.deleteAllBtn.classList.toggle('hidden', !showDeleteAll);
    els.newRecordingBtn.classList.toggle('hidden', !showDeleteAll);
    els.acceptBtn.textContent = hasSaved || state.reviewMode ? '💾 Salvesta uued boxid' : '💾 Accept & save all';

    els.boxCount.textContent = String(state.boxes.length);
    els.savedCount.textContent = String(state.saved.length);
    els.playAllBtn.textContent = state.player && state.player.followPlayhead ? '⏸ Pause' : '▶ Play all';
  }

  // ------------------------------------------------------------ new boxes

  function addBox(startSec, label) {
    const box = {
      id: state.nextBoxId++,
      start: clampStart(snap(startSec)),
      label: label || els.defaultLabel.value,
      el: null,
    };
    box.el = state.view.mountBox(createBoxElement(box), box.start);
    state.boxes.push(box);
    selectBox(box.id);
    updateControls();
  }

  function createBoxElement(box) {
    const el = document.createElement('div');
    el.className = 'wf-box absolute top-0 flex flex-col justify-between rounded-md border-2 select-none cursor-grab';
    el.style.touchAction = 'none';
    el.innerHTML = `
      <div class="flex items-center gap-1 p-1">
        <select class="box-label min-w-0 grow rounded border border-white/60 bg-white/90 px-1 py-0.5 text-xs">${labelOptions(box.label)}</select>
        <button type="button" class="box-play shrink-0 rounded bg-white/90 px-1.5 py-0.5 text-xs hover:bg-white" title="Play this second">▶</button>
        <button type="button" class="box-delete shrink-0 rounded bg-white/90 px-1.5 py-0.5 text-xs hover:bg-white hover:text-red-600" title="Delete box">✕</button>
      </div>
      <div class="box-time px-1 pb-1 font-mono text-[10px] text-slate-700"></div>`;

    const select = el.querySelector('.box-label');
    select.addEventListener('change', () => {
      box.label = select.value;
      renderBox(box);
      select.blur(); // return keyboard shortcuts to the page
    });
    el.querySelector('.box-play').addEventListener('click', (e) => {
      e.currentTarget.blur();
      playFrom(box.start, CLIP_SEC, false);
    });
    el.querySelector('.box-delete').addEventListener('click', () => deleteBox(box.id));

    el.addEventListener('pointerdown', (event) => startDrag(event, box));
    return el;
  }

  function renderBox(box) {
    const color = colors[box.label] || '#64748b';
    const selected = box.id === state.selectedId;
    const el = box.el;
    el.style.left = `${box.start * state.view.pxPerSec}px`;
    el.style.borderColor = color;
    el.style.backgroundColor = `${color}${selected ? '40' : '26'}`;
    el.style.boxShadow = selected ? '0 0 0 2px #4f46e5' : 'none';
    el.style.zIndex = selected ? '40' : '10';
    el.querySelector('.box-time').textContent = `${box.start.toFixed(2)}s`;
  }

  function selectBox(id) {
    state.selectedId = id;
    state.boxes.forEach(renderBox);
  }

  function deleteBox(id) {
    const box = state.boxes.find((b) => b.id === id);
    if (!box) return;
    box.el.remove();
    state.boxes = state.boxes.filter((b) => b.id !== id);
    if (state.selectedId === id) state.selectedId = null;
    updateControls();
  }

  function startDrag(event, box) {
    selectBox(box.id);
    if (event.target.closest('select, button')) return;
    event.preventDefault();

    const el = box.el;
    const startX = event.clientX;
    const origStart = box.start;
    el.setPointerCapture(event.pointerId);
    el.style.cursor = 'grabbing';

    const onMove = (ev) => {
      box.start = clampStart(snap(origStart + (ev.clientX - startX) / state.view.pxPerSec));
      renderBox(box);
    };
    const onUp = (ev) => {
      if (el.hasPointerCapture(ev.pointerId)) el.releasePointerCapture(ev.pointerId);
      el.style.cursor = '';
      el.removeEventListener('pointermove', onMove);
      el.removeEventListener('pointerup', onUp);
      el.removeEventListener('pointercancel', onUp);
    };
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerup', onUp);
    el.addEventListener('pointercancel', onUp);
  }

  // ------------------------------------------------------------- playback

  function getAudioBuffer() {
    if (!state.playCtx) state.playCtx = new AudioContext();
    if (!state.audioBuffer) {
      const buf = state.playCtx.createBuffer(1, state.samples.length, TARGET_SR);
      buf.copyToChannel(state.samples, 0);
      state.audioBuffer = buf;
    }
    return state.audioBuffer;
  }

  function currentPosition() {
    const p = state.player;
    if (!p || !p.followPlayhead) return state.playhead;
    return Math.min(p.from + (state.playCtx.currentTime - p.startedAt), state.duration);
  }

  // followPlayhead=true: transport playback (moves state.playhead).
  // followPlayhead=false: box preview (cursor animates, then returns).
  function playFrom(from, length = null, followPlayhead = true) {
    stopPlayback();
    const buffer = getAudioBuffer();
    if (followPlayhead && from >= state.duration - 0.01) from = 0;
    const playLength = length ?? state.duration - from;

    state.playCtx.resume();
    const src = state.playCtx.createBufferSource();
    src.buffer = buffer;
    src.connect(state.playCtx.destination);
    src.start(0, from, playLength);
    state.player = { src, from, length: playLength, startedAt: state.playCtx.currentTime, followPlayhead };
    src.onended = () => {
      if (state.player && state.player.src === src) finishPlayback(true);
    };
    if (followPlayhead) state.playhead = from;
    updateControls();
    animatePlayhead();
  }

  function animatePlayhead() {
    cancelAnimationFrame(state.rafId);
    const tick = () => {
      const p = state.player;
      if (!p) return;
      const pos = Math.min(p.from + (state.playCtx.currentTime - p.startedAt), p.from + p.length);
      renderPlayhead(pos);
      state.view.keepVisible(pos);
      state.rafId = requestAnimationFrame(tick);
    };
    state.rafId = requestAnimationFrame(tick);
  }

  function finishPlayback(ended) {
    const p = state.player;
    if (!p) return;
    if (p.followPlayhead) state.playhead = ended ? p.from + p.length : currentPosition();
    try { p.src.onended = null; p.src.stop(); } catch (_) { /* already stopped */ }
    state.player = null;
    cancelAnimationFrame(state.rafId);
    renderPlayhead(state.playhead);
    updateControls();
  }

  function stopPlayback() {
    finishPlayback(false);
  }

  function togglePlay() {
    if (state.player && state.player.followPlayhead) stopPlayback();
    else playFrom(state.playhead);
  }

  // -------------------------------------------------------------- actions

  // Saves only the NEW boxes. On success they become dashed "saved" boxes and
  // the editor stays open so more boxes can be added.
  async function saveNewBoxes() {
    if (state.mode !== 'editing' || state.boxes.length === 0) return;
    if (state.duration > WARN_SEC) {
      const ok = await confirmDialog({
        title: 'Pikk salvestus',
        message: `This recording is ${fmt.timer(state.duration)} long (over 10 minutes). Save the clips anyway?`,
        confirmText: 'Salvesta',
        danger: false,
      });
      if (!ok) return;
    }
    stopPlayback();
    state.mode = 'saving';
    updateControls();

    const pending = [...state.boxes].sort((a, b) => a.start - b.start);
    try {
      const data = await apiJson('/api/save-clips', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          recording_id: state.recordingId,
          boxes: pending.map((b) => ({ start_sec: round2(b.start), label: b.label })),
        }),
      });
      pending.forEach((b) => b.el.remove());
      state.boxes = [];
      state.selectedId = null;
      data.clips.forEach(addSavedBox);
      state.mode = 'editing';
      updateControls();
      toast(`${data.saved_count} klippi salvestatud. Saad lisada veel sildistusi või alustada uut salvestust.`);
      loadRecent();
    } catch (err) {
      state.mode = 'editing';
      updateControls();
      showError('Saving clips failed.', String(err.message || err));
    }
  }

  async function discardRecording() {
    if (state.mode !== 'editing') return;
    const ok = await confirmDialog({
      title: 'Kas oled kindel?',
      message: `Originaal kaob jäädavalt. (${state.recordingId}${state.boxes.length ? `, ${state.boxes.length} salvestamata boxi` : ''})`,
      confirmText: 'Kustuta',
    });
    if (!ok) return;
    const id = state.recordingId;
    try {
      await apiJson(`/api/recording/${encodeURIComponent(id)}`, { method: 'DELETE' });
      toast(`${id} kustutatud`);
      startNewRecording(true);
    } catch (err) {
      showError('Could not delete the original recording file.', String(err.message || err));
    }
  }

  async function deleteEverything() {
    if (state.mode !== 'editing') return;
    const ok = await confirmDialog({
      title: 'Kustuta originaal + kõik klipid?',
      message: `See kustutab jäädavalt ${state.recordingId} originaal-WAV-i, ${state.saved.length} klippi kaustast ` +
               'recordings/clips/ ja kõik selle salvestuse read labels.csv-st.',
      confirmText: 'Kustuta kõik',
    });
    if (!ok) return;
    const id = state.recordingId;
    try {
      const data = await apiJson(`/api/recording/${encodeURIComponent(id)}?all=1`, { method: 'DELETE' });
      toast(`${id} kustutatud koos ${data.deleted_clips} klipiga`);
      startNewRecording(true);
    } catch (err) {
      showError('Delete failed.', String(err.message || err));
    }
  }

  async function startNewRecording(force = false) {
    if (!force && state.boxes.length) {
      const ok = await confirmDialog({
        title: 'Salvestamata boxid',
        message: `${state.boxes.length} uut boxi pole salvestatud. Alustada ikkagi uut salvestust?`,
        confirmText: 'Jah, uus salvestus',
      });
      if (!ok) return;
    }
    state.boxes = []; // nothing left to protect in beforeunload
    if (state.reviewMode) {
      window.location.href = '/';
      return;
    }
    resetRecorder();
    loadRecent();
  }

  function resetRecorder() {
    stopPlayback();
    if (state.view) state.view.clearBoxes();
    state.boxes = [];
    state.saved = [];
    state.selectedId = null;
    state.mode = 'idle';
    state.samples = null;
    state.audioBuffer = null;
    state.duration = 0;
    state.recordingId = null;
    state.playhead = 0;
    els.editor.classList.add('hidden');
    els.recordBtn.disabled = false;
    els.recordBtn.textContent = '● RECORD';
    els.recordBtn.classList.remove('ring-4', 'ring-red-200');
    els.name.disabled = false;
    els.name.value = `${todayStr()} recording`;
    els.defaultLabel.disabled = false;
    els.recent.disabled = false;
    els.recent.value = '';
    els.timer.textContent = '00:00';
    els.longWarning.classList.add('hidden');
    stopPiPolling();
    setSourceLocked(false);
    setStatus('Press RECORD or the space bar to start.');
    updateControls();
  }

  async function toggleRecord() {
    if (state.reviewMode) return;
    const pi = state.source === 'pi_i2s';
    if (state.mode === 'idle') {
      setSourceLocked(true);
      await (pi ? piStart() : startRecording());
      if (state.mode === 'idle') setSourceLocked(false);  // start failed
    } else if (state.mode === 'recording') {
      await (pi ? piStop() : stopRecording());
    }
  }

  // ------------------------------------------------------- recent picker

  async function loadRecent() {
    try {
      const data = await apiJson('/api/recordings');
      const options = data.recordings.slice(0, RECENT_COUNT).map((r) => {
        const text = `${r.recording_id} · ${r.recording_name} · ${fmt.tick(r.duration_sec)} · ${r.clip_count} klippi`;
        return `<option value="${UI.escapeHtml(r.recording_id)}">${UI.escapeHtml(text)}</option>`;
      });
      els.recent.innerHTML = `<option value="">— vali salvestus —</option>${options.join('')}`;
      els.recent.value = state.reviewMode ? (reviewId || '') : '';
    } catch (err) {
      // Non-critical: the picker just stays empty.
      console.warn('Could not load recent recordings', err);
    }
  }

  function onRecentChange() {
    const id = els.recent.value;
    els.recent.blur();
    if (!id || id === state.recordingId) return;
    window.location.href = `/review/${encodeURIComponent(id)}`; // beforeunload guards unsaved boxes
  }

  // ------------------------------------------------------------- keyboard

  function onKeyDown(event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (UI.isModalOpen()) return;
    const target = event.target;
    const typing = target instanceof Element && target.matches('input, textarea, select, [contenteditable]');
    if (typing) return;

    if (event.code === 'Space') {
      event.preventDefault(); // no page scroll, no button activation
      if (event.repeat) return;
      if (target instanceof HTMLButtonElement) target.blur();
      if (state.mode === 'editing') togglePlay();
      else if (state.mode === 'idle' || state.mode === 'recording') toggleRecord();
      return;
    }
    if (state.mode !== 'editing') return;

    if (event.key === 'b' || event.key === 'B') {
      event.preventDefault();
      addBox(currentPosition());
    } else if (event.key === 'Delete' && state.selectedId !== null) {
      event.preventDefault();
      deleteBox(state.selectedId);
    }
  }

  // ----------------------------------------------------------------- init

  function init() {
    els.defaultLabel.innerHTML = labelOptions(defaultLabel);
    els.name.value = `${todayStr()} recording`;

    els.recordBtn.addEventListener('click', (e) => {
      e.currentTarget.blur();
      toggleRecord();
    });
    els.addBoxBtn.addEventListener('click', () => addBox(currentPosition()));
    els.playAllBtn.addEventListener('click', () => {
      if (state.player && state.player.followPlayhead) stopPlayback();
      else playFrom(0);
    });
    els.acceptBtn.addEventListener('click', saveNewBoxes);
    els.discardBtn.addEventListener('click', discardRecording);
    els.deleteAllBtn.addEventListener('click', deleteEverything);
    els.newRecordingBtn.addEventListener('click', () => startNewRecording());
    els.recent.addEventListener('change', onRecentChange);
    document.addEventListener('keydown', onKeyDown);
    window.addEventListener('beforeunload', (event) => {
      if (state.mode === 'recording' || state.boxes.length) {
        event.preventDefault();
        event.returnValue = '';
      }
    });

    loadRecent();
    updateControls();

    if (state.reviewMode) {
      loadExisting(reviewId);
      return;
    }
    if (els.sourceBox) {
      if (!availableSources().includes('browser')) els.sourceBrowserLabel.classList.add('hidden');
      document.querySelectorAll('input[name="source"]').forEach((r) => {
        r.addEventListener('change', () => { setSource(r.value); r.blur(); });
      });
    }
    setSource(availableSources().length ? preferredSource() : 'browser', false);
    if (piMicAvailable) resumePiRecording();
  }

  init();
})();
