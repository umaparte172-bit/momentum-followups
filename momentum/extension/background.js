// Momentum Bridge: connects the Momentum follow-up tool (running on this computer) to the Momentum CRM tab that
// is already signed in in this browser, so no second login or two-step code is needed.
const CRM = 'https://*.udccrm.com/';
const DEFAULT_BASE = 'http://127.0.0.1:8765';
const AGENT_FUNCTIONS = ['ping', 'openLead', 'readLead', 'openComposer', 'selectTemplate', 'fillComposer', 'composerState', 'sendComposer', 'closeComposer', 'describeScreen', 'dashboardCounts',
  'openSms', 'selectSmsTemplate', 'fillSms', 'smsState', 'sendSms', 'openLogCall', 'fillLogCall', 'logCallState', 'saveLogCall'];
let polling = false;
let lastAccount = '';
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function config() {
  const s = await chrome.storage.local.get(['base', 'token']);
  return { base: s.base || DEFAULT_BASE, token: s.token || null };
}

async function hello(base) {
  try {
    const r = await fetch(base + '/bridge/hello', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    if (!r.ok) return null;
    const j = await r.json();
    await chrome.storage.local.set({ token: j.token });
    return j.token;
  } catch (e) { return null; }
}

async function crmTabs() { return chrome.tabs.query({ url: CRM + '*' }); }

async function shellTab() {
  for (const t of await crmTabs()) {
    try {
      const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: t.id }, world: 'MAIN', func: () => !!document.getElementById('main_iframe') && !!document.getElementById('username') });
      if (result) return t;
    } catch (e) { /* tab not scriptable */ }
  }
  return null;
}

// A Momentum tab opened before this version of the extension was installed has no guard.js yet: reload it once.
const guardReloads = {};
async function ensureGuard(tab) {
  const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: 'MAIN', func: () => !!window.__momentumGuard });
  if (result || Date.now() - (guardReloads[tab.id] || 0) < 600000) return tab;
  guardReloads[tab.id] = Date.now();
  await chrome.tabs.reload(tab.id);
  await sleep(500);
  await waitComplete(tab.id).catch(() => {});
  for (let i = 0; i < 40; i++) { const t = await shellTab(); if (t) return t; await sleep(500); }
  return tab;
}

async function callAgent(fn, args) {
  if (!AGENT_FUNCTIONS.includes(fn)) throw new Error('Unknown action');
  let tab = await shellTab();
  if (!tab) throw new Error('No signed-in Momentum tab is open in this browser. Open Momentum and sign in, then try again.');
  // Chrome's Memory Saver may put a background tab to sleep in the middle of a long batch: never this one.
  if (tab.autoDiscardable !== false) { try { await chrome.tabs.update(tab.id, { autoDiscardable: false }); } catch (e) { /* older Chrome */ } }
  if (fn === 'ping' || fn === 'openLead' || fn === 'readLead') tab = await ensureGuard(tab);
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: 'MAIN', files: ['agent.js'] });
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id }, world: 'MAIN', args: [fn, args || []],
    func: async (name, params) => {
      // While the tool works, Momentum's message boxes are recorded instead of shown (guard.js); a message while a
      // screen opens or is filled in means Momentum does not allow this action for this lead.
      window.__momentumAgentUntil = Date.now() + 60000;
      const seen = window.__momentumSeen = window.__momentumSeen || [], before = seen.length;
      try { window.__momentumAgent.arm(); } catch (e) { /* older agent */ }
      const opening = ['openLead', 'readLead', 'openComposer', 'selectTemplate', 'fillComposer', 'openSms', 'selectSmsTemplate', 'fillSms', 'openLogCall', 'fillLogCall'];
      try {
        const v = await window.__momentumAgent[name](...params);
        const said = seen.slice(before).filter(Boolean);
        if (said.length && opening.includes(name)) return { ok: false, error: 'Momentum does not allow this on this lead: ' + said.join(' / ') };
        return { ok: true, value: v === undefined ? null : v };
      } catch (e) {
        const said = seen.slice(before).filter(Boolean);
        if (said.length && opening.includes(name)) return { ok: false, error: 'Momentum does not allow this on this lead: ' + said.join(' / ') };
        return { ok: false, error: String(e && e.message || e) };
      } finally { window.__momentumAgentUntil = Date.now() + 60000; }
    }
  });
  if (!result || !result.ok) throw new Error(result ? result.error : 'No answer from the Momentum page');
  return result.value;
}

