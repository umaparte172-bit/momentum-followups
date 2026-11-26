// Momentum Bridge: page agent. Runs inside the signed-in Momentum tab (page context) and only exposes a fixed set of
// read and compose actions. Nothing here accepts arbitrary code from the local server.
(() => {
  const VERSION = 18;
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
  // Counts the requests a Momentum page makes (XMLHttpRequest and fetch), so the tool knows when a save has finished.
  function watchRequests(wins) {
    const net = { started: 0, pending: 0, last: Date.now(), undo: [] };
    const done = () => { net.pending = Math.max(0, net.pending - 1); net.last = Date.now(); };
    for (const w of new Set(wins.filter(Boolean))) {
      try {
        const send = w.XMLHttpRequest.prototype.send;
        w.XMLHttpRequest.prototype.send = function () { net.started++; net.pending++; this.addEventListener('loadend', done, { once: true }); return send.apply(this, arguments); };
        net.undo.push(() => { w.XMLHttpRequest.prototype.send = send; });
        if (w.fetch) {
          const f = w.fetch;
          w.fetch = function () { net.started++; net.pending++; return f.apply(this, arguments).finally(done); };
          net.undo.push(() => { w.fetch = f; });
        }
      } catch (e) { /* not reachable */ }
    }
    net.stop = () => net.undo.forEach(u => { try { u(); } catch (e) { /* gone */ } });
    return net;
  }
  // A second line of defence next to guard.js: every Momentum frame that can be reached gets the same message-box
  // handling, so a warning raised after it loads (when a number is picked, on Save) can never freeze the tab.
  function armFrames(win) {
    try {
      if (!win.__momentumGuard && !win.__momentumArmed) {
        const original = { alert: win.alert, confirm: win.confirm, prompt: win.prompt };
        const working = () => Date.now() < (window.__momentumAgentUntil || 0);
        const note = m => (window.__momentumSeen = window.__momentumSeen || []).push(String(m == null ? '' : m));
        win.alert = function (m) { if (working()) return void note(m); return original.alert.apply(this, arguments); };
        win.confirm = function (m) { if (working()) { note(m); return false; } return original.confirm.apply(this, arguments); };
        win.prompt = function (m) { if (working()) { note(m); return null; } return original.prompt.apply(this, arguments); };
        win.__momentumArmed = true;
      }
      for (let i = 0; i < win.frames.length; i++) armFrames(win.frames[i]);
    } catch (e) { /* another site's frame */ }
  }
  async function until(fn, ms, label) {
    const t0 = Date.now();
    for (;;) {
      armFrames(window);
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
  // Every message box goes into one list, from whichever Momentum frame it comes (guard.js records the others).
  function guardDialogs(win) {
    const all = window.__momentumSeen = window.__momentumSeen || [], base = all.length;
    win.alert = m => { all.push(String(m)); };
    win.confirm = m => { all.push(String(m)); return false; };
    return { get length() { return all.length - base; }, join: sep => all.slice(base).join(sep) };
  }

  // While an action screen opens, a message from Momentum (for example "do not call") must not freeze the page:
  // it is recorded, any question is answered No, and the lead is skipped with Momentum's own words.
  function guardOpening() {
    const wins = [window];
    try { wins.push(leadFrame().contentWindow); } catch (e) { /* not loaded */ }
    try { wins.push(activitiesDoc().defaultView); } catch (e) { /* not loaded */ }
    const seen = [], saved = wins.map(w => [w, w.alert, w.confirm]);
    wins.forEach(w => { w.alert = m => { seen.push(String(m)); }; w.confirm = m => { seen.push(String(m)); return false; }; });
    return { seen, restore: () => saved.forEach(([w, a, c]) => { try { w.alert = a; w.confirm = c; } catch (e) { /* gone */ } }) };
  }
  // The lead page's own Email / Text / Log Call button. Missing or disabled means Momentum does not allow it here.
  async function press(id, label, ready) {
    let button = null;
    try { button = await until(() => { const d = activitiesDoc(); return d.readyState === 'complete' && d.getElementById(id); }, 15000, label); } catch (e) { /* not offered */ }
    if (!button || button.disabled || button.getAttribute('aria-disabled') === 'true' || button.classList.contains('disabled')) throw new Error('Momentum does not allow ' + label + ' on this lead for your account');
    // The window left over from the previous lead must never be taken for the new one: mark it as old first.
    for (const d of [(() => { const f = composerFrame(); return f && f.contentDocument; })(), (() => { const f = activitiesDoc().getElementById('activities_iframe'); return f && f.contentDocument; })()]) {
      try { if (d) d.__momentumStale = true; } catch (e) { /* not reachable */ }
    }
    const guard = guardOpening();
    try {
      button.click();
      // If Momentum reuses the same window rather than loading a new one, it is accepted after a while; the recipient is checked anyway.
      const t0 = Date.now();
      const found = await until(() => { if (guard.seen.length) return 'said'; const v = ready(); return v && (!v.__momentumStale || Date.now() - t0 > 8000) ? v : null; }, 25000, label);
      if (found === 'said') throw new Error('Momentum does not allow ' + label + ' on this lead: ' + guard.seen.join(' / '));
      return found;
    } finally { guard.restore(); }
  }
  // Puts a custom message into the email window's editor, the way typing would.
  function setEditor(c, html) {
    const w = c.defaultView, el = c.querySelector('.fr-element');
    if (!el) throw new Error('The email editor was not found');
    let done = false;
    try { const list = w.FroalaEditor && w.FroalaEditor.INSTANCES; const ed = list && list.find(e => e.el === el); if (ed) { ed.html.set(html); ed.undo && ed.undo.saveStep && ed.undo.saveStep(); done = true; } } catch (e) { /* other version */ }
    if (!done) { try { const box = el.closest('.fr-box'); const src = box && box.previousElementSibling; if (w.jQuery && src && w.jQuery(src).data('froala.editor')) { w.jQuery(src).froalaEditor('html.set', html); done = true; } } catch (e) { /* other version */ } }
    if (!done) { el.innerHTML = html; }
    ['input', 'keyup', 'change'].forEach(t => el.dispatchEvent(new Event(t, { bubbles: true })));
    const box = el.closest('.fr-box'), src = box && box.previousElementSibling;
    if (src && src.tagName === 'TEXTAREA') src.value = html;
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
  // Momentum sometimes ignores the first click on Close: click it again every few seconds until the window is gone.
  const composerGone = async () => {
    const t0 = Date.now();
    let last = t0;
    return until(() => {
      const f = composerFrame();
      if (!f || !f.offsetParent) return true;
      if (Date.now() - last > 3000) { last = Date.now(); closeComposerNow(); }
      return false;
    }, 20000, 'the email window to close');
  };

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

  // Momentum's own first / last name fields on the lead page, when it has them.
  function nameField(d, re) {
    const el = [...d.querySelectorAll('input')].find(i => i.type !== 'hidden' && (re.test(i.id || '') || re.test(i.name || '')));
    return el ? clean(el.value) : '';
  }

  window.__momentumAgent = {
    version: VERSION,
    arm() { armFrames(window); return !!window.__momentumGuard; },
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
        firstName: nameField(d, /first\s*_?name$/i),
        lastName: nameField(d, /last\s*_?name$/i),
        name: clean((d.body.innerText || '').split('\n')[0]),
        history: rows
      };
    },
    async openComposer() {
      const c = await press('email', 'Send Email', () => { const doc = composerDoc(); const sel = doc && doc.querySelector('select[name=templateId]'); return sel && sel.options.length > 1 && doc; });
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
    // A custom message: subject and body are written in, no saved template is used.
    async fillComposer(spec) {
      const c = composerDoc();
      if (!c) throw new Error('The email window is not open');
      const subject = c.querySelector('input[name=subject]');
      if (!subject) throw new Error('The subject field was not found');
      subject.value = String(spec.subject || '');
      ['input', 'change'].forEach(t => subject.dispatchEvent(new Event(t, { bubbles: true })));
      setEditor(c, String(spec.html || ''));
      await until(() => { const s = composerState(); return s.subject.trim() && s.body.trim(); }, 10000, 'the message to fill in');
      return composerState();
    },
    async sendComposer() {
      const c = composerDoc();
      if (!c) throw new Error('The email window is not open');
      const btn = [...c.querySelectorAll('button[type=submit]')].find(b => /send/i.test(b.textContent));
      if (!btn) throw new Error('Send button not found');
      const seen = guardDialogs(composerFrame().contentWindow);
      btn.click();
      await until(() => seen.length || !composerFrame() || !composerFrame().offsetParent, 45000, 'the email window to close');
      if (seen.length) throw new Error('Momentum said: ' + seen.join(' / '));
      return true;
    },
    async closeComposer() { closeComposerNow(); await composerGone(); return true; },
    // Read only: what dialogs are showing, so a window that will not close can be explained.
    async describeScreen() {
      const vis = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
      const dialogs = [...document.querySelectorAll('.ui-dialog, [role=dialog], .modal')].filter(vis).map(d => ({
        title: clean((d.querySelector('.ui-dialog-title') || {}).textContent),
        text: clean(d.innerText).slice(0, 300),
        buttons: [...d.querySelectorAll('button, input[type=button]')].filter(vis).map(b => clean(b.textContent || b.value)).slice(0, 12),
        frame: (d.querySelector('iframe') || {}).id || ''
      }));
      const f = composerFrame();
      return { dialogs, composerVisible: !!(f && f.offsetParent), frames: [...document.querySelectorAll('iframe')].filter(vis).map(x => x.id || x.name).slice(0, 12) };
    },
    // --- text messages (Send Text Message dialog) ---
    async openSms() {
      closeComposerNow();
      await composerGone();
      const c = await press('sms', 'Send Text', () => { const doc = composerDoc(); const sel = doc && doc.querySelector('#smsTemplate'); return sel && sel.options.length > 1 && doc; });
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
    async fillSms(body) {
      const c = composerDoc();
      const box = c && c.querySelector('textarea[name=emailBody]');
      if (!box) throw new Error('The text message window is not open');
      box.value = String(body || '');
      ['input', 'keyup', 'change'].forEach(t => box.dispatchEvent(new Event(t, { bubbles: true })));
      return smsState();
    },
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
      const c = await press('log_call', 'Log Call', () => logCallDoc());
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
      c.__momentumStale = true;
      // Momentum keeps (or reloads) the Log Call panel after saving, so waiting for it to close only wastes time.
      // Done as soon as Momentum answers, the panel reloads, or Momentum's own save request has finished.
      const net = watchRequests([c.defaultView, activitiesDoc().defaultView, leadFrame().contentWindow, window]);
      const t0 = Date.now();
      btn.click();
      try {
        await until(() => seen.length || logCallDoc() !== c || (net.started && !net.pending && Date.now() - net.last > 300)
          || (!net.started && Date.now() - t0 > 3000) || Date.now() - t0 > 15000, 30000, 'the call log to save');
      } finally { net.stop(); }
      if (seen.length) throw new Error('Momentum said: ' + seen.join(' / '));
      // Momentum answers a save by reloading the panel with a line of text, not a message box: "Call Logged." when it
      // was saved, or a reason (for example "Please fill out the form completely.") when it was not. Only a reason is a
      // refusal; anything unclear is left to Contact History, so a saved call is never taken for a refused one.
      let again = null;
      try { again = logCallDoc(); } catch (e) { /* panel gone */ }
      if (again && again !== c) {
        const said = [...again.querySelectorAll('p')].map(p => clean(p.textContent)).filter(Boolean).join(' / ');
        if (said && !/logged|saved|success/i.test(said) && /fill out|required|complete|invalid|not allowed|cannot|can't|unable|error/i.test(said)) {
          throw new Error('Momentum said: ' + said);
        }
      }
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
