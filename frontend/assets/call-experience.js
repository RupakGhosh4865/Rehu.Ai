/**
 * Rehu.ai — Premium persona preview & immersive connect experience
 */
window.RehuCall = (function () {
  let experience = null;
  let connectTimer = null;
  let connectStep = 0;
  const CONNECT_STEPS = [
    'Securing your session',
    'Initializing voice & video',
    'Loading persona knowledge',
    'Finalizing connection',
  ];

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
    if (st) st.textContent = roleTitle || 'SuperHuman Specialist · Available';
    const co = document.getElementById('cs-co');
    if (co && experience?.role_title) co.textContent = experience.role_title;
  }

  function setPreviewImage(url) {
    if (!url) return;
    const img = document.getElementById('hero-img');
    const sk = document.getElementById('av-skeleton');
    if (!img) return;
    const preload = new Image();
    preload.onload = () => {
      img.src = url;
      img.classList.add('loaded');
      if (sk) sk.classList.add('hidden');
    };
    preload.src = url;
    const connectImg = document.getElementById('connect-photo');
    if (connectImg) connectImg.src = url;
  }

  function showConnectOverlay(personaName) {
    const el = document.getElementById('connect-overlay');
    if (!el) return;
    const messages = experience?.connecting_messages || [
      `SuperHuman ${personaName} is joining the call in a few seconds…`,
      'Connecting you with your AI specialist…',
    ];
    const photo = document.getElementById('connect-photo');
    const hero = document.getElementById('hero-img');
    if (photo && hero?.src) photo.src = hero.src;

    el.classList.add('show');
    document.body.classList.add('connecting');
    connectStep = 0;
    rotateConnectMessage(messages, personaName);
    updateConnectSteps(0);
    connectTimer = setInterval(() => {
      connectStep = (connectStep + 1) % messages.length;
      rotateConnectMessage(messages, personaName);
      updateConnectSteps(Math.min(3, Math.floor(connectStep / 1) + 1));
    }, 2800);
  }

  function rotateConnectMessage(messages, personaName) {
    const title = document.getElementById('connect-title');
    const sub = document.getElementById('connect-sub');
    if (!title) return;
    const msg = messages[connectStep % messages.length];
    title.textContent = msg;
    if (sub) {
      sub.textContent = experience?.role_title
        ? `${experience.role_title} · ${personaName}`
        : `SuperHuman ${personaName}`;
    }
    const bar = document.querySelector('.connect-progress-bar');
    if (bar) bar.style.width = `${Math.min(95, 25 + connectStep * 22)}%`;
  }

  function updateConnectSteps(active) {
    document.querySelectorAll('.connect-step').forEach((li, i) => {
      li.classList.toggle('done', i < active);
      li.classList.toggle('active', i === active);
    });
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
      setTimeout(() => { landing.style.display = 'none'; landing.classList.remove('fade-out'); }, 400);
    }
    if (call) {
      call.style.display = 'block';
      call.classList.add('fade-in');
    }
  }

  return {
    loadExperience,
    applyLanding,
    setPreviewImage,
    showConnectOverlay,
    hideConnectOverlay,
    transitionToCall,
    getExperience: () => experience,
  };
})();