function waitComplete(tabId, ms = 40000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { chrome.tabs.onUpdated.removeListener(listener); reject(new Error('The Momentum page did not finish loading')); }, ms);
    const listener = (id, info) => { if (id === tabId && info.status === 'complete') { clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); resolve(); } };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then(t => { if (t.status === 'complete') { clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); resolve(); } }).catch(() => {});
  });
}

// Runs the saved report exactly like the Run button does, reads the result grid and closes the extra tabs.
async function runReport(reportId, account, sourceColumn) {
  const shell = await shellTab();
  if (!shell) throw new Error('No signed-in Momentum tab is open in this browser. Open Momentum and sign in, then try again.');
  const origin = new URL(shell.url).origin;
  const prompts = origin + '/acrm/servlet/general?cmd=home_report_list&action=displayprompts&reportid=' + encodeURIComponent(reportId);
  const results = origin + '/acrm/servlet/report?cmd=customreportresults&action=display&reportid=prompt_' + encodeURIComponent(reportId);
  const opened = [];
  try {
    const page = await chrome.tabs.create({ url: prompts, active: false });
    opened.push(page.id);
    await waitComplete(page.id);
    const popupPromise = new Promise(resolve => {
      const timer = setTimeout(() => { chrome.tabs.onCreated.removeListener(listener); resolve(null); }, 6000);
      const listener = t => { if (t.openerTabId === page.id) { clearTimeout(timer); chrome.tabs.onCreated.removeListener(listener); resolve(t); } };
      chrome.tabs.onCreated.addListener(listener);
    });
    const [{ result: clicked }] = await chrome.scripting.executeScript({ target: { tabId: page.id }, world: 'MAIN', func: () => { const b = [...document.querySelectorAll('input[type=button]')].find(x => x.value === 'Run'); if (!b) return false; b.click(); return true; } });
    if (!clicked) throw new Error('The report Run button was not found');
    let grid = await popupPromise;
    if (!grid) grid = await chrome.tabs.create({ url: results, active: false });
    opened.push(grid.id);
    await waitComplete(grid.id, 60000);
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: grid.id }, world: 'MAIN', args: [account, sourceColumn || ''],
      func: async (who, wanted) => {
        const t0 = Date.now();
        while (!(window.Tabulator && Tabulator.findTable && Tabulator.findTable('.tabulator')[0] && document.querySelector('.tabulator-row'))) {
          if (Date.now() - t0 > 120000) return { ok: false, error: 'The report did not finish loading' };
          await new Promise(r => setTimeout(r, 500));
        }
        // The grid can still be filling: read it only once its size has stopped changing.
        let all = [], still = 0;
        while (still < 2) {
          if (Date.now() - t0 > 240000) return { ok: false, error: 'The report did not finish loading' };
          const now = Tabulator.findTable('.tabulator')[0].getData();
          still = now.length === all.length && now.length ? still + 1 : 0;
          all = now;
          await new Promise(r => setTimeout(r, 700));
        }
        // Column names only. Every column that could hold the lead source is sent; the tool decides which one is used.
        const columns = new Set();
        for (const r of all) for (const k in r) columns.add(k);
        // Which contact columns the report has, and only yes or no per person: the numbers and addresses stay in Momentum.
        const named = re => [...columns].filter(k => re.test(k.trim()));
        const contactColumns = { cell: named(/^(cell|call|mobile)( phone)?$/i), phone: named(/^((home|work|cell|call|mobile) phone|cell|mobile|phone)$/i), email: named(/^e-?mail( address)?$/i) };
        const has = (r, keys) => keys.some(k => /\S/.test(r[k] == null ? '' : String(r[k])));
        const sourceColumns = [...columns].filter(k => k === wanted || /source|\bsrc\b|vendor|provider/i.test(k)).slice(0, 8);
        // Momentum's own total, if the page shows one ("65,015 unique people"), read from outside the table rows.
        let momentumTotal = null;
        try {
          const words = [];
          const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
          for (let n; (n = walk.nextNode());) if (n.textContent.trim() && !n.parentElement.closest('.tabulator-row,script,style')) words.push(n.textContent.trim());
          const text = words.join(' '), found = new Set();
          for (const m of text.matchAll(/([\d,]*\d)\s+(?:total\s+)?unique\b/gi)) found.add(Number(m[1].replace(/,/g, '')));
          if (!found.size) for (const m of text.matchAll(/unique\s+(?:people|persons?|customers?|leads?|prospects?|records?)\s*[:=(]?\s*([\d,]*\d)/gi)) found.add(Number(m[1].replace(/,/g, '')));
          if (found.size === 1) momentumTotal = [...found][0];
        } catch (e) { /* no total to compare with */ }
        const rows = all.filter(r => r['Sales Person 1'] === who)
          .map(r => ({ id: String(r.personid), name: r['Customer Name'], last: r['Last Contact Date'], action: r['Last Action'], created: r['Prospect Creation Date'], status: r['Sales Status'] }));
        // The counts let the tool show that its list matches the report row for row.
        // Everyone in the report, for viewing and searching only. Arrays, not objects: there are about 65,000.
        const t = v => v == null ? '' : String(v);
        const people = all.map(r => [t(r.personid), t(r['Customer Name']), t(r['Sales Person 1']), t(r['Last Contact Date']), t(r['Last Action']), t(r['Prospect Creation Date']), t(r['Sales Status']), sourceColumns.map(k => t(r[k])), (has(r, contactColumns.cell) ? 'c' : '') + (has(r, contactColumns.phone) ? 'p' : '') + (has(r, contactColumns.email) ? 'e' : '')]);
        return { ok: true, value: { rows, total: all.length, people, columns: [...columns], sourceColumns, contacts: { text: contactColumns.cell.length > 0, call: contactColumns.phone.length > 0, email: contactColumns.email.length > 0 }, momentumTotal } };
      }
    });
    if (!result || !result.ok) throw new Error(result ? result.error : 'No report data');
    return result.value;
  } finally {
    for (const id of opened) { try { await chrome.tabs.remove(id); } catch (e) { /* already closed */ } }
  }
}

