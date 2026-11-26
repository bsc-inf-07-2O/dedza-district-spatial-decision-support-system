// Range sliders paint their filled portion from --fill (see static/css/controls.css).

export function updateRangeFill(input) {
  const min = Number(input.min) || 0;
  const max = Number(input.max) || 100;
  const percent = max > min ? (Number(input.value) - min) / (max - min) * 100 : 0;
  input.style.setProperty("--fill", `${Math.max(0, Math.min(100, percent))}%`);
}

export function updateRangeFills() {
  document.querySelectorAll('input[type="range"]').forEach(updateRangeFill);
}

export function setupRangeFills() {
  document.addEventListener("input", (event) => {
    if (event.target instanceof HTMLInputElement && event.target.type === "range") updateRangeFill(event.target);
  });
  updateRangeFills();
}
