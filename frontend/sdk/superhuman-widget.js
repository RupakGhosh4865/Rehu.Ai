/**
 * Savant.ai — Embeddable Website Widget SDK
 * Drop this single script tag on any website to deploy your Savant Superhuman.
 *
 * Basic usage:
 * <script src="https://your-app.railway.app/sdk/superhuman-widget.js"
 *   data-persona="default"
 *   data-position="bottom-right"
 *   data-color="#2E86AB"
 *   data-label="Talk to our Expert"
 *   data-api="https://your-app.railway.app">
 * </script>
 *
 * In-product triggers (open automatically based on visitor behaviour):
 *   data-trigger="auto"
 *   data-trigger-delay="30"            (seconds on page)
 *   data-trigger-scroll="50"           (% of page scrolled)
 *   data-trigger-exit="true"           (exit intent — mouse leaves viewport top)
 *   data-trigger-idle="60"             (seconds with no mouse/keyboard activity)
 *   data-trigger-selector=".upgrade-btn"  (open on hover over a CSS selector)
 *
 * User context (passed to the AI as system prompt context):
 *   data-user-id="..."
 *   data-user-plan="free|pro|enterprise"
 *   data-user-stage="trial|active|churning"
 *   data-page-context="Pricing page"
 *
 * The public global remains `window.SuperHuman` for backward compatibility with
 * existing embeds; we expose `window.Savant` as an alias.
 */

