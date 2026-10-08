/**
 * Hikvision Events Timeline & Sequence Player
 * Custom panel & Lovelace card for Home Assistant
 */

class HikvisionTimelinePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.events = [];
    this.cameras = [];
    this.filteredEvents = [];
    this.currentIndex = 0;
    this.isPlaying = false;
    this.playSpeed = 1000;
    this.playTimer = null;
    this.selectedTarget = 'all';
    this.selectedCamera = 'all';
    this.selectedDate = 'all';
    this.isLoading = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this.init();
    }
  }

  connectedCallback() {
    this.render();
    this.setupKeyboard();
  }

  disconnectedCallback() {
    this.stopPlayback();
    if (this._keyHandler) {
      window.removeEventListener('keydown', this._keyHandler);
    }
  }

  async init() {
    await this.fetchEvents();
  }

  async fetchEvents() {
    if (!this._hass) return;
    this.isLoading = true;
    this.render();

    try {
      const res = await this._hass.fetchWithAuth('/api/hikvision_next/events?limit=300');
      if (res.ok) {
        const data = await res.json();
        this.events = data.events || [];
        this.cameras = data.cameras || [];
        this.applyFilters();
      }
    } catch (err) {
      console.error('Failed to load Hikvision events:', err);
    } finally {
      this.isLoading = false;
      this.render();
    }
  }

  bindImageFallback(imgEl, url) {
    if (!imgEl || !url) return;
    imgEl.onerror = async () => {
      imgEl.onerror = null;
      if (!this._hass) return;
      try {
        const res = await this._hass.fetchWithAuth(url);
        if (res.ok) {
          const blob = await res.blob();
          imgEl.src = URL.createObjectURL(blob);
        }
      } catch (err) {
        console.error('Image fallback error:', err);
      }
    };
  }

  applyFilters() {
    this.filteredEvents = this.events.filter(e => {
      if (this.selectedTarget !== 'all' && e.target !== this.selectedTarget) {
        return false;
      }
      if (this.selectedCamera !== 'all') {
        const key = e.camera_key || (e.device_serial ? `${e.device_serial}_${e.channel_id}` : `channel_${e.channel_id}`);
        const sel = this.selectedCamera;
        const matchesKey = key === sel;
        const matchesSerial = e.device_serial && (e.device_serial === sel || sel.startsWith(e.device_serial));
        const matchesChannel = String(e.channel_id) === sel;
        if (!matchesKey && !matchesSerial && !matchesChannel) {
          return false;
        }
      }
      if (this.selectedDate === 'today') {
        const todayStr = new Date().toISOString().split('T')[0];
        if (e.formatted_date !== todayStr) return false;
      } else if (this.selectedDate === 'yesterday') {
        const d = new Date();
        d.setDate(d.getDate() - 1);
        const yestStr = d.toISOString().split('T')[0];
        if (e.formatted_date !== yestStr) return false;
      }
      return true;
    });

    if (this.currentIndex >= this.filteredEvents.length) {
      this.currentIndex = 0;
    }
  }

  setupKeyboard() {
    this._keyHandler = (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        this.prevImage();
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        this.nextImage();
      } else if (e.key === ' ') {
        e.preventDefault();
        this.togglePlayback();
      } else if (e.key === 'f' || e.key === 'F') {
        e.preventDefault();
        this.toggleFullscreen();
      }
    };
    window.addEventListener('keydown', this._keyHandler);
  }

  prevImage() {
    if (this.filteredEvents.length === 0) return;
    this.currentIndex = (this.currentIndex - 1 + this.filteredEvents.length) % this.filteredEvents.length;
    this.updateStage();
  }

  nextImage() {
    if (this.filteredEvents.length === 0) return;
    this.currentIndex = (this.currentIndex + 1) % this.filteredEvents.length;
    this.updateStage();
  }

  goToIndex(idx) {
    if (idx >= 0 && idx < this.filteredEvents.length) {
      this.currentIndex = idx;
      this.updateStage();
    }
  }

  togglePlayback() {
    if (this.isPlaying) {
      this.stopPlayback();
    } else {
      this.startPlayback();
    }
    this.renderControls();
  }

  startPlayback() {
    this.isPlaying = true;
    this.playTimer = setInterval(() => {
      this.nextImage();
    }, this.playSpeed);
  }

  stopPlayback() {
    this.isPlaying = false;
    if (this.playTimer) {
      clearInterval(this.playTimer);
      this.playTimer = null;
    }
  }

  setSpeed(speedMs) {
    this.playSpeed = speedMs;
    if (this.isPlaying) {
      this.stopPlayback();
      this.startPlayback();
    }
  }

  toggleFullscreen() {
    const stage = this.shadowRoot.querySelector('.stage-container');
    if (!stage) return;
    if (!document.fullscreenElement) {
      stage.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  }

  async deleteCurrent() {
    const cur = this.filteredEvents[this.currentIndex];
    if (!cur) return;
    if (!confirm(`Delete image captured on ${cur.formatted_date} ${cur.formatted_time}?`)) return;

    try {
      const res = await this._hass.fetchWithAuth(
        `/api/hikvision_next/event/${cur.device_serial || 'default'}/${cur.channel_id}/${cur.filename}`,
        { method: 'DELETE' }
      );
      if (res.ok) {
        this.events = this.events.filter(e => e.id !== cur.id);
        this.applyFilters();
        this.render();
      }
    } catch (err) {
      console.error('Delete error:', err);
    }
  }

  updateStage() {
    const cur = this.filteredEvents[this.currentIndex];
    if (!cur) return;

    const imgEl = this.shadowRoot.querySelector('#stage-image');
    if (imgEl) {
      imgEl.src = cur.url;
      this.bindImageFallback(imgEl, cur.url);
    }

    const titleEl = this.shadowRoot.querySelector('#hud-title');
    if (titleEl) {
      titleEl.textContent = cur.camera_label || `${cur.camera_name} • Ch ${cur.channel_id}`;
    }

    const timeEl = this.shadowRoot.querySelector('#hud-time');
    if (timeEl) {
      timeEl.textContent = `${cur.formatted_date} ${cur.formatted_time}`;
    }

    const badgeEl = this.shadowRoot.querySelector('#hud-target-badge');
    if (badgeEl) {
      badgeEl.className = `target-badge badge-${cur.target}`;
      badgeEl.innerHTML = this.renderTargetBadgeContent(cur.target);
    }

    const counterEl = this.shadowRoot.querySelector('#hud-counter');
    if (counterEl) {
      counterEl.textContent = `${this.currentIndex + 1} / ${this.filteredEvents.length}`;
    }

    const scrubber = this.shadowRoot.querySelector('#scrubber-input');
    if (scrubber) {
      scrubber.value = this.currentIndex;
    }

    // Scroll active thumbnail into view
    const thumbs = this.shadowRoot.querySelectorAll('.thumb-card');
    thumbs.forEach((th, i) => {
      if (i === this.currentIndex) {
        th.classList.add('active');
        th.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
      } else {
        th.classList.remove('active');
      }
    });
  }

  renderTargetBadgeContent(target) {
    if (target === 'human') return '👤 Human';
    if (target === 'vehicle') return '🚗 Vehicle';
    if (target === 'movement') return '🍃 Movement';
    return `⚡ ${target}`;
  }

  renderControls() {
    const playBtn = this.shadowRoot.querySelector('#btn-play');
    if (playBtn) {
      playBtn.innerHTML = this.isPlaying ? '⏸' : '▶';
      playBtn.title = this.isPlaying ? 'Pause (Space)' : 'Play Sequence (Space)';
    }
  }

  render() {
    const cur = this.filteredEvents[this.currentIndex];
    const total = this.filteredEvents.length;

    // Unique cameras list for dropdown (prioritize backend list of all cameras)
    const cameraMap = new Map();
    (this.cameras || []).forEach(c => {
      cameraMap.set(c.key, { key: c.key, label: c.label || c.name });
    });

    (this.events || []).forEach(e => {
      const key = e.camera_key || (e.device_serial ? `${e.device_serial}_${e.channel_id}` : `channel_${e.channel_id}`);
      if (!cameraMap.has(key)) {
        let label = e.camera_label;
        if (!label) {
          const name = e.camera_name || `Camera ${e.channel_id}`;
          if (e.device_serial) {
            const shortSerial = e.device_serial.length > 16 ? e.device_serial.slice(-8).toUpperCase() : e.device_serial.toUpperCase();
            label = `${name} (${shortSerial} • Ch ${e.channel_id})`;
          } else {
            label = `${name} (Ch ${e.channel_id})`;
          }
        }
        cameraMap.set(key, { key, label });
      }
    });

    if (this._hass && this._hass.states) {
      Object.keys(this._hass.states).forEach(entityId => {
        if (entityId.startsWith('camera.')) {
          const stateObj = this._hass.states[entityId];
          const attrs = (stateObj && stateObj.attributes) || {};
          const match = entityId.match(/^camera\.([a-z0-9_]+)_(101|1)$/);
          if (match) {
            const serialSlug = match[1];
            const camKey = `${serialSlug}_1`;
            if (!cameraMap.has(camKey)) {
              const friendlyName = attrs.friendly_name || serialSlug;
              cameraMap.set(camKey, { key: camKey, label: friendlyName });
            }
          }
        }
      });
    }

    const cameras = Array.from(cameraMap.values());

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          color: #f1f5f9;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, Helvetica, Arial, sans-serif;
          background: #090d16;
          min-height: 100vh;
          box-sizing: border-box;
          padding: 16px;
        }

        * {
          box-sizing: border-box;
        }

        .header {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          padding: 16px 20px;
          background: rgba(17, 24, 39, 0.7);
          backdrop-filter: blur(16px);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 14px;
          margin-bottom: 16px;
          box-shadow: 0 8px 32px rgba(0, 0, 0, 0.37);
        }

        .title-group {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .camera-icon {
          width: 38px;
          height: 38px;
          background: linear-gradient(135deg, #3b82f6, #6366f1);
          border-radius: 10px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 20px;
          box-shadow: 0 0 16px rgba(99, 102, 241, 0.4);
        }

        .title-text h1 {
          margin: 0;
          font-size: 1.25rem;
          font-weight: 700;
          letter-spacing: -0.02em;
          background: linear-gradient(to right, #f8fafc, #cbd5e1);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }

        .title-text p {
          margin: 2px 0 0 0;
          font-size: 0.8rem;
          color: #94a3b8;
        }

        .filters-group {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 10px;
        }

        .pill-group {
          display: flex;
          background: rgba(15, 23, 42, 0.8);
          padding: 4px;
          border-radius: 10px;
          border: 1px solid rgba(255, 255, 255, 0.06);
        }

        .pill-btn {
          background: transparent;
          border: none;
          color: #94a3b8;
          padding: 6px 12px;
          font-size: 0.82rem;
          font-weight: 500;
          border-radius: 7px;
          cursor: pointer;
          transition: all 0.2s ease;
          display: flex;
          align-items: center;
          gap: 5px;
        }

        .pill-btn:hover {
          color: #f8fafc;
          background: rgba(255, 255, 255, 0.05);
        }

        .pill-btn.active {
          color: #fff;
          background: #3b82f6;
          box-shadow: 0 0 12px rgba(59, 130, 246, 0.4);
        }

        select.custom-select {
          background: rgba(15, 23, 42, 0.8);
          color: #f1f5f9;
          border: 1px solid rgba(255, 255, 255, 0.1);
          padding: 7px 12px;
          border-radius: 8px;
          font-size: 0.82rem;
          cursor: pointer;
          outline: none;
        }

        .btn-refresh {
          background: rgba(30, 41, 59, 0.7);
          border: 1px solid rgba(255, 255, 255, 0.1);
          color: #f1f5f9;
          padding: 7px 12px;
          border-radius: 8px;
          cursor: pointer;
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 0.82rem;
          transition: background 0.2s;
        }

        .btn-refresh:hover {
          background: rgba(51, 65, 85, 0.9);
        }

        /* STAGE CONTAINER */
        .stage-container {
          position: relative;
          width: 100%;
          height: calc(100vh - 350px);
          min-height: 380px;
          background: #020617;
          border-radius: 14px;
          border: 1px solid rgba(255, 255, 255, 0.08);
          overflow: hidden;
          display: flex;
          align-items: center;
          justify-content: center;
          box-shadow: 0 12px 40px rgba(0, 0, 0, 0.6);
          margin-bottom: 14px;
        }

        .stage-image {
          max-width: 100%;
          max-height: 100%;
          object-fit: contain;
          transition: transform 0.15s ease;
        }

        .stage-empty {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 12px;
          color: #64748b;
          text-align: center;
          padding: 24px;
        }

        .stage-empty-icon {
          font-size: 48px;
        }

        /* HUD OVERLAYS */
        .hud-overlay {
          position: absolute;
          inset: 0;
          pointer-events: none;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          padding: 16px;
        }

        .hud-top {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
        }

        .hud-card {
          pointer-events: auto;
          background: rgba(15, 23, 42, 0.75);
          backdrop-filter: blur(12px);
          border: 1px solid rgba(255, 255, 255, 0.1);
          padding: 6px 12px;
          border-radius: 8px;
          font-size: 0.85rem;
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .target-badge {
          font-weight: 600;
          padding: 3px 8px;
          border-radius: 6px;
          font-size: 0.75rem;
          letter-spacing: 0.02em;
        }

        .badge-human {
          background: rgba(16, 185, 129, 0.2);
          color: #34d399;
          border: 1px solid rgba(16, 185, 129, 0.35);
        }

        .badge-vehicle {
          background: rgba(56, 189, 248, 0.2);
          color: #38bdf8;
          border: 1px solid rgba(56, 189, 248, 0.35);
        }

        .badge-movement {
          background: rgba(245, 158, 11, 0.2);
          color: #fbbf24;
          border: 1px solid rgba(245, 158, 11, 0.35);
        }

        .hud-bottom {
          display: flex;
          justify-content: space-between;
          align-items: flex-end;
        }

        /* PLAYBACK CONTROLS BAR */
        .controls-bar {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          padding: 12px 18px;
          background: rgba(17, 24, 39, 0.75);
          backdrop-filter: blur(16px);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 12px;
          margin-bottom: 14px;
        }

        .playback-buttons {
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .ctrl-btn {
          background: rgba(30, 41, 59, 0.8);
          border: 1px solid rgba(255, 255, 255, 0.1);
          color: #f1f5f9;
          width: 36px;
          height: 36px;
          border-radius: 8px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 15px;
          cursor: pointer;
          transition: all 0.15s ease;
        }

        .ctrl-btn:hover {
          background: rgba(59, 130, 246, 0.8);
          border-color: #3b82f6;
          color: #fff;
        }

        .ctrl-btn.btn-play-main {
          width: 42px;
          height: 42px;
          background: #3b82f6;
          border-color: #60a5fa;
          font-size: 18px;
          box-shadow: 0 0 14px rgba(59, 130, 246, 0.5);
        }

        .scrubber-container {
          flex: 1;
          min-width: 200px;
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 0 12px;
        }

        .scrubber-slider {
          width: 100%;
          -webkit-appearance: none;
          background: rgba(51, 65, 85, 0.6);
          height: 6px;
          border-radius: 3px;
          outline: none;
          cursor: pointer;
        }

        .scrubber-slider::-webkit-slider-thumb {
          -webkit-appearance: none;
          width: 16px;
          height: 16px;
          border-radius: 50%;
          background: #38bdf8;
          box-shadow: 0 0 10px #38bdf8;
          cursor: pointer;
          transition: transform 0.1s;
        }

        .scrubber-slider::-webkit-slider-thumb:hover {
          transform: scale(1.2);
        }

        /* FILMSTRIP CAROUSEL */
        .filmstrip-container {
          padding: 12px;
          background: rgba(17, 24, 39, 0.6);
          backdrop-filter: blur(12px);
          border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 12px;
        }

        .filmstrip-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 8px;
          font-size: 0.8rem;
          color: #94a3b8;
        }

        .filmstrip-track {
          display: flex;
          gap: 10px;
          overflow-x: auto;
          padding-bottom: 8px;
          scrollbar-width: thin;
          scrollbar-color: rgba(99, 102, 241, 0.4) transparent;
        }

        .thumb-card {
          flex: 0 0 140px;
          height: 95px;
          background: #020617;
          border-radius: 8px;
          border: 2px solid transparent;
          overflow: hidden;
          cursor: pointer;
          position: relative;
          transition: all 0.18s ease;
        }

        .thumb-card:hover {
          border-color: rgba(56, 189, 248, 0.6);
          transform: translateY(-2px);
        }

        .thumb-card.active {
          border-color: #38bdf8;
          box-shadow: 0 0 16px rgba(56, 189, 248, 0.5);
          transform: scale(1.02);
        }

        .thumb-card img {
          width: 100%;
          height: 100%;
          object-fit: cover;
        }

        .thumb-overlay {
          position: absolute;
          inset: 0;
          background: linear-gradient(to top, rgba(0,0,0,0.85) 0%, transparent 60%);
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          padding: 4px 6px;
        }

        .thumb-time {
          font-size: 0.7rem;
          color: #f1f5f9;
          font-weight: 500;
        }

        .thumb-badge {
          align-self: flex-start;
          font-size: 0.62rem;
          padding: 2px 5px;
          border-radius: 4px;
          font-weight: 600;
        }
      </style>

      <div class="header">
        <div class="title-group">
          <div class="camera-icon">📹</div>
          <div class="title-text">
            <h1>Hikvision Timeline</h1>
            <p>Chronological sequence viewer & detection filter</p>
          </div>
        </div>

        <div class="filters-group">
          <!-- Target pills -->
          <div class="pill-group">
            <button class="pill-btn ${this.selectedTarget === 'all' ? 'active' : ''}" data-target="all">All</button>
            <button class="pill-btn ${this.selectedTarget === 'human' ? 'active' : ''}" data-target="human">👤 Human</button>
            <button class="pill-btn ${this.selectedTarget === 'vehicle' ? 'active' : ''}" data-target="vehicle">🚗 Vehicle</button>
            <button class="pill-btn ${this.selectedTarget === 'movement' ? 'active' : ''}" data-target="movement">🍃 Movement</button>
          </div>

          <!-- Camera selector -->
          <select class="custom-select" id="select-camera">
            <option value="all">All Cameras</option>
            ${cameras.map(c => `<option value="${c.key}" ${this.selectedCamera === c.key ? 'selected' : ''}>${c.label}</option>`).join('')}
          </select>

          <!-- Date filter -->
          <select class="custom-select" id="select-date">
            <option value="all" ${this.selectedDate === 'all' ? 'selected' : ''}>All Dates</option>
            <option value="today" ${this.selectedDate === 'today' ? 'selected' : ''}>Today</option>
            <option value="yesterday" ${this.selectedDate === 'yesterday' ? 'selected' : ''}>Yesterday</option>
          </select>

          <button class="btn-refresh" id="btn-refresh" title="Refresh events">
            🔄 Refresh
          </button>
        </div>
      </div>

      <!-- MAIN STAGE PLAYER -->
      <div class="stage-container">
        ${cur ? `
          <img class="stage-image" id="stage-image" src="${cur.url}" alt="Event snapshot" />
          <div class="hud-overlay">
            <div class="hud-top">
              <div class="hud-card" id="hud-title">${cur.camera_label || `${cur.camera_name} • Ch ${cur.channel_id}`}</div>
              <div class="hud-card">
                <span class="target-badge badge-${cur.target}" id="hud-target-badge">
                  ${this.renderTargetBadgeContent(cur.target)}
                </span>
              </div>
            </div>
            <div class="hud-bottom">
              <div class="hud-card" id="hud-time">${cur.formatted_date} ${cur.formatted_time}</div>
              <div class="hud-card" id="hud-counter">${this.currentIndex + 1} / ${total}</div>
            </div>
          </div>
        ` : `
          <div class="stage-empty">
            <div class="stage-empty-icon">📷</div>
            <h3>${this.isLoading ? 'Loading event snapshots...' : 'No event images found'}</h3>
            <p>Snapshots captured by Hikvision cameras will appear here in chronological sequence.</p>
          </div>
        `}
      </div>

      <!-- CONTROLS BAR -->
      <div class="controls-bar">
        <div class="playback-buttons">
          <button class="ctrl-btn" id="btn-prev" title="Previous Image (←)">◀</button>
          <button class="ctrl-btn btn-play-main" id="btn-play" title="Play Sequence (Space)">
            ${this.isPlaying ? '⏸' : '▶'}
          </button>
          <button class="ctrl-btn" id="btn-next" title="Next Image (→)">▶</button>

          <select class="custom-select" id="select-speed" title="Playback speed">
            <option value="500" ${this.playSpeed === 500 ? 'selected' : ''}>0.5s</option>
            <option value="1000" ${this.playSpeed === 1000 ? 'selected' : ''}>1.0s</option>
            <option value="2000" ${this.playSpeed === 2000 ? 'selected' : ''}>2.0s</option>
          </select>
        </div>

        <div class="scrubber-container">
          <input
            type="range"
            class="scrubber-slider"
            id="scrubber-input"
            min="0"
            max="${Math.max(0, total - 1)}"
            value="${this.currentIndex}"
            ${total <= 1 ? 'disabled' : ''}
          />
        </div>

        <div class="playback-buttons">
          ${cur ? `
            <a href="${cur.url}" download="${cur.filename}" class="ctrl-btn" title="Download Image" target="_blank">⬇</a>
            <button class="ctrl-btn" id="btn-delete" title="Delete Image">🗑</button>
          ` : ''}
          <button class="ctrl-btn" id="btn-fs" title="Fullscreen (F)">⛶</button>
        </div>
      </div>

      <!-- FILMSTRIP CAROUSEL -->
      ${total > 0 ? `
        <div class="filmstrip-container">
          <div class="filmstrip-header">
            <span>Event Sequence (${total} items)</span>
            <span>Use ← / → keys to navigate</span>
          </div>
          <div class="filmstrip-track">
            ${this.filteredEvents.map((e, idx) => `
              <div class="thumb-card ${idx === this.currentIndex ? 'active' : ''}" data-idx="${idx}">
                <img src="${e.url}" loading="lazy" alt="thumb" />
                <div class="thumb-overlay">
                  <span class="thumb-badge badge-${e.target}">
                    ${e.target === 'human' ? '👤' : (e.target === 'vehicle' ? '🚗' : '🍃')}
                  </span>
                  <span class="thumb-time">${e.formatted_time}</span>
                </div>
              </div>
            `).join('')}
          </div>
        </div>
      ` : ''}
    `;

    this.attachEvents();
  }

  attachEvents() {
    // Pill filters
    this.shadowRoot.querySelectorAll('.pill-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        this.selectedTarget = btn.dataset.target;
        this.applyFilters();
        this.render();
      });
    });

    // Camera filter
    const camSelect = this.shadowRoot.querySelector('#select-camera');
    if (camSelect) {
      camSelect.addEventListener('change', () => {
        this.selectedCamera = camSelect.value;
        this.applyFilters();
        this.render();
      });
    }

    // Date filter
    const dateSelect = this.shadowRoot.querySelector('#select-date');
    if (dateSelect) {
      dateSelect.addEventListener('change', () => {
        this.selectedDate = dateSelect.value;
        this.applyFilters();
        this.render();
      });
    }

    // Refresh
    const refBtn = this.shadowRoot.querySelector('#btn-refresh');
    if (refBtn) {
      refBtn.addEventListener('click', () => this.fetchEvents());
    }

    // Prev / Next / Play
    const prevBtn = this.shadowRoot.querySelector('#btn-prev');
    if (prevBtn) prevBtn.addEventListener('click', () => this.prevImage());

    const nextBtn = this.shadowRoot.querySelector('#btn-next');
    if (nextBtn) nextBtn.addEventListener('click', () => this.nextImage());

    const playBtn = this.shadowRoot.querySelector('#btn-play');
    if (playBtn) playBtn.addEventListener('click', () => this.togglePlayback());

    // Speed
    const speedSelect = this.shadowRoot.querySelector('#select-speed');
    if (speedSelect) {
      speedSelect.addEventListener('change', () => {
        this.setSpeed(parseInt(speedSelect.value, 10));
      });
    }

    // Scrubber
    const scrubber = this.shadowRoot.querySelector('#scrubber-input');
    if (scrubber) {
      scrubber.addEventListener('input', (e) => {
        this.goToIndex(parseInt(e.target.value, 10));
      });
    }

    // Fullscreen
    const fsBtn = this.shadowRoot.querySelector('#btn-fs');
    if (fsBtn) fsBtn.addEventListener('click', () => this.toggleFullscreen());

    // Delete
    const delBtn = this.shadowRoot.querySelector('#btn-delete');
    if (delBtn) delBtn.addEventListener('click', () => this.deleteCurrent());

    // Filmstrip clicks
    this.shadowRoot.querySelectorAll('.thumb-card').forEach(th => {
      th.addEventListener('click', () => {
        const idx = parseInt(th.dataset.idx, 10);
        this.goToIndex(idx);
      });
    });

    // Image fallbacks for stage and filmstrip
    const stageImg = this.shadowRoot.querySelector('#stage-image');
    if (stageImg) {
      this.bindImageFallback(stageImg, stageImg.getAttribute('src'));
    }
    this.shadowRoot.querySelectorAll('.thumb-card img').forEach(img => {
      this.bindImageFallback(img, img.getAttribute('src'));
    });
  }
}

customElements.define('hikvision-timeline-panel', HikvisionTimelinePanel);
customElements.define('hikvision-timeline-card', HikvisionTimelinePanel);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'hikvision-timeline-card',
  name: 'Hikvision Timeline Card',
  description: 'Interactive timeline sequence viewer for Hikvision event images with detection filters.'
});
