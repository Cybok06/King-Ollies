/* Direct purchases share UI; each page supplies its existing payment adapter. */
(() => {
  'use strict';
  const el = document.getElementById('dataPurchaseModal');
  if (!el) return;
  const get = id => document.getElementById(id);
  const modal = new bootstrap.Modal(el, {backdrop: 'static'});
  const pay = get('dataPurchasePay');
  let service, bulk = false, busy = false, locked = false;
  const requestKey = 'direct-data-purchase:' + location.pathname;
  const recipients = () => (bulk ? get('dataPurchasePhones').value.split(/[\n,;]+/) : [get('dataPurchasePhone').value]).map(p => p.trim()).filter(Boolean);
  const error = message => { get('dataPurchaseError').textContent = message || ''; get('dataPurchaseError').hidden = !message; };
  function summary() {
    const offer = service?.offers?.[get('dataPurchaseOffer').value];
    const count = recipients().length;
    get('dataPurchaseCount').textContent = count + (count === 1 ? ' recipient' : ' recipients');
    get('dataPurchaseTotal').textContent = (Number(offer?.total ?? offer?.amount ?? 0) * count).toFixed(2);
  }
  function tab(isBulk) {
    bulk = isBulk;
    for (const [id, active] of [['dataSingleTab', !bulk], ['dataBulkTab', bulk]]) {
      get(id).classList.toggle('active', active); get(id).setAttribute('aria-selected', String(active)); get(id).tabIndex = active ? 0 : -1;
    }
    get('dataSinglePanel').hidden = bulk; get('dataBulkPanel').hidden = !bulk; summary();
  }
  get('dataSingleTab').onclick = () => tab(false);
  get('dataBulkTab').onclick = () => tab(true);
  for (const id of ['dataSingleTab', 'dataBulkTab']) get(id).addEventListener('keydown', e => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {e.preventDefault(); tab(e.key === 'End' || (e.key !== 'Home' && !bulk)); get(bulk ? 'dataBulkTab' : 'dataSingleTab').focus();}
  });
  for (const id of ['dataPurchasePhone', 'dataPurchasePhones', 'dataPurchaseOffer']) get(id).addEventListener('input', () => {error(''); summary();});
  el.addEventListener('hide.bs.modal', e => { if (busy) e.preventDefault(); });
  document.addEventListener('click', e => {
    const button = e.target.closest('.select-service-btn, .select-btn');
    if (!button || button.disabled || button.dataset.can === '0') return;
    let svc; try { svc = JSON.parse(button.dataset.service); } catch (_) {return;}
    e.preventDefault(); e.stopImmediatePropagation();
    service = svc; locked = false; error('');
    get('dataPurchaseTitle').textContent = service.name;
    get('dataPurchaseEditor').hidden = false; get('dataPurchaseResult').hidden = true;
    get('dataPurchasePhone').value = ''; get('dataPurchasePhones').value = '';
    get('dataPurchaseOffer').replaceChildren(new Option('Choose a data bundle', ''));
    (service.offers || []).forEach((offer, i) => {
      const label = offer.value_text || (offer.value?.volume ? (Number(offer.value.volume) / 1000) + ' GB' : String(offer.value || 'Bundle'));
      const option = new Option(label + ' — GHS ' + Number(offer.total ?? offer.amount ?? 0).toFixed(2), String(i));
      option.disabled = offer.amount == null || offer.total == null;
      get('dataPurchaseOffer').add(option);
    });
    get('dataPurchaseMethod').textContent = window.directDataPurchase.mode === 'store' ? 'Secure payment via Paystack. Any gateway fee is shown during payment.' : 'Payment will be deducted from your wallet.';
    tab(false); modal.show();
  }, true);
  el.addEventListener('shown.bs.modal', () => get('dataPurchasePhone').focus());
  pay.addEventListener('click', async () => {
    if (busy || locked) return;
    error('');
    const phones = recipients(), selected = get('dataPurchaseOffer').value;
    const offer = selected === '' ? null : service.offers[selected];
    if (!phones.length || phones.some(phone => !/^0\d{9}$/.test(phone))) {error('Enter valid Ghana phone numbers: 10 digits starting with 0.'); return;}
    if (new Set(phones).size !== phones.length) {error('Remove duplicate phone numbers before paying.'); return;}
    if (!offer) {error('Please select a data offer.'); return;}
    const adapter = window.directDataPurchase;
    const lines = phones.map(phone => ({serviceId: service._id_str, serviceName: service.name, phone,
      value: offer.value_text || (offer.value?.volume ? Number(offer.value.volume) / 1000 + ' GB' : 'Bundle'), value_obj: offer.value,
      base_amount: Number(offer.amount || 0), amount: Number(offer.total ?? offer.amount ?? 0), total: Number(offer.total ?? offer.amount ?? 0),
      service_network: service.service_network || '', network: service.network || '', network_group: adapter.network(service)}));
    busy = true; pay.disabled = true;
    pay.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span><span role="status">Processing…</span>';
    const fields = el.querySelectorAll('input, textarea, select, [role="tab"]');
    fields.forEach(field => {field.disabled = true;});
    el.setAttribute('aria-busy', 'true');
    try {
      for (const line of lines) {
        const eligibility = await adapter.eligibility(line.phone);
        if (!eligibility.allowed) throw new Error(eligibility.message || 'This number cannot place an order. Please contact Admin.');
      }
      // Hide the purchase modal before the existing ported-number confirmation.
      busy = false;
      await new Promise(resolve => {el.addEventListener('hidden.bs.modal', resolve, {once:true}); modal.hide();});
      busy = true;
      const confirmed = await adapter.confirm(lines);
      const portedModal = document.getElementById('portedModal');
      if (portedModal?.style.display === 'block' && !portedModal.classList.contains('show')) {
        await new Promise(resolve => portedModal.addEventListener('hidden.bs.modal', resolve, {once:true}));
      }
      modal.show();
      if (!confirmed) return;
      const signature = JSON.stringify(lines);
      let request;
      try {request = JSON.parse(sessionStorage.getItem(requestKey));} catch (_) {}
      if (!request || request.signature !== signature) {
        if (request?.payment) throw new Error('A previous paid order still needs confirmation. Re-enter those recipients and offer to retry confirmation, or contact support.');
        request = {signature, id: window.crypto.randomUUID ? crypto.randomUUID() : Date.now() + '-' + Math.random().toString(36).slice(2)};
        sessionStorage.setItem(requestKey, JSON.stringify(request));
      }
      const result = await adapter.purchase(lines, request, () => sessionStorage.setItem(requestKey, JSON.stringify(request)));
      if (!result) return; // Payment window was closed.
      if (!result.success && !result.order_recorded) throw new Error(result.message || 'Payment could not be confirmed. Please try again.');
      locked = true;
      sessionStorage.removeItem(requestKey);
      get('dataPurchaseEditor').hidden = true; get('dataPurchaseResult').hidden = false;
      get('dataPurchaseResultTitle').textContent = result.success ? 'Order Placed Successfully' : (result.rejected_count ? 'Order could not be completed' : 'Order submitted');
      get('dataPurchaseResultIcon').className = result.success ? 'bi bi-check-circle' : 'bi bi-exclamation-circle';
      get('dataPurchaseFirstTimeNote').hidden = !result.success;
      get('dataPurchaseResultMessage').textContent = result.success ? 'View your receipt for order details.' : (result.rejected_count ? 'View your receipt for the order status, or contact support for help.' : 'Confirmation is pending. View your receipt for updates.');
      const receipt = result.redirect_url || (result.order_id ? '/invoice/' + encodeURIComponent(result.order_id) : '');
      const validReceipt = receipt && receipt.startsWith('/') && !receipt.startsWith('//');
      get('dataPurchaseReceipt').hidden = !validReceipt;
      get('dataPurchaseReceipt').removeAttribute('href');
      if (validReceipt) get('dataPurchaseReceipt').href = receipt;
    } catch (e) {error(e instanceof TypeError || e instanceof SyntaxError ? 'Unable to confirm the order. Check your orders before retrying; retrying this same order will reuse its payment reference.' : (e.message || 'Unable to confirm your order. Check your orders before retrying.'));}
    finally {busy = false; pay.disabled = false; pay.textContent = 'Pay Now'; fields.forEach(field => {field.disabled = false;}); el.removeAttribute('aria-busy');}
  });
})();
