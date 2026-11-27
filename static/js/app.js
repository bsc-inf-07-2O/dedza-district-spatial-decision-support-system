// Start-up: build the map and shell, load the data, then wire each tool.
// The order of the setup calls matters where two of them listen to the same
// element (for example the map container's capture-phase click handlers).
import { baseTilesFailed, mapStatus } from "./map/map.js";

import { data, loadData } from "./data.js";
import { initState, state } from "./state.js";
import { enhanceSelect } from "./ui/custom-select.js";
import { escapeHtml } from "./ui/format.js";
import { setupNotice } from "./ui/notice.js";
import { enhanceNumberInput } from "./ui/number-input.js";
import { setupRangeFills } from "./ui/range.js";
import { setupShell } from "./ui/shell.js";
import { setupBriefingActions, setupInsightTabs, setupPopupActions } from "./features/actions.js";
import { activeData, selectDistrict, setupAreaControls, showDistrictBoundary } from "./features/area.js";
import { setupExport } from "./features/export.js";
import { setupGapAnalysis, showInitialGapAnalysis } from "./features/gap.js";
import { setupLayerToggles, showLayerAvailability } from "./features/layers.js";
import { setupPriorityWeights } from "./features/priority.js";
import { render } from "./features/render.js";
import { fillRouteOptions, setupRoutes } from "./features/routes.js";
import { setupSiteControls } from "./features/sites.js";
import { setupSuggestions } from "./features/suggestions.js";
import { buildTAComparison, populateTASelect, renderTAComparison } from "./features/ta.js";

// Schools tool: the walking-distance threshold for "within reach of a school".
function setupSchoolRadius() {
  const schoolRadiusInput = document.getElementById("school-radius");
  schoolRadiusInput.addEventListener("change", () => {
    const value = Number(schoolRadiusInput.value);
    if (!Number.isFinite(value) || value < 1 || value > 50) {
      schoolRadiusInput.value = String(state.schoolRadiusKm);
      return;
    }
    state.schoolRadiusKm = value;
    render(activeData());
  });
}

async function init() {
  await loadData();
  showDistrictBoundary();
  initState(data);

  // Clicks and scrolls on the floating result cards must not reach the map.
  L.DomEvent.disableClickPropagation(document.getElementById("route-results-panel"));
  showInitialGapAnalysis(data.gapAnalysis);
  const scenarioResults = document.getElementById("scenario-results");
  L.DomEvent.disableClickPropagation(scenarioResults);
  L.DomEvent.disableScrollPropagation(scenarioResults);
  showLayerAvailability();
  setupNotice();

  // Form controls
  fillRouteOptions();
  ["ta-select", "route-mode", "route-settlement", "route-facility", "scenario-type", "scenario-travel-mode"]
    .forEach((id) => enhanceSelect(document.getElementById(id)));
  ["school-radius", "scenario-radius"]
    .forEach((id) => enhanceNumberInput(document.getElementById(id)));

  // Tools
  setupAreaControls();
  setupRoutes();
  setupSiteControls();
  setupSuggestions();
  setupBriefingActions();
  setupExport();
  setupGapAnalysis();
  setupPriorityWeights();
  setupSchoolRadius();

  // First render: TA ranking, the whole district, then the map is ready.
  document.getElementById("sources").textContent = `Sources: ${data.summary.meta.sources}`;
  const taRows = buildTAComparison();
  renderTAComparison(taRows);
  populateTASelect(taRows);
  selectDistrict();
  if (!baseTilesFailed()) mapStatus.hidden = true;

  setupInsightTabs();
  setupPopupActions();
  setupLayerToggles();
}

setupRangeFills();
setupShell();
init().catch((err) => {
  console.error(err);
  mapStatus.hidden = false;
  mapStatus.textContent = `Could not load the Dedza planning layers: ${err.message}`;
  document.getElementById("kpis").innerHTML =
    `<p>Could not load the Dedza planning layers: ${escapeHtml(err.message)}</p>`;
});
