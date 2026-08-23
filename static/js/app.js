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

/* Guard done-episode deletes (irreversible mp3 loss) with a confirm(). */
(function () {
  "use strict";

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form && form.matches && form.matches('form[data-confirm="true"]')) {
      if (!window.confirm("Delete this episode and its audio file?")) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }
  });
})();

/* Copy the subscribe URL to the clipboard; falls back to execCommand for
 * plain-HTTP LAN origins where navigator.clipboard is unavailable. */
(function () {
  "use strict";

  function legacyCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      return document.execCommand("copy");
    } catch (err) {
      return false;
    } finally {
      document.body.removeChild(area);
    }
  }

  function flashCopied(button) {
    button.classList.add("copied");
    setTimeout(function () {
      button.classList.remove("copied");
    }, 1500);
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest(".btn-copy") : null;
    if (!button) {
      return;
    }
    var url = button.dataset.copyUrl;
    if (!url) {
      return;
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(url).then(
        function () { flashCopied(button); },
        function () { /* clipboard rejected — no feedback */ }
      );
    } else if (legacyCopy(url)) {
      flashCopied(button);
    }
  });
})();
