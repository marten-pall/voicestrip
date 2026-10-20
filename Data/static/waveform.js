// Shared waveform component: scrollable canvas + timeline + playhead + box layer.
// Used by the recorder (editable), review mode, and the playback preview (read-only).
(() => {
  'use strict';

  const CLIP_SEC = 1.0;
  const MAX_CANVAS_PX = 16000; // backing-store width cap; CSS stretches beyond
  const SAVED_BORDER = '#94a3b8';

  // Max absolute amplitude per bucket (0..1) from Float32 samples.
  function computePeaks(samples, count) {
    const n = samples.length;
    if (!n) return [];
    count = Math.max(1, Math.min(count, n));
    const step = n / count;
    const peaks = new Array(count);
    for (let i = 0; i < count; i++) {
      const end = Math.max(Math.floor((i + 1) * step), Math.floor(i * step) + 1);
      let max = 0;
      for (let j = Math.floor(i * step); j < end; j++) {
        const v = Math.abs(samples[j]);
        if (v > max) max = v;
      }
      peaks[i] = max;
    }
    return peaks;
  }

  class WaveformView {
    constructor(host, { height = 200, minPxPerSec = 120, onSeek = null } = {}) {
      this.height = height;
      this.minPxPerSec = minPxPerSec;
      this.onSeek = onSeek;
      this.duration = 0;
      this.pxPerSec = minPxPerSec;
      this.width = 0;

      host.innerHTML = `
        <div class="wf-scroller overflow-x-auto rounded-lg border border-slate-200 bg-slate-50">
          <div class="wf-track relative">
            <canvas class="block ${onSeek ? 'cursor-crosshair' : ''}"></canvas>
            <div class="wf-timeline relative h-6 border-t border-slate-200 bg-white text-[11px] font-mono text-slate-500 ${onSeek ? 'cursor-crosshair' : ''}"></div>
            <div class="wf-playhead hidden pointer-events-none absolute top-0 w-0.5 bg-indigo-600" style="height:${height}px; z-index:50"></div>
          </div>
        </div>`;
      this.scroller = host.querySelector('.wf-scroller');
      this.track = host.querySelector('.wf-track');
      this.canvas = host.querySelector('canvas');
      this.timeline = host.querySelector('.wf-timeline');
      this.playhead = host.querySelector('.wf-playhead');

      if (onSeek) {
        const seek = (e) => this.onSeek(this.eventToSec(e));
        this.canvas.addEventListener('click', seek);
        this.timeline.addEventListener('click', seek);
      }
    }

    // Must be called while the host is visible (needs a measurable width).
    layout(duration) {
      this.duration = duration;
      const viewWidth = Math.max(this.scroller.clientWidth, 300);
      const span = Math.max(duration, CLIP_SEC);
      this.pxPerSec = Math.max(viewWidth / span, this.minPxPerSec);
      this.width = Math.ceil(span * this.pxPerSec);
      this.track.style.width = `${this.width}px`;
    }

    get peakCount() {
      return Math.min(this.width, MAX_CANVAS_PX);
    }

    draw(peaks) {
      const canvas = this.canvas;
      const w = Math.max(peaks.length, 1);
      const h = this.height;
      canvas.width = w;
      canvas.height = h;
      canvas.style.width = `${this.width}px`;
      canvas.style.height = `${h}px`;

      const g = canvas.getContext('2d');
      const mid = h / 2;
      g.fillStyle = '#f8fafc';
      g.fillRect(0, 0, w, h);
      g.fillStyle = '#e2e8f0';
      g.fillRect(0, mid, w, 1);

      // Normalise to the loudest peak so quiet recordings remain visible.
      const maxPeak = peaks.reduce((m, p) => Math.max(m, p), 0);
      const scale = 1 / Math.max(maxPeak, 0.05);
      g.fillStyle = '#94a3b8';
      for (let i = 0; i < peaks.length; i++) {
        const bar = Math.max(1, Math.min(1, peaks[i] * scale) * (mid - 6));
        g.fillRect(i, mid - bar, 1, bar * 2);
      }
      this.drawTimeline();
    }

    drawTimeline() {
      const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
      const step = steps.find((s) => s * this.pxPerSec >= 60) || 600;
      this.timeline.style.width = `${this.width}px`;
      const parts = [];
      for (let t = 0; t <= this.duration + 1e-6; t += step) {
        const x = t * this.pxPerSec;
        parts.push(
          `<span class="absolute top-0 h-1.5 w-px bg-slate-300" style="left:${x}px"></span>` +
          `<span class="absolute top-1.5 pl-1" style="left:${x}px">${UI.fmt.tick(t)}</span>`
        );
      }
      this.timeline.innerHTML = parts.join('');
    }

    eventToSec(event) {
      const rect = this.track.getBoundingClientRect();
      return Math.min(Math.max((event.clientX - rect.left) / this.pxPerSec, 0), this.duration);
    }

    setPlayhead(sec) {
      this.playhead.classList.remove('hidden');
      this.playhead.style.left = `${sec * this.pxPerSec}px`;
    }

    hidePlayhead() {
      this.playhead.classList.add('hidden');
    }

    keepVisible(sec) {
      const x = sec * this.pxPerSec;
      const { scrollLeft, clientWidth } = this.scroller;
      if (x < scrollLeft || x > scrollLeft + clientWidth - 20) {
        this.scroller.scrollLeft = Math.max(x - 40, 0);
      }
    }

    // Insert a box element into the track (below the playhead).
    mountBox(el, startSec) {
      el.style.left = `${startSec * this.pxPerSec}px`;
      el.style.width = `${CLIP_SEC * this.pxPerSec}px`;
      el.style.height = `${this.height}px`;
      this.track.insertBefore(el, this.playhead);
      return el;
    }

    // Read-only "already saved" box: dashed gray border, 60% opacity, label-tinted.
    addSavedBox(clip, { onPlay = null, onBodyClick = null } = {}) {
      const { labels, colors } = window.APP;
      const color = colors[clip.label] || '#64748b';
      const display = labels[clip.label] || clip.label;
      const el = document.createElement('div');
      el.className = 'wf-saved absolute top-0 flex flex-col justify-between rounded-md border-2 border-dashed select-none';
      el.style.borderColor = SAVED_BORDER;
      el.style.backgroundColor = `${color}33`;
      el.style.opacity = '0.6';
      el.style.zIndex = '5';
      el.title = `Juba salvestatud: ${display}`;
      el.innerHTML = `
        <div class="flex items-center gap-1 p-1">
          <span class="min-w-0 grow truncate rounded bg-white/90 px-1 py-0.5 text-xs font-medium" style="color:${color}">● ${UI.escapeHtml(display)}</span>
          ${onPlay ? '<button type="button" class="wf-play shrink-0 rounded bg-white/90 px-1.5 py-0.5 text-xs hover:bg-white" title="Play this clip">▶</button>' : ''}
        </div>
        <div class="px-1 pb-1 font-mono text-[10px] text-slate-700">${Number(clip.start_sec).toFixed(2)}s</div>`;
      if (onPlay) {
        el.querySelector('.wf-play').addEventListener('click', (e) => {
          e.stopPropagation();
          e.currentTarget.blur();
          onPlay(clip);
        });
      }
      if (onBodyClick) {
        el.style.cursor = 'pointer';
        el.addEventListener('click', (e) => onBodyClick(e, clip));
      }
      return this.mountBox(el, Number(clip.start_sec));
    }

    clearBoxes() {
      this.track.querySelectorAll('.wf-box, .wf-saved').forEach((el) => el.remove());
    }
  }

  window.WaveformView = WaveformView;
  window.computePeaks = computePeaks;
})();
