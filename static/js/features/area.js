// Area of interest: the whole district or one Traditional Authority (TA).
import { data } from "../data.js";
import { map } from "../map/map.js";
import { state } from "../state.js";
import { render } from "./render.js";

const taSelect = document.getElementById("ta-select");
const districtButton = document.getElementById("clear-ta");
const placeScenarioButton = document.getElementById("place-scenario");
let districtLayer = null;

export function showDistrictBoundary() {
  districtLayer = L.geoJSON(data.boundary, {
    style: { color: "#e8f1ee", weight: 2, fill: false },
  }).addTo(map);
  map.fitBounds(districtLayer.getBounds().pad(0.04));
}

// ---- What lies inside the selected area ----

function intersectsSelectedArea(feature) {
  if (!state.areas.length) return true;
  try {
    return state.areas.some((area) => turf.booleanIntersects(feature, area));
  } catch {
    return false;
  }
}

function selectedFacilities() {
  return data.facilities.filter((facility) => {
    if (!state.areas.length) return true;
    const point = turf.point([facility.lon, facility.lat]);
    return state.areas.some((area) => turf.booleanPointInPolygon(point, area));
  });
}

function selectedRegistryFacilities() {
  return data.registry.features.filter((facility) => intersectsSelectedArea(facility));
}

function selectedSchools() {
  return data.schools.features.filter((school) => {
    if (!state.areas.length) return true;
    return state.areas.some((area) => turf.booleanPointInPolygon(school, area));
  });
}

function selectedSettlements() {
  return data.settlements.features.filter((settlement) => {
    if (!state.areas.length) return true;
    return state.areas.some((area) => turf.booleanPointInPolygon(settlement, area));
  });
}

// Area (km²) of a grid cell inside the selected area.
export function clippedCellArea(cell) {
  if (!state.areas.length) return Number(cell.properties.area_km2) || 0;
  return state.areas.reduce((areaTotal, area) => {
    try {
      const overlap = turf.intersect(turf.featureCollection([cell, area]));
      return areaTotal + (overlap ? turf.area(overlap) / 1e6 : 0);
    } catch {
      return areaTotal;
    }
  }, 0);
}

// The layers clipped to the selected area; passed to render().
export function activeData() {
  const inArea = (feature) => intersectsSelectedArea(feature);
  return {
    cells: data.cells.features.filter(inArea),
    facilities: selectedFacilities(),
    registry: { type: "FeatureCollection", features: selectedRegistryFacilities() },
    schools: selectedSchools(),
    settlements: selectedSettlements(),
    roads: { type: "FeatureCollection", features: data.roads.features.filter(inArea) },
    waterways: { type: "FeatureCollection", features: data.waterways.features.filter(inArea) },
  };
}

// Is a turf point inside the selected TA (or the district when none is selected)?
export function pointInScope(point) {
  return state.areas.length
    ? state.areas.some((area) => turf.booleanPointInPolygon(point, area))
    : data.boundary.features.some((feature) => turf.booleanPointInPolygon(point, feature));
}

// ---- Selecting an area ----

// Reflect the current area in the dropdown without re-triggering selection.
function syncTASelect() {
  if ((taSelect.value || null) === state.name) return;
  taSelect.value = state.name || "";
  taSelect.dispatchEvent(new Event("change", { bubbles: true }));
}

export function selectTA(name) {
  const areas = data.taByName.get(name.toLocaleLowerCase());
  if (!areas) return false;
  state.name = areas[0].properties.name;
  state.areas = areas;
  state.scenarios = [];
  state.nextScenarioId = 1;
  state.placingScenario = false;
  state.scenarioResultsCollapsed = false;
  placeScenarioButton.textContent = "Add site on map";
  districtButton.setAttribute("aria-pressed", "false");
  syncTASelect();
  const areaLayer = L.geoJSON({ type: "FeatureCollection", features: areas });
  map.fitBounds(areaLayer.getBounds().pad(0.12));
  render(activeData());
  return true;
}

export function selectDistrict() {
  state.name = null;
  state.areas = [];
  districtButton.setAttribute("aria-pressed", "true");
  syncTASelect();
  map.fitBounds(districtLayer.getBounds().pad(0.04));
  render(activeData());
}

export function setupAreaControls() {
  taSelect.addEventListener("change", () => {
    const value = taSelect.value || null;
    if (value === state.name) return; // programmatic sync, already applied
    if (value) selectTA(value);
    else selectDistrict();
  });
  districtButton.addEventListener("click", selectDistrict);
}