(function () {
  'use strict';

  // ── Read config from script tag ──────────────────────────────────────
  const scriptTag = document.currentScript ||
    document.querySelector('script[data-persona]');

  const attr = (name, fallback) => {
    const v = scriptTag?.getAttribute(name);
    return v == null ? fallback : v;
  };
  const decoded = (name, fallback) => decodeURIComponent(attr(name, fallback));

  const CONFIG = {
    personaId:        attr('data-persona', 'default'),
    position:         attr('data-position', 'bottom-right'),
    color:            decoded('data-color', '#2E86AB'),
    label:            decoded('data-label', 'Talk to our Expert'),
    apiBase:          attr('data-api', ''),
    autoOpen:         attr('data-auto-open', '') === 'true',
    greeting:         decoded('data-greeting', ''),

    // Triggers
    trigger:          attr('data-trigger', ''),                  // "auto" enables condition-based opens
    triggerDelay:     parseInt(attr('data-trigger-delay', ''), 10),
    triggerScroll:    parseInt(attr('data-trigger-scroll', ''), 10),
    triggerExit:      attr('data-trigger-exit', '') === 'true',
    triggerIdle:      parseInt(attr('data-trigger-idle', ''), 10),
    triggerSelector:  attr('data-trigger-selector', ''),

    // User context
    userId:           attr('data-user-id', ''),
    userPlan:         attr('data-user-plan', ''),
    userStage:        attr('data-user-stage', ''),
    pageContext:      decoded('data-page-context', ''),
  };

  // ── Prevent double-init ───────────────────────────────────────────────
  if (window.__superhuman_loaded) return;
  window.__superhuman_loaded = true;

  // ── State ─────────────────────────────────────────────────────────────
  let isOpen = false;
  let hasLoaded = false;
  let activeGreeting = CONFIG.greeting;     // can be overridden by triggers
  let triggerListenersBound = false;
  const FIRED_KEY = 'sh_widget_fired';      // sessionStorage flag

  // Contextual greetings per trigger source
  const CONTEXTUAL_GREETINGS = {
    exit:     'Wait — before you go, can I answer any questions?',
    selector: 'Thinking about upgrading? I can walk you through what changes.',
    idle:     "Still exploring? I'm here if you have questions.",
    scroll:   "Looks like you're going deep — want me to summarise this for you?",
    delay:    "Hey — got a quick minute? I can save you a lot of reading.",
  };

  function hasFired() {
    try { return sessionStorage.getItem(FIRED_KEY) === '1'; } catch (_) { return false; }
  }
  function markFired() {
    try { sessionStorage.setItem(FIRED_KEY, '1'); } catch (_) { /* ignore */ }
  }

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
  function openWidget(opts) {
    opts = opts || {};
    // Allow triggers to override the greeting once before load
    if (opts.greeting) activeGreeting = opts.greeting;
    if (opts.fromTrigger) markFired();

    if (!hasLoaded) {
      loadIframe();
      hasLoaded = true;
    }
    isOpen = true;
    container.classList.add('open');
    btn.innerHTML = `<span style="font-size:18px;">✕</span> Close`;

    // Notify the iframe it is now visible (and pass updated greeting)
    const iframe = document.getElementById('sh-widget-iframe');
    if (iframe) {
      iframe.contentWindow?.postMessage(
        { type: 'sh:visible', greeting: activeGreeting || undefined },
        '*'
      );
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
    if (activeGreeting) params.set('greeting', activeGreeting);

    // User + page context as URL params (read by the call page)
    if (CONFIG.userId)      params.set('user_id', CONFIG.userId);
    if (CONFIG.userPlan)    params.set('user_plan', CONFIG.userPlan);
    if (CONFIG.userStage)   params.set('user_stage', CONFIG.userStage);
    if (CONFIG.pageContext) params.set('page', CONFIG.pageContext);

    const iframe = document.createElement('iframe');
    iframe.id = 'sh-widget-iframe';
    iframe.src = `${CONFIG.apiBase}/call?${params.toString()}`;
    iframe.allow = 'microphone; camera; autoplay; display-capture';
    iframe.title = 'Talk to our specialist';
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

  // ── Auto-open (legacy data-auto-open) ─────────────────────────────────
  if (CONFIG.autoOpen) {
    setTimeout(() => openWidget({ fromTrigger: true }), 2000);
  }

  // ── In-product triggers ───────────────────────────────────────────────
  function fireTrigger(source) {
    if (isOpen || hasFired()) return;
    const greeting = CONTEXTUAL_GREETINGS[source] || activeGreeting || '';
    openWidget({ greeting, fromTrigger: true });
  }

  function bindTriggers() {
    if (triggerListenersBound) return;
    triggerListenersBound = true;

    // Delay trigger
    if (CONFIG.triggerDelay && CONFIG.triggerDelay > 0) {
      setTimeout(() => fireTrigger('delay'), CONFIG.triggerDelay * 1000);
    }

    // Scroll-depth trigger
    if (CONFIG.triggerScroll && CONFIG.triggerScroll > 0 && CONFIG.triggerScroll <= 100) {
      const onScroll = () => {
        if (hasFired() || isOpen) {
          window.removeEventListener('scroll', onScroll);
          return;
        }
        const doc = document.documentElement;
        const scrollable = Math.max(1, (doc.scrollHeight || document.body.scrollHeight) - window.innerHeight);
        const pct = Math.min(100, (window.scrollY / scrollable) * 100);
        if (pct >= CONFIG.triggerScroll) {
          fireTrigger('scroll');
          window.removeEventListener('scroll', onScroll);
        }
      };
      window.addEventListener('scroll', onScroll, { passive: true });
    }

    // Exit-intent trigger
    if (CONFIG.triggerExit) {
      const onLeave = (e) => {
        // Only fire when the pointer leaves through the top of the viewport
        if (e.clientY <= 0 && !hasFired() && !isOpen) {
          fireTrigger('exit');
          document.removeEventListener('mouseleave', onLeave);
        }
      };
      document.addEventListener('mouseleave', onLeave);
    }

    // Idle trigger
    if (CONFIG.triggerIdle && CONFIG.triggerIdle > 0) {
      let idleTimer = null;
      const resetIdle = () => {
        if (hasFired() || isOpen) return;
        clearTimeout(idleTimer);
        idleTimer = setTimeout(() => fireTrigger('idle'), CONFIG.triggerIdle * 1000);
      };
      ['mousemove', 'mousedown', 'keydown', 'scroll', 'touchstart'].forEach((evt) =>
        window.addEventListener(evt, resetIdle, { passive: true })
      );
      resetIdle();
    }

    // Selector hover trigger
    if (CONFIG.triggerSelector) {
      try {
        const nodes = document.querySelectorAll(CONFIG.triggerSelector);
        nodes.forEach((node) => {
          const onHover = () => {
            if (!hasFired() && !isOpen) {
              fireTrigger('selector');
              node.removeEventListener('mouseenter', onHover);
            }
          };
          node.addEventListener('mouseenter', onHover, { once: true });
        });
      } catch (e) {
        console.warn('[SuperHuman] Invalid data-trigger-selector:', CONFIG.triggerSelector);
      }
    }
  }

  // Trigger logic runs after the page is ready — never blocks rendering
  if (CONFIG.trigger === 'auto' ||
      CONFIG.triggerDelay || CONFIG.triggerScroll ||
      CONFIG.triggerExit || CONFIG.triggerIdle || CONFIG.triggerSelector) {
    if (document.readyState === 'loading') {
      window.addEventListener('DOMContentLoaded', bindTriggers, { once: true });
    } else {
      bindTriggers();
    }
  }

  // ── Public API ────────────────────────────────────────────────────────
  const PublicAPI = {
    open: openWidget,
    close: closeWidget,
    toggle: toggleWidget,
    /** Programmatically open with a custom greeting (does not consume the "fired once" flag). */
    openWithGreeting: (text) => openWidget({ greeting: text }),
    /** Programmatically reset the once-per-session flag (for testing). */
    resetTriggers: () => { try { sessionStorage.removeItem(FIRED_KEY); } catch(_) {} },
  };
  window.Savant = PublicAPI;
  window.SuperHuman = PublicAPI;

})();
