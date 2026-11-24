// Momentum Bridge: page agent. Runs inside the signed-in Momentum tab (page context) and only exposes a fixed set of
// read and compose actions. Nothing here accepts arbitrary code from the local server.
(() => {
  const VERSION = 6;
  if (window.__momentumAgent && window.__momentumAgent.version === VERSION) return;
  // Chrome throttles timers in tabs that have been hidden for a while (to about one tick a minute), which would make
  // every wait crawl. While the tab is hidden, wait by yielding to the event loop instead of using timers.
  const sleep = ms => {
    if (document.visibilityState === 'visible') return new Promise(r => setTimeout(r, ms));
    return new Promise(resolve => {
      const end = Date.now() + ms, channel = new MessageChannel();
      channel.port1.onmessage = () => { if (Date.now() >= end) { channel.port1.close(); resolve(); } else channel.port2.postMessage(0); };
      channel.port2.postMessage(0);
    });
  };
  async function until(fn, ms, label) {
    const t0 = Date.now();
    for (;;) {
      let v = null;
      try { v = fn(); } catch (e) { v = null; }
      if (v) return v;
      if (Date.now() - t0 > (ms || 25000)) throw new Error('Timed out waiting for ' + (label || 'the CRM page'));
      await sleep(150);
    }
  }
  const leadFrame = () => document.getElementById('main_iframe');
  const leadDoc = () => leadFrame().contentDocument;
  const historyDoc = () => leadDoc().querySelector('iframe#cap_tab_iframe').contentDocument.querySelector('iframe[src*=customer_history_followup]').contentDocument;
  const composerFrame = () => document.getElementById('modalDialog0');
  const composerDoc = () => { const f = composerFrame(); return f && f.offsetParent ? f.contentDocument : null; };
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const activitiesDoc = () => leadDoc().querySelector('iframe[name=cap_activities_iframe]').contentDocument;
  const logCallDoc = () => { const f = activitiesDoc().getElementById('activities_iframe'); const d = f && f.contentDocument; return d && d.querySelector('select[name="logCallView.callResultID"]') ? d : null; };
  // A message box from the CRM (for example a compliance warning) must never be answered automatically: record it,
  // refuse any confirmation, and report it as an error instead.
  function guardDialogs(win) {
    const seen = [];
    win.alert = m => { seen.push(String(m)); };
    win.confirm = m => { seen.push(String(m)); return false; };
    return seen;
  }

  function smsState() {
    const c = composerDoc();
    if (!c || !c.querySelector('#smsTemplate')) throw new Error('The text message window is not open');
    const to = c.querySelector('#deliverTo');
    const from = c.querySelector('#deliveredFrom');
    return {
      from: from && from.selectedOptions[0] ? clean(from.selectedOptions[0].text) : '',
      to: to ? to.value : '',
      toClass: to ? to.className : '',
      toOptions: to ? [...to.options].map(o => o.value) : [],
      body: (c.querySelector('textarea[name=emailBody]') || {}).value || ''
    };
  }

  function logCallState() {
    const c = logCallDoc();
    if (!c) throw new Error('The Log Call panel is not open');
    const pick = n => c.querySelector(`[name="${n}"]`);
    const result = pick('logCallView.callResultID');
    return {
      incoming: pick('logCallView.incoming').value,
      video: pick('logCallView.isVideoCall').checked,
      type: pick('logCallView.callTypeID').value,
      phone: pick('logCallView.contactedPhone').value,
      result: result.value,
      resultText: result.selectedOptions[0] ? clean(result.selectedOptions[0].text) : '',
      note: pick('logCallView.note').value
    };
  }

  function closeComposerNow() {
    const f = composerFrame();
    const dialog = f && f.closest('.ui-dialog');
    const x = dialog && dialog.querySelector('.ui-dialog-titlebar-close, .ui-dialog-titlebar button');
    if (x) x.click();
  }
  const composerGone = () => until(() => { const f = composerFrame(); return !f || !f.offsetParent; }, 15000, 'the email window to close');

  function composerState() {
    const c = composerDoc();
    if (!c) throw new Error('The email window is not open');
    const to = c.querySelector('select[name=to]');
    const body = c.querySelector('.fr-element');
    return {
      toClass: to ? to.className : '',
      toText: to && to.selectedOptions[0] ? clean(to.selectedOptions[0].text) : '',
      cc: [...c.querySelectorAll('input[name=cc],input[name=bcc]')].map(i => i.value.trim()),
      subject: (c.querySelector('input[name=subject]') || {}).value || '',
      body: body ? body.innerText : ''
    };
  }

  window.__momentumAgent = {
    version: VERSION,
    async ping() {
      const u = document.getElementById('username');
      return { shell: !!leadFrame(), account: u ? u.textContent.split(':')[0].trim() : null };
    },
    async openLead(cid) {
      if (!/^\d{1,12}$/.test(String(cid))) throw new Error('Invalid lead id');
      closeComposerNow();
      await composerGone();
      const f = leadFrame();
      // Mark the page being left, so reopening the same lead never reads the old page before the new one loads.
      try { f.contentWindow.document.__momentumStale = true; } catch (e) { /* not loaded yet */ }
      f.contentWindow.location.href = '/acrm/servlet/customer?cmd=viewcust&cid=' + encodeURIComponent(cid);
      await until(() => {
        const w = f.contentWindow;
        return !w.document.__momentumStale && w.location.href.includes('cid=' + cid) && w.document.readyState === 'complete' && w.document.getElementById('display_emailaddress');
      }, 25000, 'the lead page');
      // Contact History must be fully loaded before anything is decided from it.
      await until(() => { const h = historyDoc(); return h.readyState === 'complete' && h.querySelector('table'); }, 25000, 'Contact History');
      return true;
    },
    async readLead(cid) {
      await this.openLead(cid);
      const d = leadDoc();
      const pref = [...d.querySelectorAll('button.preferred.email')];
      const sp = d.querySelector('#select_salesperson');
      // The same frame also lists the Scheduled Follow-up panel (planned tasks, sched_followup icon): those are not contact.
      const rows = [...historyDoc().querySelectorAll('table.rowtable tr')].filter(r => r.querySelector('td.cap_hist_col_2') && !r.querySelector('img[src*="sched_followup"]')).map(r => ({
        when: clean(r.querySelector('td.cap_hist_col_2').innerText),
        method: clean(r.querySelector('td.cap_hist_col_3').innerText),
        by: clean(r.querySelector('td.cap_hist_col_4').innerText),
        text: clean(r.innerText)
      }));
      return {
        email: (d.getElementById('display_emailaddress').value || '').trim(),
        optOut: pref.length === 1 ? pref[0].classList.contains('dont') : null,
        assigned: sp && sp.selectedOptions[0] ? sp.selectedOptions[0].text.trim() : null,
        phones: ['home', 'work', 'cell'].map(kind => {
          const input = d.getElementById(`custView.${kind}Phone.phone`);
          const item = input && input.closest('.input-item');
          const call = item && item.querySelector('button.preferred.call');
          const text = item && item.querySelector('button.preferred.text');
          return { kind, phone: input ? input.value.trim() : '', call: call ? call.className : '', text: text ? text.className : '' };
        }).filter(p => p.phone),
        name: clean((d.body.innerText || '').split('\n')[0]),
        history: rows
      };
    },
    async openComposer() {
      const act = leadDoc().querySelector('iframe[name=cap_activities_iframe]').contentDocument;
      act.getElementById('email').click();
      const c = await until(() => { const doc = composerDoc(); const sel = doc && doc.querySelector('select[name=templateId]'); return sel && sel.options.length > 1 && doc; }, 25000, 'the saved templates');
      const options = [...c.querySelector('select[name=templateId]').options].filter(o => o.value && !o.disabled).map(o => ({ id: o.value, name: clean(o.text) }));
      return { templates: options };
    },
    async selectTemplate(id) {
      const c = composerDoc();
      if (!c) throw new Error('The email window is not open');
      const sel = c.querySelector('select[name=templateId]');
      if (![...sel.options].some(o => o.value === String(id))) throw new Error('Saved template not found');
      sel.value = String(id);
      sel.dispatchEvent(new Event('change', { bubbles: true }));
      await until(() => { const s = composerState(); return s.subject.trim() && s.body.trim(); }, 20000, 'the template to fill in');
      return composerState();
    },
    async composerState() { return composerState(); },
    async sendComposer() {
      const c = composerDoc();
      if (!c) throw new Error('The email window is not open');
      const btn = [...c.querySelectorAll('button[type=submit]')].find(b => /send/i.test(b.textContent));
      if (!btn) throw new Error('Send button not found');
      btn.click();
      await composerGone();
      return true;
    },
    async closeComposer() { closeComposerNow(); await composerGone(); return true; },
    // --- text messages (Send Text Message dialog) ---
    async openSms() {
      closeComposerNow();
      await composerGone();
      activitiesDoc().getElementById('sms').click();
      const c = await until(() => { const doc = composerDoc(); const sel = doc && doc.querySelector('#smsTemplate'); return sel && sel.options.length > 1 && doc; }, 25000, 'the text message window');
      return { templates: [...c.querySelector('#smsTemplate').options].filter(o => o.value && !o.disabled).map(o => ({ id: o.value, name: clean(o.text) })) };
    },
    async selectSmsTemplate(id) {
      const c = composerDoc();
      const sel = c && c.querySelector('#smsTemplate');
      if (!sel) throw new Error('The text message window is not open');
      if (![...sel.options].some(o => o.value === String(id))) throw new Error('Saved text template not found');
      sel.value = String(id);
      sel.dispatchEvent(new Event('change', { bubbles: true }));
      await until(() => smsState().body.trim(), 20000, 'the text template to fill in');
      return smsState();
    },
    async smsState() { return smsState(); },
    async sendSms() {
      const c = composerDoc();
      if (!c || !c.querySelector('#smsTemplate')) throw new Error('The text message window is not open');
      const btn = [...c.querySelectorAll('button[type=submit]')].find(b => /send/i.test(b.textContent));
      if (!btn) throw new Error('Send button not found');
      const seen = guardDialogs(composerFrame().contentWindow);
      btn.click();
      await until(() => seen.length || !composerFrame() || !composerFrame().offsetParent, 30000, 'the text message window to close');
      if (seen.length) throw new Error('Momentum said: ' + seen.join(' / '));
      return true;
    },
    // --- call logs (Log Call panel) ---
    async openLogCall() {
      closeComposerNow();
      await composerGone();
      activitiesDoc().getElementById('log_call').click();
      const c = await until(() => logCallDoc(), 25000, 'the Log Call panel');
      const opts = n => [...c.querySelector(`[name="${n}"]`).options].filter(o => o.value).map(o => ({ id: o.value, name: clean(o.text) }));
      return { types: opts('logCallView.callTypeID'), results: opts('logCallView.callResultID'), phones: opts('logCallView.contactedPhone') };
    },
    async fillLogCall(spec) {
      const c = logCallDoc();
      if (!c) throw new Error('The Log Call panel is not open');
      const set = (n, v) => {
        const el = c.querySelector(`[name="${n}"]`);
        if (el.tagName === 'SELECT' && ![...el.options].some(o => o.value === String(v))) throw new Error('Momentum does not offer ' + n.split('.').pop() + ' ' + v);
        el.value = String(v);
        el.dispatchEvent(new Event('change', { bubbles: true }));
      };
      set('logCallView.incoming', 'false');
      const video = c.querySelector('[name="logCallView.isVideoCall"]');
      if (video.checked) video.click();
      set('logCallView.callTypeID', spec.type);
      set('logCallView.contactedPhone', spec.phone);
      set('logCallView.callResultID', spec.result);
      c.querySelector('[name="logCallView.note"]').value = String(spec.note || '');
      return logCallState();
    },
    async logCallState() { return logCallState(); },
    async saveLogCall() {
      const c = logCallDoc();
      if (!c) throw new Error('The Log Call panel is not open');
      const btn = [...c.querySelectorAll('button')].find(b => /^\s*save\s*$/i.test(b.textContent));
      if (!btn) throw new Error('Save button not found');
      const seen = guardDialogs(c.defaultView);
      btn.click();
      await until(() => seen.length || !logCallDoc(), 30000, 'the call log to save');
      if (seen.length) throw new Error('Momentum said: ' + seen.join(' / '));
      return true;
    },
    // Today's counts from the home page's Sales Statistics (for example "Outbound Emails / SMS").
    async dashboardCounts() {
      closeComposerNow();
      await composerGone();
      const f = leadFrame();
      f.contentWindow.location.href = '/acrm/servlet/general?cmd=general';
      const labels = () => [...f.contentWindow.frames[0].document.querySelectorAll('td.input_lbl')];
      const value = l => { const cell = l.parentElement.querySelector('td.dark_grey_bold'); return cell ? parseInt(clean(cell.textContent), 10) : NaN; };
      await until(() => { const l = labels().find(x => /Outbound Emails/.test(x.textContent)); return l && !Number.isNaN(value(l)); }, 30000, 'the dashboard');
      const out = {};
      labels().forEach(l => { const v = value(l); if (!Number.isNaN(v)) out[clean(l.textContent)] = v; });
      return out;
    }
  };
})();
