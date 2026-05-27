/**
 * Savant.ai — Premium persona preview & immersive connect experience
 */
window.RehuCall = (function () {
  let experience = null;
  let connectTimer = null;
  let connectStep = 0;

  async function loadExperience(personaId) {
    try {
      const r = await fetch(`/api/personas/${personaId}/experience`);
      if (r.ok) experience = await r.json();
    } catch (e) {
      experience = null;
    }
    return experience;
  }

  function applyLanding(personaName, roleTitle) {
    const st = document.getElementById('av-st');
    if (st) st.textContent = roleTitle || 'Available now';
    const co = document.getElementById('cs-co');
    if (co) co.textContent = experience?.role_title || 'Specialist';
  }

  function setPreviewImage(url) {
    if (!url) return;
    const img = document.getElementById('hero-img');
    const sk = document.getElementById('av-skeleton');
    if (!img) return;

    img.src = url;
    img.onload = () => {
      img.classList.add('loaded');
      if (sk) sk.classList.add('hidden');
    };
    img.onerror = () => {
      if (sk) sk.classList.remove('hidden');
    };

    const connectImg = document.getElementById('connect-photo');
    if (connectImg) connectImg.src = url;
  }

  function showConnectOverlay(personaName) {
    const el = document.getElementById('connect-overlay');
    if (!el) return;

    const messages = experience?.connecting_messages || [
      "Hold on — we're connecting you to your specialist…",
      'Your specialist is joining now…',
      'Preparing your live conversation…',
    ];

    const photo = document.getElementById('connect-photo');
    const hero = document.getElementById('hero-img');
    if (photo) {
      photo.src = hero?.src || experience?.preview_url || '';
    }

    el.classList.add('show');
    document.body.classList.add('connecting');
    connectStep = 0;
    rotateConnectMessage(messages, personaName);

    connectTimer = setInterval(() => {
      connectStep = (connectStep + 1) % messages.length;
      rotateConnectMessage(messages, personaName);
    }, 3200);
  }

  function rotateConnectMessage(messages, personaName) {
    const title = document.getElementById('connect-title');
    const sub = document.getElementById('connect-sub');
    if (!title) return;
    title.textContent = messages[connectStep % messages.length];
    if (sub) {
      sub.textContent = experience?.role_title
        ? `${personaName} · ${experience.role_title}`
        : personaName;
    }
    const bar = document.querySelector('.connect-progress-bar');
    if (bar) {
      const pct = 20 + ((connectStep + 1) / messages.length) * 75;
      bar.style.width = `${Math.min(95, pct)}%`;
    }
  }

  function hideConnectOverlay() {
    const el = document.getElementById('connect-overlay');
    if (connectTimer) clearInterval(connectTimer);
    connectTimer = null;
    if (el) {
      el.classList.add('fade-out');
      setTimeout(() => {
        el.classList.remove('show', 'fade-out');
        document.body.classList.remove('connecting');
      }, 450);
    }
  }

  function transitionToCall() {
    const landing = document.getElementById('landing');
    const call = document.getElementById('call-screen');
    if (landing) {
      landing.classList.add('fade-out');
      setTimeout(() => {
        landing.style.display = 'none';
        landing.classList.remove('fade-out');
      }, 400);
    }
    if (call) {
      call.style.display = 'block';
      call.classList.add('fade-in');
    }
  }

  function initAlivePersona(selector) {
    const frame = document.querySelector(selector);
    const img = frame?.querySelector('img');
    if (!frame || !img) return;
    frame.addEventListener('mousemove', (e) => {
      const r = frame.getBoundingClientRect();
      const x = ((e.clientX - r.left) / r.width - 0.5) * 6;
      const y = ((e.clientY - r.top) / r.height - 0.5) * 4;
      img.style.transform = `scale(1.04) translate(${x}px, ${y}px)`;
    });
    frame.addEventListener('mouseleave', () => {
      img.style.transform = '';
    });
  }

  return {
    loadExperience,
    applyLanding,
    setPreviewImage,
    showConnectOverlay,
    hideConnectOverlay,
    transitionToCall,
    initAlivePersona,
    getExperience: () => experience,
  };
})();
