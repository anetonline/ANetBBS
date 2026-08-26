(() => {
  'use strict';
  const root = document.querySelector('.pulse');
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const duration = (seconds) => {
    if (seconds == null) return '--';
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
    return `${Math.floor(seconds / 86400)}d ${Math.floor(seconds % 86400 / 3600)}h`;
  };
  const bytes = (value) => {
    if (value == null) return '--';
    const units = ['B','KB','MB','GB','TB']; let n = value; let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i > 2 ? 1 : 0)} ${units[i]}`;
  };
  const age = (iso) => {
    if (!iso) return '';
    const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000));
    return seconds < 60 ? 'just now' : `${duration(seconds)} ago`;
  };
  const setText = (id, text) => { const el = $(id); if (el) el.textContent = text; };

  function renderCallers(data) {
    const rows = [
      ...data.nodes.map(x => ({...x, kind:'terminal'})),
      ...data.web_users.map(x => ({...x, protocol:'web', action:x.page, kind:'web'})),
    ];
    setText('caller-count', rows.length);
    setText('total-online', rows.length);
    $('live-callers').innerHTML = rows.length ? rows.map(row => `
      <div class="pulse-row">
        <div class="pulse-row-main"><span class="pulse-led"></span><div class="pulse-row-copy">
          <strong>${escapeHtml(row.username)}</strong>
          <small>${escapeHtml(row.action || row.page || 'Online')} · ${escapeHtml(age(row.last_seen))}</small>
        </div></div><span class="pulse-badge good">${escapeHtml(row.protocol || row.kind)}</span>
      </div>`).join('') : '<p class="pulse-empty">No callers online</p>';
  }

  function renderServices(services, metrics) {
    $('pulse-services').innerHTML = services.map(service => {
      const bad = !service.is_running || ['partial','all_down'].includes(service.listener_summary);
      const m = metrics?.units?.[service.unit] || {};
      const parts = [];
      if (m.cpu_pct != null) parts.push(`${Number(m.cpu_pct).toFixed(1)}% CPU`);
      if (m.rss_mb != null) parts.push(`${bytes(m.rss_mb * 1024 * 1024)} RAM`);
      const ports = service.ports || [];
      const listeners = ports.length ? `${ports.filter(p => p.up === true).length}/${ports.length} listeners` : 'no listeners';
      return `<div class="pulse-row">
        <div class="pulse-row-main"><span class="pulse-led ${bad ? 'bad' : ''}"></span><div class="pulse-row-copy">
          <strong>${escapeHtml(service.label)}</strong><small>${escapeHtml(parts.join(' · ') || listeners)}</small>
        </div></div><span class="pulse-badge ${bad ? 'bad' : 'good'}">${bad ? 'attention' : 'healthy'}</span>
      </div>`;
    }).join('');
  }

  function renderRecent(rows) {
    $('recent-callers').innerHTML = rows.length ? rows.map(row => `
      <div class="pulse-row"><div class="pulse-row-main"><span class="pulse-led warn"></span><div class="pulse-row-copy">
        <strong>${escapeHtml(row.username)}</strong><small>${escapeHtml(age(row.started_at))}${row.duration_seconds ? ` · ${duration(row.duration_seconds)}` : ''}</small>
      </div></div><span class="pulse-badge">${escapeHtml(row.protocol || 'unknown')}</span></div>`).join('')
      : '<p class="pulse-empty">No recent callers logged</p>';
  }

  function render(data) {
    const banner = $('pulse-banner');
    banner.classList.remove('pulse-loading', 'pulse-bad');
    if (!data.ok) banner.classList.add('pulse-bad');
    setText('pulse-health', data.ok ? 'All monitored systems healthy' : `${data.summary.services_unhealthy} service issue${data.summary.services_unhealthy === 1 ? '' : 's'} detected`);
    setText('pulse-updated', `Updated ${new Date(data.updated).toLocaleTimeString()}`);
    setText('pulse-bbs-name', data.bbs_name);
    setText('healthy-services', data.summary.services_healthy);
    setText('service-ratio', `of ${data.summary.services_total} healthy`);
    setText('disk-used', data.host.disk ? `${data.host.disk.percent}%` : '--');
    setText('disk-free', data.host.disk ? `${bytes(data.host.disk.free)} free` : 'unavailable');
    setText('host-uptime', duration(data.host.uptime_seconds));
    setText('pulse-version', `ANetBBS ${data.version}`);
    setText('new-users', data.totals.new_users_24h);
    setText('new-posts', data.totals.new_posts_24h);
    setText('total-users', data.totals.users);
    setText('total-posts', data.totals.posts);
    renderCallers(data); renderServices(data.services, data.metrics); renderRecent(data.recent_callers);
  }

  async function refresh() {
    const button = $('pulse-refresh'); button.classList.add('spinning'); button.disabled = true;
    try {
      const response = await fetch(root.dataset.statusUrl, {credentials:'same-origin', cache:'no-store', headers:{'Accept':'application/json'}});
      if (response.status === 401 || response.redirected) { window.location.reload(); return; }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (error) {
      $('pulse-banner').classList.remove('pulse-loading'); $('pulse-banner').classList.add('pulse-bad');
      setText('pulse-health', 'Status unavailable'); setText('pulse-updated', `${error.message} · tap refresh to retry`);
    } finally { button.classList.remove('spinning'); button.disabled = false; }
  }

  $('pulse-refresh').addEventListener('click', refresh);
  let installPrompt = null;
  window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); installPrompt = event; $('pulse-install').hidden = false; });
  $('pulse-install').addEventListener('click', async () => { if (!installPrompt) return; installPrompt.prompt(); await installPrompt.userChoice; installPrompt = null; $('pulse-install').hidden = true; });
  if ('serviceWorker' in navigator) navigator.serviceWorker.register(root.dataset.swUrl, {scope:'/admin/pulse/'}).catch(() => {});
  refresh(); setInterval(refresh, 15000);
})();

