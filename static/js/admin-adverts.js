(() => {
  const $ = id => document.getElementById(id), form = $('advert-form');
  let editing = null, imageUrl = '', imageId = '', uploading = false, saving = false, generation = 0;
  const pages = [...document.querySelectorAll('.ad-page')];
  const labels = {dashboard:'Customer Dashboard', store:'Store pages', index:'Index / Home page'};
  function message(text, error = false) {$('advert-message').hidden = false; $('advert-message').className = `alert ${error ? 'alert-danger' : 'alert-success'}`; $('advert-message').textContent = text;}
  async function api(url, method = 'GET', body) {
    const options = {method, headers:{'X-CSRF-Token':form.dataset.csrf}};
    if (body instanceof FormData) options.body = body;
    else if (body) {options.body = JSON.stringify(body); options.headers['Content-Type'] = 'application/json';}
    const response = await fetch(url, options), data = await response.json();
    if (!response.ok || data.success === false) throw new Error(data.error || 'Request failed');
    return data;
  }
  function duration() {const dated = form.querySelector('[name=duration]:checked').value === 'date'; $('ad-end').disabled = !dated; $('ad-end').required = dated;}
  form.querySelectorAll('[name=duration]').forEach(input => input.addEventListener('change', duration));
  $('ad-all').addEventListener('change', () => pages.forEach(input => input.checked = $('ad-all').checked));
  pages.forEach(input => input.addEventListener('change', () => $('ad-all').checked = pages.every(p => p.checked)));
  function reset() {generation++; form.reset(); editing = null; imageUrl = ''; imageId = ''; uploading = false; $('ad-file').disabled = false; $('ad-save').disabled = false; $('ad-preview').hidden = true; $('upload-state').textContent = ''; $('form-heading').textContent = 'Create advert'; duration();}
  $('ad-cancel').addEventListener('click', reset);
  $('ad-file').addEventListener('change', async () => {
    const file = $('ad-file').files[0]; if (!file) return;
    const current = ++generation; uploading = true; $('ad-save').disabled = true; $('ad-file').disabled = true; $('upload-state').textContent = 'Uploading image…';
    try {
      const body = new FormData(); body.append('image', file);
      const data = await api('/admin/api/adverts/upload', 'POST', body);
      if (generation !== current) return;
      imageUrl = data.image_url; imageId = data.image_id; $('ad-preview').src = imageUrl; $('ad-preview').hidden = false; $('upload-state').textContent = 'Image uploaded.';
    } catch (error) {if (generation === current) {$('upload-state').textContent = 'Upload failed. Please try again.'; message(error.message, true);}}
    finally {if (generation === current) {uploading = false; $('ad-save').disabled = false; $('ad-file').disabled = false;}}
  });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (uploading || saving) return;
    if (!imageUrl) {message('Upload an image first.', true); return;}
    const selected = pages.filter(p => p.checked).map(p => p.value);
    if (!selected.length) {message('Select at least one page.', true); return;}
    const body = {title:$('ad-title').value, text:$('ad-text').value, image_url:imageUrl, image_id:imageId, pages:selected, active:$('ad-active').checked, end_date:$('ad-end').disabled ? null : $('ad-end').value};
    saving = true; $('ad-save').disabled = true;
    try {await api(editing ? `/admin/api/adverts/${editing}` : '/admin/api/adverts', editing ? 'PUT' : 'POST', body); reset(); message('Advert saved.'); await load();}
    catch (error) {message(error.message, true);} finally {saving = false; $('ad-save').disabled = false;}
  });
  const preview = $('admin-ad-preview');
  for (const id of ['preview-close','preview-okay']) $(id).addEventListener('click', () => preview.close());
  function button(label, handler, danger = false) {const b = document.createElement('button'); b.type = 'button'; b.className = `btn btn-sm ${danger ? 'btn-outline-danger' : 'btn-outline-primary'}`; b.textContent = label; b.addEventListener('click', async () => {b.disabled = true; try {await handler();} catch(error) {message(error.message, true);} finally {b.disabled = false;}}); return b;}
  async function load() {
    try {
      const data = await api('/admin/api/adverts'); $('ad-end').min = data.today; $('advert-list').replaceChildren();
      if (!data.adverts.length) {$('advert-list').textContent = 'No adverts yet. Create your first advert.'; return;}
      for (const ad of data.adverts) {
        const row = document.createElement('article'); row.className = 'advert-row';
        const img = document.createElement('img'); img.className = 'advert-list-image'; img.src = ad.image_url; img.alt = ad.title;
        const copy = document.createElement('div'); copy.className = 'advert-row-copy';
        const title = document.createElement('h3'); title.className = 'h6'; title.textContent = ad.title;
        const status = document.createElement('div'); status.className = 'advert-status'; status.textContent = ad.end_date && ad.end_date < data.today ? 'Expired' : ad.active ? 'Active' : 'Inactive';
        const details = document.createElement('small'); details.textContent = `${ad.pages.map(p => labels[p]).join(', ')} • ${ad.end_date ? `Ends ${ad.end_date}` : 'Forever'}`;
        copy.append(title, status, details);
        const actions = document.createElement('div'); actions.className = 'advert-row-actions';
        actions.append(button('Preview', () => {$('preview-title').textContent = ad.title; $('preview-text').textContent = ad.text || ''; $('preview-image').src = ad.image_url; $('preview-image').alt = ad.title; preview.showModal();}), button('Edit', () => {
          reset(); editing = ad.id; imageUrl = ad.image_url; imageId = ad.image_id; $('ad-title').value = ad.title; $('ad-text').value = ad.text || ''; $('ad-active').checked = ad.active;
          pages.forEach(p => p.checked = ad.pages.includes(p.value)); $('ad-all').checked = pages.every(p => p.checked);
          form.querySelector(`[name=duration][value=${ad.end_date ? 'date' : 'forever'}]`).checked = true; $('ad-end').value = ad.end_date || ''; duration(); $('ad-preview').src = imageUrl; $('ad-preview').hidden = false; $('form-heading').textContent = 'Edit advert'; form.scrollIntoView({behavior:'smooth', block:'start'});
        }), button(ad.active ? 'Deactivate' : 'Activate', async () => {await api(`/admin/api/adverts/${ad.id}`, 'PATCH', {active:!ad.active}); await load();}), button('Delete', async () => {
          if (!confirm(`Delete “${ad.title}”?`)) return;
          await api(`/admin/api/adverts/${ad.id}`, 'DELETE'); if (editing === ad.id) reset(); message('Advert deleted.'); await load();
        }, true));
        row.append(img, copy, actions); $('advert-list').append(row);
      }
    } catch(error) {$('advert-list').textContent = 'Unable to load adverts.'; message(error.message, true);}
  }
  load();
})();