// Chrome throttles hidden tabs (including the CRM page's own scripts), so while the tool works the Momentum tab is brought
// forward, and the tab you were on is restored afterwards.
let previous = null;
async function setFocus(on) {
  if (on) {
    const tab = await shellTab();
    if (!tab) return false;
    const [active] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (active && active.id !== tab.id) previous = { tabId: active.id, windowId: active.windowId };
    await chrome.windows.update(tab.windowId, { focused: true });
    await chrome.tabs.update(tab.id, { active: true });
    return true;
  }
  if (previous) {
    const back = previous;
    previous = null;
    try { await chrome.windows.update(back.windowId, { focused: true }); await chrome.tabs.update(back.tabId, { active: true }); } catch (e) { /* tab closed */ }
  }
  return true;
}

async function handle(cmd) {
  if (cmd.name === 'focus') return setFocus(!!cmd.on);
  if (cmd.name === 'reload') { setTimeout(() => chrome.runtime.reload(), 400); return true; }
  if (cmd.name === 'ping') return { shell: !!(await shellTab()) };
  if (cmd.name === 'agent') return callAgent(cmd.fn, cmd.args);
  if (cmd.name === 'report') return runReport(cmd.reportId, cmd.account, cmd.sourceColumn);
  throw new Error('Unknown command');
}

async function loop() {
  if (polling) return;
  polling = true;
  try {
    while ((await crmTabs()).length) {
      let { base, token } = await config();
      if (!token) { token = await hello(base); if (!token) { await sleep(3000); continue; } }
      let r;
      try { r = await fetch(base + '/bridge/poll?token=' + encodeURIComponent(token) + '&account=' + encodeURIComponent(lastAccount) + '&version=' + encodeURIComponent(chrome.runtime.getManifest().version)); }
      catch (e) { await sleep(3000); continue; }
      if (r.status === 401 || r.status === 403) { await chrome.storage.local.remove('token'); await sleep(1500); continue; }
      const j = await r.json().catch(() => ({}));
      if (!j.cmd) continue;
      let body;
      try { body = { token, id: j.cmd.id, ok: true, value: await handle(j.cmd) }; }
      catch (e) { body = { token, id: j.cmd.id, ok: false, error: String(e && e.message || e) }; }
      try { await fetch(base + '/bridge/result', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); } catch (e) { /* server stopped */ }
    }
  } finally { polling = false; }
}

chrome.runtime.onMessage.addListener(msg => { if (msg && msg.type === 'ping') { lastAccount = msg.account || lastAccount; loop(); } });
chrome.alarms.create('tick', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => loop());
chrome.runtime.onStartup.addListener(() => loop());
chrome.runtime.onInstalled.addListener(() => loop());
