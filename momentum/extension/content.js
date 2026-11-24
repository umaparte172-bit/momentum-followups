// Tells the Momentum Bridge that a Momentum tab is open, and which account is signed in, so the bridge can connect.
(function () {
  function ping() {
    const u = document.getElementById('username');
    try { chrome.runtime.sendMessage({ type: 'ping', account: u ? u.textContent.split(':')[0].trim() : '' }); } catch (e) { /* extension reloaded */ }
  }
  ping();
  setInterval(ping, 15000);
})();
