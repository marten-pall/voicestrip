// Pi I2S mic calibration page: live peak meter (short poll) + 3 s test recording.
(() => {
  'use strict';

  const POLL_MS = 100;
  const QUIET = 0.01;
  const CLIPPING = 0.9;
  const $ = (id) => document.getElementById(id);
  const els = {
    error: $('error'), value: $('peak-value'), db: $('peak-db'), bar: $('peak-bar'), hint: $('peak-hint'),
    rtt: $('rtt'), lag: $('lag'),
    testBtn: $('test-btn'), testStatus: $('test-status'), audio: $('test-audio'),
  };
  let polling = false;
  let paused = false;
  let audioUrl = null;

  function showError(message) {
    els.error.textContent = message;
    els.error.classList.toggle('hidden', !message);
  }

  function render(peak) {
    els.value.textContent = peak.toFixed(3);
    els.db.textContent = peak > 0 ? (20 * Math.log10(peak)).toFixed(1) : '-∞';
    els.bar.style.width = `${Math.min(1, peak) * 100}%`;
    els.bar.className = `h-full ${peak > CLIPPING ? 'bg-red-500' : peak < QUIET ? 'bg-slate-400' : 'bg-emerald-500'}`;
    els.hint.textContent = peak > CLIPPING ? 'Liiga vali / lõikab — kontrolli juhtmeid või vähenda helitugevust.'
      : peak < QUIET ? 'Vaikne — räägi mikrofoni. Kui ka rääkides jääb alla 0.01, on mikrofon liiga kaugel või juhe lahti.'
      : 'Hea tase.';
  }

  async function poll() {
    if (polling || paused || document.hidden) return;
    polling = true;
    try {
      const t0 = performance.now();
      const res = await fetch('/api/pi/peak');
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      els.rtt.textContent = Math.round(performance.now() - t0);
      els.lag.textContent = data.buffer_lag_ms ?? '–';
      showError('');
      render(data.peak);
    } catch (err) {
      showError(`Taseme lugemine ebaõnnestus: ${err.message || err}`);
    } finally {
      polling = false;
    }
  }

  async function testRecord() {
    paused = true;
    els.testBtn.disabled = true;
    els.testStatus.textContent = 'Salvestan… räägi nüüd';
    try {
      const res = await fetch('/api/pi/test-record', { method: 'POST' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      const peak = Number(res.headers.get('X-Peak') || 0);
      if (audioUrl) URL.revokeObjectURL(audioUrl);
      audioUrl = URL.createObjectURL(await res.blob());
      els.audio.src = audioUrl;
      els.audio.classList.remove('hidden');
      els.audio.play().catch(() => {});
      els.testStatus.textContent = `Valmis — testi peak ${peak.toFixed(3)}`;
      showError('');
    } catch (err) {
      els.testStatus.textContent = '';
      showError(`Testsalvestus ebaõnnestus: ${err.message || err}`);
    } finally {
      els.testBtn.disabled = false;
      paused = false;
    }
  }

  els.testBtn.addEventListener('click', testRecord);
  setInterval(poll, POLL_MS);
  poll();
})();
