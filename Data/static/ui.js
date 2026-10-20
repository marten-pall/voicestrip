// Shared UI helpers: formatting, HTML escaping, toast, confirmation modal.
(() => {
  'use strict';

  const pad2 = (n) => String(n).padStart(2, '0');

  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

  const fmt = {
    timer: (sec) => `${pad2(Math.floor(sec / 60))}:${pad2(Math.floor(sec % 60))}`,    // 01:05
    tick: (sec) => `${Math.floor(sec / 60)}:${pad2(Math.floor(sec % 60))}`,           // 1:05
    precise: (sec) => `${Math.floor(sec / 60)}:${(sec % 60).toFixed(2).padStart(5, '0')}`, // 1:05.25
  };

  // ---------------------------------------------------------------- toast

  let toastEl = null;
  let toastTimer = null;

  function toast(message, ms = 4000) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.className = 'hidden fixed bottom-6 right-6 z-[60] max-w-sm rounded-xl bg-slate-900 px-4 py-3 text-sm text-white shadow-lg';
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = message;
    toastEl.classList.remove('hidden');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.add('hidden'), ms);
  }

  // ---------------------------------------------------------------- modal

  let modal = null;
  let resolveModal = null;

  function buildModal() {
    modal = document.createElement('div');
    modal.className = 'hidden fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40';
    modal.innerHTML = `
      <div class="w-full max-w-md rounded-xl bg-white p-6 shadow-lg" role="dialog" aria-modal="true">
        <h2 class="modal-title font-semibold text-lg"></h2>
        <p class="modal-message mt-2 text-sm text-slate-600"></p>
        <div class="mt-6 flex justify-end gap-2">
          <button type="button" class="modal-cancel rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium hover:bg-slate-50"></button>
          <button type="button" class="modal-confirm rounded-lg px-4 py-2 text-sm font-semibold text-white"></button>
        </div>
      </div>`;
    document.body.appendChild(modal);
    modal.querySelector('.modal-cancel').addEventListener('click', () => closeModal(false));
    modal.querySelector('.modal-confirm').addEventListener('click', () => closeModal(true));
    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(false); });
    document.addEventListener('keydown', (e) => {
      if (!isModalOpen()) return;
      if (e.key === 'Escape') { e.preventDefault(); closeModal(false); }
    });
  }

  function closeModal(result) {
    modal.classList.add('hidden');
    const resolve = resolveModal;
    resolveModal = null;
    if (resolve) resolve(result);
  }

  function isModalOpen() {
    return !!modal && !modal.classList.contains('hidden');
  }

  // Resolves to true (confirm) or false (cancel / Esc / backdrop click).
  function confirmDialog({ title, message = '', confirmText = 'OK', cancelText = 'Loobu', danger = true }) {
    if (!modal) buildModal();
    if (resolveModal) closeModal(false);
    modal.querySelector('.modal-title').textContent = title;
    modal.querySelector('.modal-message').textContent = message;
    modal.querySelector('.modal-cancel').textContent = cancelText;
    const confirmBtn = modal.querySelector('.modal-confirm');
    confirmBtn.textContent = confirmText;
    confirmBtn.className = `modal-confirm rounded-lg px-4 py-2 text-sm font-semibold text-white ${
      danger ? 'bg-red-600 hover:bg-red-700' : 'bg-indigo-600 hover:bg-indigo-700'}`;
    modal.classList.remove('hidden');
    confirmBtn.focus();
    return new Promise((resolve) => { resolveModal = resolve; });
  }

  async function apiJson(url, options) {
    const res = await fetch(url, options);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    return body;
  }

  window.UI = { escapeHtml, fmt, toast, confirmDialog, isModalOpen, apiJson };
})();
