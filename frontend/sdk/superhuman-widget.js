/**
 * SuperHuman AI Persona — Embeddable Website Widget SDK
 * Drop this single script tag on any website to add your AI persona.
 *
 * Usage:
 * <script src="https://your-app.railway.app/sdk/superhuman-widget.js"
 *   data-persona="default"
 *   data-position="bottom-right"
 *   data-color="#2E86AB"
 *   data-label="Talk to our AI Expert"
 *   data-api="https://your-app.railway.app">
 * </script>
 */

(function () {
  'use strict';

  // ── Read config from script tag ──────────────────────────────────────
  const scriptTag = document.currentScript ||
    document.querySelector('script[data-persona]');

  const CONFIG = {
    personaId:  scriptTag?.getAttribute('data-persona') || 'default',
    position:   scriptTag?.getAttribute('data-position') || 'bottom-right',
    color:      decodeURIComponent(scriptTag?.getAttribute('data-color') || '#2E86AB'),
    label:      decodeURIComponent(scriptTag?.getAttribute('data-label') || 'Talk to our AI Expert'),
    apiBase:    scriptTag?.getAttribute('data-api') || '',
    autoOpen:   scriptTag?.getAttribute('data-auto-open') === 'true',
    greeting:   decodeURIComponent(scriptTag?.getAttribute('data-greeting') || ''),
  };

  // ── Prevent double-init ───────────────────────────────────────────────
  if (window.__superhuman_loaded) return;
  window.__superhuman_loaded = true;

  // ── State ─────────────────────────────────────────────────────────────
  let isOpen = false;
  let hasLoaded = false;

  // ── Positioning ───────────────────────────────────────────────────────
  const POSITIONS = {
    'bottom-right': { bottom: '24px', right: '24px' },
    'bottom-left':  { bottom: '24px', left: '24px' },
  };
  const pos = POSITIONS[CONFIG.position] || POSITIONS['bottom-right'];
  const posStr = Object.entries(pos).map(([k, v]) => `${k}:${v}`).join(';');

  // ── Inject styles ─────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    #sh-widget-btn {
      position: fixed; ${posStr} z-index: 9998;
      background: ${CONFIG.color}; color: #fff;
      border: none; border-radius: 28px;
      padding: 13px 22px;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      font-size: 15px; font-weight: 600;
      cursor: pointer;
      box-shadow: 0 4px 20px rgba(0,0,0,0.25);
      display: flex; align-items: center; gap: 10px;
      transition: transform 0.2s, box-shadow 0.2s;
      white-space: nowrap;
    }
    #sh-widget-btn:hover { transform: translateY(-2px); box-shadow: 0 8px 30px rgba(0,0,0,0.3); }
    #sh-widget-btn .sh-avatar-dot {
      width: 9px; height: 9px; background: #4ade80;
      border-radius: 50%; flex-shrink: 0;
      box-shadow: 0 0 0 2px rgba(74,222,128,0.3);
      animation: sh-pulse 2s infinite;
    }
    @keyframes sh-pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }

    #sh-widget-iframe-container {
      position: fixed; ${posStr}
      ${CONFIG.position === 'bottom-right' ? 'right:24px;' : 'left:24px;'}
      bottom: 90px;
      width: 420px; height: 620px;
      max-width: calc(100vw - 48px);
      max-height: calc(100vh - 120px);
      z-index: 9999;
      border-radius: 16px;
      box-shadow: 0 20px 60px rgba(0,0,0,0.3);
      overflow: hidden;
      transform: scale(0.95) translateY(10px);
      opacity: 0;
      pointer-events: none;
      transition: transform 0.25s cubic-bezier(0.34,1.56,0.64,1), opacity 0.2s ease;
    }
    #sh-widget-iframe-container.open {
      transform: scale(1) translateY(0);
      opacity: 1;
      pointer-events: all;
    }
    #sh-widget-iframe {
      width: 100%; height: 100%; border: none;
      border-radius: 16px;
      background: #fff;
    }
    @media (max-width: 480px) {
      #sh-widget-iframe-container {
        bottom: 0 !important; right: 0 !important; left: 0 !important;
        width: 100vw; max-width: 100vw;
        height: 100vh; max-height: 100vh;
        border-radius: 0;
        top: 0;
      }
    }
  `;
  document.head.appendChild(style);

  // ── Launcher button ───────────────────────────────────────────────────
  const btn = document.createElement('button');
  btn.id = 'sh-widget-btn';
  btn.setAttribute('aria-label', CONFIG.label);
  btn.innerHTML = `<span class="sh-avatar-dot"></span>${CONFIG.label}`;
  btn.addEventListener('click', toggleWidget);
  document.body.appendChild(btn);

  // ── iframe container ──────────────────────────────────────────────────
  const container = document.createElement('div');
  container.id = 'sh-widget-iframe-container';
  document.body.appendChild(container);

  // ── Open / close ──────────────────────────────────────────────────────
  function openWidget() {
    if (!hasLoaded) {
      loadIframe();
      hasLoaded = true;
    }
    isOpen = true;
    container.classList.add('open');
    btn.innerHTML = `<span style="font-size:18px;">✕</span> Close`;

    // Notify the iframe it is now visible
    const iframe = document.getElementById('sh-widget-iframe');
    if (iframe) {
      iframe.contentWindow?.postMessage({ type: 'sh:visible' }, '*');
    }
  }

  function closeWidget() {
    isOpen = false;
    container.classList.remove('open');
    btn.innerHTML = `<span class="sh-avatar-dot"></span>${CONFIG.label}`;
  }

  function toggleWidget() {
    if (isOpen) closeWidget(); else openWidget();
  }

  // ── Lazy-load the iframe ──────────────────────────────────────────────
  function loadIframe() {
    const params = new URLSearchParams({
      persona: CONFIG.personaId,
      widget: '1',
      color: CONFIG.color,
    });
    if (CONFIG.greeting) params.set('greeting', CONFIG.greeting);

    const iframe = document.createElement('iframe');
    iframe.id = 'sh-widget-iframe';
    iframe.src = `${CONFIG.apiBase}/?${params.toString()}`;
    iframe.allow = 'microphone; camera; autoplay; display-capture';
    iframe.title = 'AI Persona Chat';
    container.appendChild(iframe);
  }

  // ── Close widget when user ends session (message from iframe) ─────────
  window.addEventListener('message', (event) => {
    if (!CONFIG.apiBase || event.origin !== new URL(CONFIG.apiBase).origin) return;
    if (event.data?.type === 'sh:session_ended') {
      closeWidget();
    }
  });

  // ── ESC key to close ──────────────────────────────────────────────────
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen) closeWidget();
  });

  // ── Auto-open if configured ───────────────────────────────────────────
  if (CONFIG.autoOpen) {
    setTimeout(openWidget, 2000);  // 2-second delay to not interrupt page load
  }

  // ── Public API ────────────────────────────────────────────────────────
  window.SuperHuman = { open: openWidget, close: closeWidget, toggle: toggleWidget };

})();
