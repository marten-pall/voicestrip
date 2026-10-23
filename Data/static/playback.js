// Playback page: clips grouped by recording, with an inline read-only preview
// of the original waveform + saved boxes, and play / delete per clip.
(() => {
  'use strict';

  const PREVIEW_HEIGHT = 120;
  const PREVIEW_MIN_PX_PER_SEC = 80;

  const { labels, colors } = window.APP;
  const { escapeHtml, confirmDialog, apiJson } = window.UI;
  const $ = (id) => document.getElementById(id);

  const els = {
    error: $('error'), filter: $('filter'), sourceFilter: $('source-filter'), counts: $('counts'),
    total: $('total'), recTotal: $('rec-total'),
    rows: $('rows'), empty: $('empty'), player: $('player'), nowPlaying: $('now-playing'),
  };

  const expanded = new Set(); // recording_ids whose preview is open (survives reloads)
  const durations = new Map(); // recording_id -> duration_sec (for preview layout)

  function showError(message) {
    els.error.textContent = message;
    els.error.classList.toggle('hidden', !message);
  }

  const urlFor = (relPath) => `/recordings/${relPath.split('/').map(encodeURIComponent).join('/')}`;

  function labelChip(key, extra = '') {
    const color = colors[key] || '#64748b';
    return `<span class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium"
                  style="border-color:${color}55;background:${color}14;color:#0f172a">
              <span class="h-2 w-2 rounded-full" style="background:${color}"></span>${escapeHtml(labels[key] || key)}${extra}
            </span>`;
  }

  function renderCounts(counts, total, recordingCount) {
    els.total.textContent = String(total);
    els.recTotal.textContent = String(recordingCount);
    els.counts.innerHTML = Object.keys(labels)
      .map((key) => labelChip(key, `: <span class="font-mono">${counts[key] || 0}</span>`))
      .join('');
  }

  // ------------------------------------------------------------- grouping

  function groupClips(recordings, clips, filtered) {
    const groups = new Map();
    for (const r of recordings) groups.set(r.recording_id, { rec: r, clips: [] });
    for (const c of clips) {
      if (!groups.has(c.recording_id)) {
        // Clip whose recording is missing from recordings.csv - still show it.
        groups.set(c.recording_id, {
          rec: { recording_id: c.recording_id, recording_name: c.recording_name, created_at: c.created_at, clip_count: 0 },
          clips: [],
        });
      }
      groups.get(c.recording_id).clips.push(c);
    }
    return [...groups.values()]
      .filter((g) => !filtered || g.clips.length > 0)
      .map((g) => {
        g.clips.sort((a, b) => a.start_sec - b.start_sec);
        g.total = Math.max(g.rec.clip_count || 0, g.clips.length);
        return g;
      })
      .sort((a, b) => String(b.rec.created_at).localeCompare(String(a.rec.created_at)));
  }

  function parentRow(g, filtered) {
    const id = escapeHtml(g.rec.recording_id);
    const open = expanded.has(g.rec.recording_id);
    const count = filtered ? `${g.clips.length} / ${g.total}` : String(g.total);
    return `
      <tr class="rec-row cursor-pointer border-t border-slate-200 bg-slate-50 hover:bg-indigo-50/50" data-rec="${id}">
        <td colspan="8" class="px-4 py-2.5">
          <div class="flex flex-wrap items-center gap-3">
            <span class="chevron w-3 text-slate-400">${open ? '▾' : '▸'}</span>
            <span class="font-mono text-xs font-semibold">${id}</span>
            <span class="font-medium">${escapeHtml(g.rec.recording_name || '')}</span>
            <span class="font-mono text-xs text-slate-500">${escapeHtml(String(g.rec.created_at || '').replace('T', ' '))}</span>
            <span class="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-xs text-slate-600">${count} klippi</span>
            <span class="grow"></span>
            <button type="button" data-preview="${id}"
                    class="rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs hover:bg-indigo-50 hover:border-indigo-300">▶ Preview</button>
            <a href="/review/${encodeURIComponent(g.rec.recording_id)}" data-review
               class="rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100">Jump to review →</a>
          </div>
        </td>
      </tr>
      <tr class="preview-row ${open ? '' : 'hidden'}" data-preview-for="${id}">
        <td colspan="8" class="px-4 pb-4 pt-2">
          <div class="mb-2 flex items-center gap-3 text-xs text-slate-500">
            <button type="button" data-play-original="${id}"
                    class="rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-xs text-slate-900 hover:bg-slate-50">▶ Play original</button>
            <span>Click a box to play that clip.</span>
          </div>
          <div class="preview-host"></div>
        </td>
      </tr>`;
  }

  function childRow(c) {
    return `
      <tr class="border-t border-slate-100 hover:bg-slate-50">
        <td class="py-2 pl-10 pr-4 font-mono text-xs">${escapeHtml(c.clip_id)}</td>
        <td class="px-4 py-2">${labelChip(c.label)}</td>
        <td class="px-4 py-2">${escapeHtml(c.recording_name)}</td>
        <td class="px-4 py-2 font-mono text-right">${c.start_sec.toFixed(2)}</td>
        <td class="px-4 py-2 font-mono text-right">${c.duration_sec.toFixed(1)}s</td>
        <td class="px-4 py-2 text-slate-500">${escapeHtml(c.source)}</td>
        <td class="px-4 py-2 font-mono text-xs text-slate-500">${escapeHtml(c.created_at.replace('T', ' '))}</td>
        <td class="px-4 py-2 text-right whitespace-nowrap">
          <button type="button" data-play="${escapeHtml(c.clip_path)}" data-id="${escapeHtml(c.clip_id)}"
                  class="rounded-lg border border-slate-300 px-2.5 py-1 text-xs hover:bg-indigo-50 hover:border-indigo-300">▶ play</button>
          <button type="button" data-delete="${escapeHtml(c.clip_id)}"
                  class="rounded-lg border border-slate-300 px-2.5 py-1 text-xs hover:bg-red-50 hover:border-red-300 hover:text-red-700">🗑 delete</button>
        </td>
      </tr>`;
  }

  function renderGroups(groups, filtered) {
    els.empty.classList.toggle('hidden', groups.length > 0);
    els.rows.innerHTML = groups
      .map((g) => parentRow(g, filtered) + g.clips.map(childRow).join(''))
      .join('');
    for (const g of groups) {
      if (expanded.has(g.rec.recording_id)) loadPreview(g.rec.recording_id);
    }
  }

  async function load() {
    const label = els.filter.value;
    const source = els.sourceFilter.value;
    const params = new URLSearchParams();
    if (label) params.set('label', label);
    if (source) params.set('source', source);
    const filtered = !!(label || source);
    try {
      const [recData, clipData] = await Promise.all([
        apiJson('/api/recordings'),
        apiJson(`/api/clips${params.toString() ? `?${params}` : ''}`),
      ]);
      fillSourceFilter(clipData.sources || []);
      showError('');
      recData.recordings.forEach((r) => durations.set(r.recording_id, r.duration_sec));
      const groups = groupClips(recData.recordings, clipData.clips, filtered);
      renderCounts(clipData.counts, clipData.total, recData.recordings.length);
      renderGroups(groups, filtered);
    } catch (err) {
      showError(`Could not load clips: ${err.message || err}`);
    }
  }

  function fillSourceFilter(sources) {
    const current = els.sourceFilter.value;
    els.sourceFilter.innerHTML = '<option value="">All sources</option>' +
      sources.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('');
    els.sourceFilter.value = sources.includes(current) ? current : '';
  }

  // -------------------------------------------------------------- preview

  function previewRow(recordingId) {
    return els.rows.querySelector(`[data-preview-for="${CSS.escape(recordingId)}"]`);
  }

  function togglePreview(recordingId) {
    const row = previewRow(recordingId);
    const parent = els.rows.querySelector(`[data-rec="${CSS.escape(recordingId)}"]`);
    if (!row) return;
    const open = !expanded.has(recordingId);
    if (open) expanded.add(recordingId);
    else expanded.delete(recordingId);
    row.classList.toggle('hidden', !open);
    parent.querySelector('.chevron').textContent = open ? '▾' : '▸';
    if (open) loadPreview(recordingId);
  }

  async function loadPreview(recordingId) {
    const row = previewRow(recordingId);
    if (!row) return;
    const host = row.querySelector('.preview-host');
    const view = new WaveformView(host, { height: PREVIEW_HEIGHT, minPxPerSec: PREVIEW_MIN_PX_PER_SEC });
    view.layout(durations.get(recordingId) || 10);
    try {
      const data = await apiJson(`/api/recording/${encodeURIComponent(recordingId)}?peaks=${view.peakCount}`);
      view.layout(data.duration_sec);
      view.draw(data.waveform);
      data.clips.forEach((clip) => view.addSavedBox(clip, {
        onPlay: () => play(clip.clip_path, clip.clip_id),
        onBodyClick: () => play(clip.clip_path, clip.clip_id),
      }));
      row.querySelector('[data-play-original]').dataset.path = data.original_path;
    } catch (err) {
      host.innerHTML = `<p class="text-xs text-red-700">Could not load the original: ${escapeHtml(err.message || err)}</p>`;
    }
  }

  // ------------------------------------------------------- play / delete

  function play(relPath, title) {
    els.player.src = urlFor(relPath);
    els.nowPlaying.textContent = title;
    els.player.play().catch((err) => showError(`Playback failed: ${err.message || err}`));
  }

  async function deleteClip(clipId) {
    const ok = await confirmDialog({
      title: 'Delete clip?',
      message: `This removes ${clipId} from disk and from labels.csv.`,
      confirmText: 'Delete',
      cancelText: 'Cancel',
    });
    if (!ok) return;
    try {
      await apiJson(`/api/clip/${encodeURIComponent(clipId)}`, { method: 'DELETE' });
      if (els.nowPlaying.textContent === clipId) {
        els.player.removeAttribute('src');
        els.player.load();
        els.nowPlaying.textContent = '—';
      }
      await load();
    } catch (err) {
      showError(`Delete failed: ${err.message || err}`);
    }
  }

  function onRowsClick(event) {
    const t = event.target;
    if (t.closest('[data-review]')) return; // plain link navigation
    const playBtn = t.closest('[data-play]');
    if (playBtn) return play(playBtn.dataset.play, playBtn.dataset.id);
    const delBtn = t.closest('[data-delete]');
    if (delBtn) return deleteClip(delBtn.dataset.delete);
    const originalBtn = t.closest('[data-play-original]');
    if (originalBtn) {
      if (originalBtn.dataset.path) play(originalBtn.dataset.path, `${originalBtn.dataset.playOriginal} (original)`);
      return;
    }
    const recRow = t.closest('.rec-row');
    if (recRow) togglePreview(recRow.dataset.rec);
  }

  function init() {
    els.filter.insertAdjacentHTML('beforeend', Object.entries(labels)
      .map(([key, display]) => `<option value="${key}">${escapeHtml(display)}</option>`).join(''));
    els.filter.addEventListener('change', load);
    els.sourceFilter.addEventListener('change', load);
    els.rows.addEventListener('click', onRowsClick);
    load();
  }

  init();
})();
