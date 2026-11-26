// "Suggest best sites": greedy maximal coverage on walking distance, calculated
// on the server (/api/suggest-sites), then nudged off detected buildings.
import { postJson } from "../api.js";
import { map } from "../map/map.js";
import { state } from "../state.js";
import { showNotice } from "../ui/notice.js";
import { activeData, pointInScope } from "./area.js";
import { render } from "./render.js";
import { estimateScenarioCandidate, screenCandidateSite, showScenarioLayer } from "./sites.js";

const scenarioTypeInput = document.getElementById("scenario-type");
const scenarioRadiusInput = document.getElementById("scenario-radius");
const scenarioStatus = document.getElementById("scenario-status");
const placeScenarioButton = document.getElementById("place-scenario");
const suggestSitesButton = document.getElementById("suggest-sites");

// Results are cached per area, facility type and radius.
const suggestionCache = new Map();
const suggestionPending = new Set();

async function fetchSuggestedSites(type, radiusKm, count = 3, exclude = []) {
  const result = await postJson(
    "/api/suggest-sites",
    { type, radius_km: radiusKm, area: state.name || null, exclude, count },
    "Site suggestion",
  );
  return result.picks;
}

// Best single site for the briefing: undefined while calculating, null if none.
// The briefing re-renders when the answer arrives.
export function cachedSuggestion(type, radiusKm) {
  const key = `${state.name || ""}|${type}|${radiusKm}`;
  if (suggestionCache.has(key)) return suggestionCache.get(key);
  if (!suggestionPending.has(key)) {
    suggestionPending.add(key);
    fetchSuggestedSites(type, radiusKm, 1)
      .then((picks) => suggestionCache.set(key, picks[0] || null))
      .catch(() => suggestionCache.set(key, null))
      .finally(() => {
        suggestionPending.delete(key);
        if (key.startsWith(`${state.name || ""}|`)) render(activeData());
      });
  }
  return undefined;
}

// Nudge a suggested point off any detected building (up to 500 m away).
async function findOpenSiteNear(coordinates) {
  const offsets = [[0, 0]];
  [0.15, 0.3, 0.5].forEach((km) => {
    for (let bearing = 0; bearing < 360; bearing += 45) offsets.push([km, bearing]);
  });
  for (const [km, bearing] of offsets) {
    const point = km ? turf.destination(coordinates, km, bearing, { units: "kilometers" }) : turf.point(coordinates);
    if (!pointInScope(point)) continue;
    const siteScreening = await screenCandidateSite(point.geometry.coordinates);
    if (!siteScreening.occupied) return { point, siteScreening };
  }
  return null;
}

// Add the top 3 suggested sites as candidates. `type` / `radius` override the
// New sites form (used by the briefing's "Compare top 3 sites" buttons).
export async function addSuggestedSites({ type, radius } = {}) {
  if (type && scenarioTypeInput.value !== type) {
    scenarioTypeInput.value = type;
    scenarioTypeInput.dispatchEvent(new Event("change", { bubbles: true }));
  }
  if (radius) scenarioRadiusInput.value = String(radius);
  const siteType = scenarioTypeInput.value;
  const radiusKm = Number(scenarioRadiusInput.value);
  if (!Number.isFinite(radiusKm) || radiusKm < 1 || radiusKm > 50) {
    showNotice("Invalid radius", "Enter a scenario radius between 1 and 50 km.", "error", true);
    return;
  }
  const label = siteType === "school" ? "school" : "health centre";
  suggestSitesButton.disabled = true;
  suggestSitesButton.classList.add("is-loading");
  showNotice("Finding best sites", `Calculating walking reach for ${label} locations…`, "warning", false);
  let picks;
  try {
    picks = await fetchSuggestedSites(siteType, radiusKm, 3, state.scenarios
      .filter((candidate) => candidate.type === siteType)
      .map((candidate) => candidate.point.geometry.coordinates));
  } catch (error) {
    suggestSitesButton.disabled = false;
    suggestSitesButton.classList.remove("is-loading");
    showNotice("Suggestions unavailable", error.message, "error", true);
    return;
  }
  if (!picks.length) {
    suggestSitesButton.disabled = false;
    suggestSitesButton.classList.remove("is-loading");
    const message = `Every populated settlement in ${state.name || "Dedza"} is already within ${radiusKm} km walking distance of a ${siteType === "school" ? "school" : "health facility"} or a candidate site.`;
    scenarioStatus.textContent = message;
    showNotice("No coverage gap at this radius", message, "success", true);
    return;
  }
  state.placingScenario = false;
  placeScenarioButton.textContent = state.scenarios.length ? "Add another site" : "Add site on map";
  const added = [];
  try {
    for (const pick of picks) {
      const open = await findOpenSiteNear(pick.coordinates);
      if (!open) continue;
      const candidate = {
        id: state.nextScenarioId++,
        point: open.point,
        type: siteType,
        radius: radiusKm,
        siteScreening: open.siteScreening,
        loading: true,
        suggestion: {
          name: pick.name,
          people: pick.people,
          settlementCount: pick.settlement_count,
        },
      };
      state.scenarios.push(candidate);
      added.push(candidate);
    }
  } catch (error) {
    showNotice("Screening unavailable", `Could not screen suggested sites: ${error.message}`, "error", true);
  } finally {
    suggestSitesButton.disabled = false;
    suggestSitesButton.classList.remove("is-loading");
  }
  if (!added.length) {
    showNotice("No open site found", "Every suggested location overlaps detected buildings. Place a site manually.", "error", true);
    return;
  }
  showScenarioLayer();
  state.scenarioResultsCollapsed = false;
  placeScenarioButton.textContent = "Add another site";
  const totalPeople = added.reduce((sum, candidate) => sum + candidate.suggestion.people, 0);
  const message = `${added.length} suggested ${label} site${added.length === 1 ? "" : "s"} added. Each passes the flood, road, slope and land-cover checks; together they could bring about ${Math.round(totalPeople).toLocaleString()} people in mapped settlements (2030 projection) within ${radiusKm} km walking distance. Estimating full reach…`;
  scenarioStatus.textContent = message;
  showNotice("Suggested sites added", message, "success", true);
  render(activeData());
  const bounds = L.latLngBounds(added.map((candidate) => {
    const [lon, lat] = candidate.point.geometry.coordinates;
    return [lat, lon];
  }));
  map.fitBounds(bounds.pad(0.6), { maxZoom: 12 });
  added.forEach((candidate) => estimateScenarioCandidate(candidate));
}

export function setupSuggestions() {
  suggestSitesButton.addEventListener("click", () => addSuggestedSites());
}
