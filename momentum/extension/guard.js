// Momentum Bridge: runs first in every Momentum frame. While the tool is working (the minute after its last action), a
// message box from Momentum (alert, confirm, prompt) must never freeze the tab: it is recorded, any question is
// answered No, and the tool skips that lead with Momentum's own words. At any other time Momentum behaves as usual.
(() => {
  let top;
  try { top = window.top; void top.location.href; } catch (e) { return; }  // not a Momentum frame
  const working = () => { try { return Date.now() < (top.__momentumAgentUntil || 0); } catch (e) { return false; } };
  const note = (kind, m) => { try { (top.__momentumSeen = top.__momentumSeen || []).push(String(m == null ? '' : m)); } catch (e) { /* gone */ } };
  window.__momentumGuard = 1;
  const original = { alert: window.alert, confirm: window.confirm, prompt: window.prompt };
  window.alert = function (m) { if (working()) { note('alert', m); return undefined; } return original.alert.apply(this, arguments); };
  window.confirm = function (m) { if (working()) { note('confirm', m); return false; } return original.confirm.apply(this, arguments); };
  window.prompt = function (m) { if (working()) { note('prompt', m); return null; } return original.prompt.apply(this, arguments); };
})();
