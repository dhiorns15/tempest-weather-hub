// Registers the service worker (/sw.js) that gives the installed app an
// offline page. Browsers only allow service workers in a secure context
// (HTTPS, or http://localhost), so on a plain-HTTP LAN address this does
// nothing at all and the site works exactly as it does without it.
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // not supported/allowed here - the site doesn't depend on it
    });
  });
}
