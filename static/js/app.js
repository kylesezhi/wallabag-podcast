/* Poll /queue/status while a generation run is in progress and reload the
 * page when it finishes so the queue shows fresh statuses. */
(function () {
  "use strict";

  var progress = document.getElementById("generation-progress");
  if (!progress || progress.dataset.generating !== "true") {
    return;
  }

  var doneEl = document.getElementById("progress-done");
  var totalEl = document.getElementById("progress-total");

  async function poll() {
    try {
      var resp = await fetch("/queue/status", { headers: { Accept: "application/json" } });
      if (!resp.ok) {
        return;
      }
      var data = await resp.json();
      if (data.stats) {
        if (doneEl) {
          doneEl.textContent = String((data.stats.done || 0) + (data.stats.generating || 0));
        }
        if (totalEl) {
          totalEl.textContent = String(
            (data.stats.staged || 0) + (data.stats.generating || 0) + (data.stats.done || 0) + (data.stats.failed || 0)
          );
        }
      }
      if (!data.generating) {
        // Generation finished: reload once to show final statuses + stats.
        window.location.reload();
      }
    } catch (err) {
      // Network hiccup — keep polling; the server may be busy synthesizing.
    }
  }

  setInterval(poll, 2000);
})();
