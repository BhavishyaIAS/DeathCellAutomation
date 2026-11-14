/* Death Claims Cell – small progressive enhancements; every screen works without it. */
window.DeathCell = (function () {
  "use strict";

  /** Dashboard: refresh the counters every 30 seconds. */
  function liveSummary(url) {
    var stamp = document.getElementById("live-at");
    function tick() {
      fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function (data) {
          document.querySelectorAll("[data-k]").forEach(function (el) {
            var value = data[el.dataset.k];
            if (value !== undefined) el.textContent = value;
          });
          document.querySelectorAll("[data-c]").forEach(function (el) {
            var value = data.counts[el.dataset.c];
            if (value !== undefined) el.textContent = value;
          });
          if (stamp) stamp.textContent = "● live · " + data.at;
        })
        .catch(function () { if (stamp) stamp.textContent = "○ offline"; });
    }
    setInterval(tick, 30000);
  }

  /** Scrutiny form: live evaluation, past-service rows, mismatch highlighting. */
  function scrutinyForm() {
    var form = document.getElementById("scrutiny-form");
    if (!form) return;
    var preview = document.getElementById("preview");
    var state = document.getElementById("preview-state");
    var timer = null;
    var dirty = false;

    function evaluate() {
      if (state) state.textContent = "checking…";
      fetch(form.dataset.preview, {
        method: "POST", body: new FormData(form), credentials: "same-origin",
      })
        .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
        .then(function (html) {
          preview.innerHTML = html;
          if (state) state.textContent = dirty ? "not saved yet" : "up to date";
        })
        .catch(function () { if (state) state.textContent = "could not refresh"; });
    }

    function highlight() {
      form.querySelectorAll("table.compare:not(.prev) tbody tr").forEach(function (row) {
        var cells = row.querySelectorAll("input, select");
        if (cells.length !== 2) return;
        var name = cells[0].name.replace("off_", "");
        if (["dol", "dod", "reason"].indexOf(name) !== -1) return;
        var a = cells[0].value.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
        var b = cells[1].value.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
        row.classList.toggle("differs", Boolean(a && b && a !== b));
      });
    }

    function changed() {
      dirty = true;
      highlight();
      clearTimeout(timer);
      timer = setTimeout(evaluate, 500);
    }
    form.addEventListener("input", changed);
    form.addEventListener("change", changed);

    // Clicking a selected option again clears it.
    form.addEventListener("click", function (event) {
      var radio = event.target.closest(".seg input[type=radio]");
      if (radio) {
        if (radio.dataset.was === "1") { radio.checked = false; changed(); }
        form.querySelectorAll('input[name="' + radio.name + '"]').forEach(function (r) {
          r.dataset.was = r.checked ? "1" : "";
        });
      }
      var remove = event.target.closest("[data-remove-row]");
      if (remove) { remove.closest("tr").remove(); changed(); }
    });
    form.querySelectorAll(".seg input:checked").forEach(function (r) { r.dataset.was = "1"; });

    var add = document.getElementById("add-prev");
    if (add) {
      add.addEventListener("click", function () {
        var row = document.getElementById("prev-row").content.cloneNode(true);
        document.querySelector("#prev-table tbody").appendChild(row);
        var tick = form.querySelector('input[name="no_prev_service"]');
        if (tick) tick.checked = false;
      });
    }

    form.addEventListener("submit", function () { dirty = false; });
    window.addEventListener("beforeunload", function (event) {
      if (dirty) { event.preventDefault(); event.returnValue = ""; }
    });
    highlight();
  }

  return { liveSummary: liveSummary, scrutinyForm: scrutinyForm };
})();
