(function () {
  var container = document.getElementById("build-progress");
  if (!container) return;

  var POLL_MS = 1500;

  function stateOf(root) {
    var live = root.querySelector(".build-live");
    return live ? live.dataset.state : null;
  }

  // A build's log is the thing you actually want to watch, so keep it
  // pinned to the newest line — unless the operator has scrolled up to
  // read something, in which case leave their position alone.
  function logIsPinned() {
    var log = document.getElementById("build-log");
    if (!log) return true;
    return log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  }

  function scrollLog() {
    var log = document.getElementById("build-log");
    if (log) log.scrollTop = log.scrollHeight;
  }

  function poll() {
    var pinned = logIsPinned();
    fetch(container.dataset.progressUrl, { headers: { "X-Requested-With": "fetch" } })
      .then(function (response) { return response.ok ? response.text() : null; })
      .then(function (html) {
        if (html === null) return;
        container.innerHTML = html;
        if (pinned) scrollLog();
        if (stateOf(container) === "running") {
          setTimeout(poll, POLL_MS);
        }
      })
      .catch(function () {
        // A dropped poll shouldn't end the watch — the build is still
        // running on the daemon and writing to disk regardless.
        setTimeout(poll, POLL_MS * 2);
      });
  }

  scrollLog();
  if (stateOf(container) === "running") setTimeout(poll, POLL_MS);
})();
