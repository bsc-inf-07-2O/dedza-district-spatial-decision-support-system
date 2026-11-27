// New sites tool: placing candidate sites, screening them for buildings and
// estimating the population they would reach. The result cards are drawn by
// static/js/features/site-results.js.
import { postJson } from "../api.js";
import { layers, map } from "../map/map.js";
import { state } from "../state.js";
import { hideNotice, showNotice } from "../ui/notice.js";
import { activeData, pointInScope } from "./area.js";
import { render } from "./render.js";
import { renderScenario } from "./site-results.js";

const scenarioTypeInput = document.getElementById("scenario-type");
const scenarioRadiusInput = document.getElementById("scenario-radius");
const scenarioStatus = document.getElementById("scenario-status");
const placeScenarioButton = document.getElementById("place-scenario");
const clearScenarioButton = document.getElementById("clear-scenario");
const scenarioResultsToggle = document.getElementById("toggle-scenario-results");

// Does a detected building footprint contain this point?
export function screenCandidateSite(coordinates) {
  return postJson("/api/site-screening", { coordinates }, "Site screening");
}

// Population reached, site checks and road-time access, from /api/scenario-population.
export async function estimateScenarioCandidate(candidate) {
  candidate.estimateVersion = (candidate.estimateVersion || 0) + 1;
  const version = candidate.estimateVersion;
  candidate.populationEstimate = null;
  candidate.populationError = "";
  candidate.loading = true;
  renderScenario(activeData());
  try {
    const result = await postJson("/api/scenario-population", {
      coordinates: candidate.point.geometry.coordinates,
      type: candidate.type,
      radius_km: candidate.radius,
      school_level: candidate.schoolLevel || "all",
    }, "Population estimate");
    if (!Number.isFinite(result.additional_population_estimate)) {
      throw new Error("The population estimate response was incomplete.");
    }
    if (state.scenarios.includes(candidate) && candidate.estimateVersion === version) {
      candidate.populationEstimate = result;
    }
  } catch (error) {
    if (state.scenarios.includes(candidate) && candidate.estimateVersion === version) {
      candidate.populationError = error.message;
    }
  } finally {
    if (state.scenarios.includes(candidate) && candidate.estimateVersion === version) {
      candidate.loading = false;
      renderScenario(activeData());
    }
  }
}

// Facility type or radius changed: re-estimate every candidate with the new values.
function refreshScenarioEstimates() {
  state.scenarios.forEach((candidate) => {
    candidate.type = scenarioTypeInput.value;
    candidate.radius = Number(scenarioRadiusInput.value);
    estimateScenarioCandidate(candidate);
  });
}

// Re-show candidate sites (if the layer was switched off) when a site is added.
export function showScenarioLayer() {
  const toggle = document.getElementById("lyr-scenario");
  state.scenarioLayerHidden = false;
  if (toggle) toggle.checked = true;
  if (!map.hasLayer(layers.scenario)) map.addLayer(layers.scenario);
}

