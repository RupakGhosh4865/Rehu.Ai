/**
 * Rehu.ai — Live avatar preview (idle stream with natural blinking)
 * Uses the same LiveAvatar as the conversation session.
 */
window.RehuPersonaLive = (function () {
  let room = null;
  let sessionId = null;
  let mounting = false;

  function getLiveKit() {
    return window.LivekitClient || window.LiveKitClient || null;
  }

  async function mountLivePreview(containerSelector, personaId) {
    if (mounting) return;
    mounting = true;

    const container = document.querySelector(containerSelector);
    if (!container) {
      mounting = false;
      return;
    }

    const LC = getLiveKit();
    if (!LC) {
      mounting = false;
      return;
    }

    try {
      const health = await fetch('/health').then((r) => r.json());
      if (!health.services?.liveavatar_key_set) return;

      const exp = await fetch(`/api/personas/${personaId}/experience`).then((r) => r.json());
      const poster =
        container.querySelector('.persona-poster') ||
        container.querySelector('#hero-img') ||
        container.querySelector('.persona-alive-img');
      if (poster && exp.preview_url) {
        poster.src = exp.preview_url;
        poster.classList.add('loaded');
      }

      let video = container.querySelector('.persona-live-video');
      if (!video) {
        video = document.createElement('video');
        video.className = 'persona-live-video';
        video.autoplay = true;
        video.playsInline = true;
        video.muted = true;
        video.setAttribute('muted', '');
        container.appendChild(video);
      }

      const data = await fetch(`/api/personas/${personaId}/preview-session`, {
        method: 'POST',
      }).then((r) => (r.ok ? r.json() : null));

      if (!data?.livekit_url || !data.livekit_client_token) return;

      sessionId = data.session_id;
      room = new LC.Room({ adaptiveStream: true, dynacast: true });

      room.on(LC.RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === 'video') {
          track.attach(video);
          video.classList.add('active');
          if (poster) poster.classList.add('hidden');
          const sk = container.querySelector('.av-skeleton');
          if (sk) sk.classList.add('hidden');
          video.play().catch(() => {});
        }
        if (track.kind === 'audio') {
          const el = track.attach();
          el.muted = true;
          el.volume = 0;
          el.style.display = 'none';
          document.body.appendChild(el);
        }
      });

      await room.connect(data.livekit_url, data.livekit_client_token, { autoSubscribe: true });
      try {
        await room.startAudio();
      } catch (e) {
        /* preview is muted */
      }
    } catch (e) {
      console.warn('Live preview unavailable:', e);
    } finally {
      mounting = false;
    }
  }

  async function stop() {
    mounting = false;
    if (room) {
      try {
        await room.disconnect();
      } catch (e) {
        /* ignore */
      }
      room = null;
    }
    if (sessionId) {
      const sid = sessionId;
      sessionId = null;
      try {
        await fetch(`/api/sessions/${sid}`, { method: 'DELETE' });
      } catch (e) {
        /* ignore */
      }
    }
  }

  return { mountLivePreview, stop };
})();
