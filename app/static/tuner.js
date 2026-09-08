(function () {
  var form = document.getElementById("tuner-form");
  if (!form) return;

  var sliders = Array.prototype.slice.call(form.querySelectorAll(".tuner-slider"));

  function formatValue(slider) {
    var decimals = parseInt(slider.dataset.decimals || "0", 10);
    var value = parseFloat(slider.value);
    var text = decimals > 0 ? value.toFixed(decimals) : String(Math.round(value));
    var unit = slider.dataset.unit || "";
    return unit ? text + " " + unit : text;
  }

  function updateTarget(slider) {
    var target = document.getElementById("target-" + slider.dataset.key);
    if (target) target.textContent = formatValue(slider);
  }

  function recomputeStats() {
    var tuned = sliders.filter(function (slider) {
      var value = parseFloat(slider.value);
      var def = parseFloat(slider.dataset.default);
      return Math.abs(value - def) > 1e-9;
    });

    var tunedCountEl = document.getElementById("stat-tuned-count");
    if (tunedCountEl) tunedCountEl.textContent = tuned.length + " / " + sliders.length;

    var avgImpactEl = document.getElementById("stat-avg-impact");
    if (avgImpactEl) {
      var avg = 0;
      if (tuned.length) {
        var sum = tuned.reduce(function (acc, s) {
          return acc + parseFloat(s.dataset.impact);
        }, 0);
        avg = Math.round(sum / tuned.length);
      }
      avgImpactEl.textContent = avg + " / 100";
    }

    var restartEl = document.getElementById("stat-restart");
    if (restartEl) {
      var anyRestart = tuned.some(function (s) { return s.dataset.restart === "1"; });
      restartEl.textContent = anyRestart ? "Yes" : "No";
    }
  }

  sliders.forEach(function (slider) {
    slider.addEventListener("input", function () {
      updateTarget(slider);
      recomputeStats();
    });
  });

  form.addEventListener("reset", function () {
    setTimeout(function () {
      sliders.forEach(updateTarget);
      recomputeStats();
    }, 0);
  });
})();