// Screen and add a candidate site (map click in placement mode, or a popup action).
export async function addCandidateAt(latlng) {
  const point = turf.point([latlng.lng, latlng.lat]);
  if (!pointInScope(point)) {
    const message = "Place the proposed facility inside the selected district or Traditional Authority.";
    scenarioStatus.textContent = message;
    showNotice("Candidate outside selected area", message, "error", true);
    return;
  }
  const radius = Number(scenarioRadiusInput.value);
  if (!Number.isFinite(radius) || radius < 1 || radius > 50) {
    const message = "Enter a scenario radius between 1 and 50 km.";
    scenarioStatus.textContent = message;
    showNotice("Invalid radius", message, "error", true);
    return;
  }
  const checkingMessage = "Checking for detected building footprints...";
  scenarioStatus.textContent = checkingMessage;
  showNotice("Screening candidate site", checkingMessage, "warning", false);
  const requestId = ++state.siteScreeningRequestId;
  state.siteScreeningPending = true;
  let siteScreening;
  try {
    siteScreening = await screenCandidateSite(point.geometry.coordinates);
  } catch (error) {
    if (requestId === state.siteScreeningRequestId) {
      scenarioStatus.textContent = `Could not screen candidate site: ${error.message}`;
      showNotice("Screening unavailable", `Could not screen candidate site: ${error.message}`, "error", true);
    }
    return;
  } finally {
    if (requestId === state.siteScreeningRequestId) {
      state.siteScreeningPending = false;
    }
  }
  if (requestId !== state.siteScreeningRequestId || !state.placingScenario) return;
  if (siteScreening.occupied) {
    const message = "Not added: the candidate point overlaps a detected building footprint. Choose another point.";
    scenarioStatus.textContent = message;
    showNotice("Building occupies this site", message, "error", true);
    return;
  }
  const type = scenarioTypeInput.value;
  const candidate = {
    id: state.nextScenarioId++,
    point,
    type,
    // A school candidate keeps the level chosen in the Schools tool when it was placed.
    schoolLevel: type === "school" ? state.schoolLevel : "all",
    radius,
    siteScreening,
    loading: true,
  };
  state.scenarios.push(candidate);
  showScenarioLayer();
  state.placingScenario = false;
  state.scenarioResultsCollapsed = false;
  placeScenarioButton.textContent = "Add another site";
  const successMessage = siteScreening.buildings_available
    ? "Candidate added. Review the estimates and verify the site in the field."
    : "Candidate added. Building data is unavailable, so occupancy was not screened.";
  scenarioStatus.textContent = successMessage;
  showNotice(
    siteScreening.buildings_available ? "Candidate accepted for analysis" : "Candidate added with limited screening",
    successMessage,
    siteScreening.buildings_available ? "success" : "warning",
    true
  );
  render(activeData());
  if (window.matchMedia("(max-width: 900px)").matches) {
    map.getContainer().scrollIntoView({
      block: "start",
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    });
  }
  estimateScenarioCandidate(candidate);
}

export function setupSiteControls() {
  placeScenarioButton.addEventListener("click", () => {
    state.placingScenario = !state.placingScenario;
    placeScenarioButton.textContent = state.placingScenario
      ? "Cancel placement"
      : state.scenarios.length ? "Add another site" : "Add site on map";
    if (state.placingScenario) {
      showNotice(
        "Place candidate site",
        "Click inside your selected area to place a site. Detected buildings block placement.",
        "warning",
        false
      );
    } else {
      hideNotice();
    }
    render(activeData());
  });
  scenarioResultsToggle.addEventListener("click", () => {
    state.scenarioResultsCollapsed = !state.scenarioResultsCollapsed;
    renderScenario(activeData());
  });
  clearScenarioButton.addEventListener("click", () => {
    const clearedCandidates = state.scenarios.length > 0;
    state.siteScreeningRequestId += 1;
    state.siteScreeningPending = false;
    hideNotice();
    state.scenarios = [];
    state.nextScenarioId = 1;
    state.placingScenario = false;
    state.scenarioResultsCollapsed = false;
    placeScenarioButton.textContent = "Add site on map";
    render(activeData());
    scenarioStatus.textContent = clearedCandidates
      ? "Candidate sites cleared from the map."
      : "Placement cancelled.";
  });
  document.getElementById("close-scenario-results").addEventListener("click", () => clearScenarioButton.click());
  scenarioRadiusInput.addEventListener("change", () => {
    const radius = Number(scenarioRadiusInput.value);
    if (!Number.isFinite(radius) || radius < 1 || radius > 50) {
      scenarioRadiusInput.value = String(state.scenarios[0]?.radius || 5);
      return;
    }
    if (state.scenarios.length) {
      refreshScenarioEstimates();
    }
  });
  scenarioTypeInput.addEventListener("change", () => {
    if (state.scenarios.length) {
      refreshScenarioEstimates();
    }
  });
  // Placement mode: the next map click adds a candidate (capture phase, before Leaflet).
  map.getContainer().addEventListener("click", async (event) => {
    if (event.target instanceof Element && event.target.closest(".map-scenario-results, .map-route-results, .leaflet-popup, .leaflet-control")) return;
    if (!state.placingScenario || state.siteScreeningPending) return;
    L.DomEvent.stopPropagation(event);
    L.DomEvent.preventDefault(event);
    await addCandidateAt(map.mouseEventToLatLng(event));
  }, true);
}
