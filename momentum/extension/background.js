// Momentum Bridge: connects the Momentum follow-up tool (running on this computer) to the Momentum CRM tab that
// is already signed in in this browser, so no second login or two-step code is needed.
const CRM = 'https://*.udccrm.com/';
const DEFAULT_BASE = 'http://127.0.0.1:8765';
const AGENT_FUNCTIONS = ['ping', 'openLead', 'readLead', 'openComposer', 'selectTemplate', 'composerState', 'sendComposer', 'closeComposer', 'dashboardCounts',
  'openSms', 'selectSmsTemplate', 'smsState', 'sendSms', 'openLogCall', 'fillLogCall', 'logCallState', 'saveLogCall'];
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

async function callAgent(fn, args) {
  if (!AGENT_FUNCTIONS.includes(fn)) throw new Error('Unknown action');
  const tab = await shellTab();
  if (!tab) throw new Error('No signed-in Momentum tab is open in this browser. Open Momentum and sign in, then try again.');
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: 'MAIN', files: ['agent.js'] });
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id }, world: 'MAIN', args: [fn, args || []],
    func: async (name, params) => {
      try { const v = await window.__momentumAgent[name](...params); return { ok: true, value: v === undefined ? null : v }; }
      catch (e) { return { ok: false, error: String(e && e.message || e) }; }
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
async function runReport(reportId, account) {
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
      target: { tabId: grid.id }, world: 'MAIN', args: [account],
      func: async (who) => {
        const t0 = Date.now();
        while (!(window.Tabulator && Tabulator.findTable && Tabulator.findTable('.tabulator')[0] && document.querySelector('.tabulator-row'))) {
          if (Date.now() - t0 > 120000) return { ok: false, error: 'The report did not finish loading' };
          await new Promise(r => setTimeout(r, 500));
        }
        const all = Tabulator.findTable('.tabulator')[0].getData();
        const rows = all.filter(r => r['Sales Person 1'] === who)
          .map(r => ({ id: String(r.personid), name: r['Customer Name'], last: r['Last Contact Date'], action: r['Last Action'], created: r['Prospect Creation Date'], status: r['Sales Status'] }));
        // The counts let the tool show that its list matches the report row for row.
        return { ok: true, value: { rows, total: all.length } };
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
  if (cmd.name === 'report') return runReport(cmd.reportId, cmd.account);
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
      try { r = await fetch(base + '/bridge/poll?token=' + encodeURIComponent(token) + '&account=' + encodeURIComponent(lastAccount)); }
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
