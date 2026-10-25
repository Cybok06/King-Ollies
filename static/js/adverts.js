(() => {
  const dialog = document.getElementById('advert-popup');
  if (!dialog) return;
  const page = dialog.dataset.page;
  // Treat the two public store URL aliases as the same page, but keep stores separate.
  const path = page === 'store' ? location.pathname.replace(/^\/s\//, '/store/').replace(/\/$/, '') : page;
  const identity = `${page}:${path}`;
  const day = () => new Intl.DateTimeFormat('en-CA', {timeZone:'Africa/Accra', year:'numeric', month:'2-digit', day:'2-digit'}).format(new Date());
  const key = ad => `king-ollies:advert:${ad.id}:${identity}`;
  const memory = new Map();
  const dismissed = ad => { try { return localStorage.getItem(key(ad)) === day(); } catch (_) { return memory.get(key(ad)) === day(); } };
  let ads = [], index = 0, timer, fetching = false;
  const eligible = ad => ad.active && (!ad.end_date || ad.end_date >= day()) && !dismissed(ad);
  const blocked = () => !!document.querySelector('dialog[open]:not(#advert-popup), .modal.show, .offcanvas.show');
  function render() {
    const ad = ads[index];
    document.getElementById('advert-title').textContent = ad.title;
    document.getElementById('advert-text').textContent = ad.text || '';
    const image = document.getElementById('advert-image'); image.src = ad.image_url; image.alt = ad.title;
    document.getElementById('advert-position').textContent = ads.length > 1 ? `${index + 1} / ${ads.length}` : '';
    for (const name of ['previous', 'next']) document.getElementById(`advert-${name}`).hidden = ads.length < 2;
  }
  function show() {
    clearTimeout(timer);
    if (dialog.open) return;
    ads = ads.filter(eligible); index = Math.min(index, ads.length - 1);
    if (!ads.length) return;
    if (blocked()) { timer = setTimeout(show, 500); return; }
    render(); dialog.showModal(); document.getElementById('advert-okay').focus();
  }
  async function refresh() {
    if (fetching) return;
    fetching = true;
    try {
      const response = await fetch(`/api/adverts?page=${encodeURIComponent(page)}`, {cache:'no-store'});
      if (!response.ok) return;
      const data = await response.json(); ads = data.adverts.filter(eligible); index = 0;
      if (dialog.open) { if (!ads.length) dialog.close(); else render(); }
      else show();
    } catch (_) {} finally { fetching = false; }
  }
  document.getElementById('advert-okay').addEventListener('click', () => {
    const ad = ads[index]; if (!ad) return;
    memory.set(key(ad), day()); try { localStorage.setItem(key(ad), day()); } catch (_) {}
    ads.splice(index, 1); index = Math.max(0, Math.min(index, ads.length - 1));
    if (ads.length) render(); else dialog.close();
  });
  document.getElementById('advert-close').addEventListener('click', () => dialog.close());
  for (const [name, delta] of [['previous', -1], ['next', 1]]) document.getElementById(`advert-${name}`).addEventListener('click', () => {index = (index + delta + ads.length) % ads.length; render();});
  window.addEventListener('storage', event => { if (event.key?.startsWith('king-ollies:advert:')) refresh(); });
  document.addEventListener('visibilitychange', () => {if (!document.hidden) refresh();});
  // Refresh when a tab crosses midnight, so expired adverts disappear and daily dismissal resets.
  let lastDay = day(); setInterval(() => {if (lastDay !== day()) {lastDay = day(); refresh();}}, 30000);
  refresh();
})();
