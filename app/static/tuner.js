(function () {
  var form = document.getElementById("tuner-form");
  if (!form) return;

  var controls = Array.prototype.slice.call(form.querySelectorAll(".tuner-control"));

  // Group controls by data-key: a range slider is a group of one; an
  // enum (radio) parameter is a group of choices, only one checked.
  var groups = {};
  controls.forEach(function (el) {
    var key = el.dataset.key;
    (groups[key] = groups[key] || []).push(el);
  });

  function isRadioGroup(els) {
    return els[0].type === "radio";
  }

  function currentValue(els) {
    if (isRadioGroup(els)) {
      var checked = els.filter(function (el) { return el.checked; })[0];
      return checked ? checked.value : els[0].dataset.default;
    }
    return els[0].value;
  }

  function formatValue(el, rawValue) {
    var decimals = parseInt(el.dataset.decimals || "0", 10);
    var unit = el.dataset.unit || "";
    if (el.type === "radio") {
      return rawValue.charAt(0).toUpperCase() + rawValue.slice(1);
    }
    var value = parseFloat(rawValue);
    var text = decimals > 0 ? value.toFixed(decimals) : String(Math.round(value));
    return unit ? text + " " + unit : text;
  }

  function updateTarget(key) {
    var els = groups[key];
    var target = document.getElementById("target-" + key);
    if (target) target.textContent = formatValue(els[0], currentValue(els));
  }

  function isTuned(key) {
    var els = groups[key];
    var value = currentValue(els);
    var def = els[0].dataset.default;
    if (isRadioGroup(els)) return String(value) !== String(def);
    return Math.abs(parseFloat(value) - parseFloat(def)) > 1e-9;
  }

  function recomputeStats() {
    var keys = Object.keys(groups);
    var tunedKeys = keys.filter(isTuned);

    var tunedCountEl = document.getElementById("stat-tuned-count");
    if (tunedCountEl) tunedCountEl.textContent = tunedKeys.length + " / " + keys.length;

    var avgImpactEl = document.getElementById("stat-avg-impact");
    if (avgImpactEl) {
      var avg = 0;
      if (tunedKeys.length) {
        var sum = tunedKeys.reduce(function (acc, key) {
          return acc + parseFloat(groups[key][0].dataset.impact);
        }, 0);
        avg = Math.round(sum / tunedKeys.length);
      }
      avgImpactEl.textContent = avg + " / 100";
    }

    var restartEl = document.getElementById("stat-restart");
    if (restartEl) {
      var anyRestart = tunedKeys.some(function (key) {
        return groups[key][0].dataset.restart === "1";
      });
      restartEl.textContent = anyRestart ? "Yes" : "No";
    }
  }

  controls.forEach(function (el) {
    var eventName = el.type === "radio" ? "change" : "input";
    el.addEventListener(eventName, function () {
      updateTarget(el.dataset.key);
      recomputeStats();
    });
  });

  form.addEventListener("reset", function () {
    setTimeout(function () {
      Object.keys(groups).forEach(updateTarget);
      recomputeStats();
    }, 0);
  });

  // Extension checkboxes: just keep the "Extensions enabled" tile in sync.
  var extControls = Array.prototype.slice.call(form.querySelectorAll(".ext-control"));

  function recomputeExtStats() {
    var extCountEl = document.getElementById("stat-ext-count");
    if (!extCountEl) return;
    var checked = extControls.filter(function (el) { return el.checked; }).length;
    extCountEl.textContent = checked + " / " + extControls.length;
  }

  extControls.forEach(function (el) {
    el.addEventListener("change", recomputeExtStats);
  });

  form.addEventListener("reset", function () {
    setTimeout(recomputeExtStats, 0);
  });

  // Companion service checkboxes: same pattern, own stat tile.
  var svcControls = Array.prototype.slice.call(form.querySelectorAll(".svc-control"));

  function recomputeSvcStats() {
    var svcCountEl = document.getElementById("stat-svc-count");
    if (!svcCountEl) return;
    var checked = svcControls.filter(function (el) { return el.checked; }).length;
    svcCountEl.textContent = checked + " / " + svcControls.length;
  }

  svcControls.forEach(function (el) {
    el.addEventListener("change", recomputeSvcStats);
  });

  form.addEventListener("reset", function () {
    setTimeout(recomputeSvcStats, 0);
  });

  // The live panel: cross-parameter findings, and the generated conf.
  // Both are arithmetic over the whole parameter set and both live in
  // app/core — so rather than reimplement either here and have two copies
  // drift apart, post the form back and render what the server returns.
  var panel = document.getElementById("preview-panel");
  if (!panel) return;

  var pending = null;
  var inFlight = false;

  function refreshFindings() {
    if (inFlight) return;
    inFlight = true;
    fetch(panel.dataset.previewUrl, { method: "POST", body: new FormData(form) })
      .then(function (response) { return response.ok ? response.text() : null; })
      .then(function (html) {
        if (html !== null) panel.innerHTML = html;
      })
      .catch(function () {
        // A failed check shouldn't block tuning; the /build POST
        // re-validates server-side regardless of what's on screen.
      })
      .finally(function () { inFlight = false; });
  }

  function scheduleRefresh() {
    clearTimeout(pending);
    pending = setTimeout(refreshFindings, 250);
  }

  form.addEventListener("input", scheduleRefresh);
  form.addEventListener("change", scheduleRefresh);
  form.addEventListener("reset", function () {
    setTimeout(scheduleRefresh, 0);
  });
})();
