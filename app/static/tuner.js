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
})();
