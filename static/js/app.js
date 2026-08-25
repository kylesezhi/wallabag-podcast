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
  var chunkEl = document.getElementById("progress-chunk");

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
      // Chunk progress of the episode currently synthesizing: update its
      // queue-row badge and the progress-card line.
      if (data.episodes && data.episodes.length) {
        var genEp = null;
        for (var i = 0; i < data.episodes.length; i++) {
          if (data.episodes[i].status === "generating") {
            genEp = data.episodes[i];
            break;
          }
        }
        var label = "";
        if (genEp && genEp.progress_total) {
          label = String(genEp.progress_done || 0) + "/" + genEp.progress_total;
          var badge = document.getElementById("ep-progress-" + genEp.id);
          if (badge) {
            badge.textContent = label;
            badge.hidden = false;
          }
        }
        if (chunkEl) {
          if (label) {
            chunkEl.textContent = label + " chunks synthesized";
            chunkEl.hidden = false;
          } else {
            chunkEl.hidden = true;
          }
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

/* Guard every episode delete with a styled confirmation modal. Replaces
 * the former native window.confirm() that only protected done episodes. */
(function () {
  "use strict";

  var overlay = document.getElementById("delete-modal");
  if (!overlay) {
    return;
  }
  var body = document.getElementById("delete-modal-body");
  var confirmBtn = document.getElementById("delete-modal-confirm");
  var cancelBtn = document.getElementById("delete-modal-cancel");
  var pendingForm = null;
  var triggerBtn = null;

  function open(form, message, button) {
    pendingForm = form;
    triggerBtn = button || null;
    if (body) {
      body.textContent = message;
    }
    overlay.hidden = false;
    if (cancelBtn) {
      cancelBtn.focus();
    }
  }

  function close() {
    overlay.hidden = true;
    pendingForm = null;
    var btn = triggerBtn;
    triggerBtn = null;
    if (btn) {
      try {
        btn.focus();
      } catch (err) {
        /* button may be gone after a re-render */
      }
    }
  }

  function confirmDelete() {
    var form = pendingForm;
    close();
    if (form) {
      form.submit();
    }
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.matches || !form.matches("form[data-confirm-message]")) {
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    var message = form.getAttribute("data-confirm-message") || "";
    var button = event.submitter || form.querySelector("button[type=submit]");
    open(form, message, button);
  });

  if (confirmBtn) {
    confirmBtn.addEventListener("click", confirmDelete);
  }
  if (cancelBtn) {
    cancelBtn.addEventListener("click", close);
  }

  overlay.addEventListener("click", function (event) {
    if (event.target === overlay) {
      close();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !overlay.hidden) {
      event.preventDefault();
      close();
    }
  });
})();

/* Settings autosave: any change inside the settings form posts it via fetch
 * and reports the outcome in the inline status line (#save-status). */
(function () {
  "use strict";

  var form = document.querySelector("form.settings-form");
  var status = document.getElementById("save-status");
  if (!form || !status) {
    return;
  }

  var saveTimer = null;

  function setStatus(text, className) {
    status.textContent = text;
    status.classList.remove("saved", "save-error");
    if (className) {
      status.classList.add(className);
    }
  }

  form.addEventListener("change", function () {
    // Debounce slightly so rapid slider releases collapse into one request.
    clearTimeout(saveTimer);
    setStatus("Saving…", null);
    saveTimer = setTimeout(save, 150);
  });

  async function save() {
    try {
      var resp = await fetch("/settings", {
        method: "POST",
        headers: { Accept: "application/json" },
        body: new FormData(form),
      });
      if (resp.ok) {
        setStatus("Saved ✓", "saved");
        return;
      }
      var data = null;
      try {
        data = await resp.json();
      } catch (err) {
        /* non-JSON body — fall through to generic message */
      }
      setStatus(
        data && data.error ? data.error : "Save failed",
        "save-error"
      );
    } catch (err) {
      setStatus("Save failed: network error", "save-error");
    }
  }
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
