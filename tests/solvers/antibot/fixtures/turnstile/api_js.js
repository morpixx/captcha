/* Shape fixture, not a byte dump: the 2026-08 Turnstile flow (docs/reverse-2026.md §3)
   shows the unversioned loader 302-ing to, or embedding, a versioned bundle at
   /turnstile/v0/b/<hash>/api.js. The hash below is a placeholder for the fixture. */
(function () {
  var script = document.createElement('script');
  script.src = '/turnstile/v0/b/8f1a2b3c/api.js';
  document.head.appendChild(script);
})();
