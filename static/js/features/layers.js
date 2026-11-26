// Layers tool: layer switches, detected building footprints (loaded for the
// visible area), the 5 km walking-access area and environmental-layer status.
import { fetchJson } from "../api.js";
import { data } from "../data.js";
import { layers, map } from "../map/map.js";
import { state } from "../state.js";
import { activeData } from "./area.js";
import { renderScenario } from "./site-results.js";

const buildingToggle = document.getElementById("lyr-buildings");
const buildingLayerStatus = document.getElementById("building-layer-status");
let buildingRequestId = 0;
let buildingLoadTimer = null;

// Enable the optional layers that are installed and explain the others.
export function showLayerAvailability() {
  const { siteLayers } = data;
  const slopeToggle = document.getElementById("lyr-slope");
  const landCoverToggle = document.getElementById("lyr-land-cover");
  const floodToggle = document.getElementById("lyr-flood");
  const environmentLayerStatus = document.getElementById("environment-layer-status");
  const buildingLayer = siteLayers.buildings;
  buildingToggle.disabled = !buildingLayer.available;
  buildingToggle.title = buildingLayer.available
    ? "Show detected footprints; coverage may be incomplete or out of date."
    : buildingLayer.message;
  buildingLayerStatus.textContent = buildingLayer.available
    ? `${buildingLayer.count.toLocaleString()} detected footprints · ${buildingLayer.source} (${buildingLayer.date}; ${buildingLayer.license}). ${buildingLayer.message}`
    : `${buildingLayer.message} Run python scripts/import_buildings.py to fetch the Malawi tiles and clip them to Dedza.`;
  slopeToggle.disabled = !siteLayers.terrain.available;
  slopeToggle.title = siteLayers.terrain.message;
  landCoverToggle.disabled = !siteLayers.land_cover.available;
  landCoverToggle.title = siteLayers.land_cover.message;
  floodToggle.disabled = !siteLayers.flood.available;
  floodToggle.title = siteLayers.flood.message;
  environmentLayerStatus.textContent = [
    `Slope: ${siteLayers.terrain.message}`,
    `Land cover: ${siteLayers.land_cover.message}`,
    `Flood: ${siteLayers.flood.message}`,
  ].join(" ");
}

// ---- Detected building footprints (zoom 12+, visible area only) ----

async function loadBuildingFeatures() {
  const requestId = ++buildingRequestId;
  layers.buildings.clearLayers();
  if (!buildingToggle.checked || !data.siteLayers.buildings.available) return;
  if (map.getZoom() < 12) {
    buildingLayerStatus.textContent = "Zoom in to level 12 or closer to display detected building footprints.";
    return;
  }
  const bounds = map.getBounds();
  const bbox = [
    bounds.getWest(),
    bounds.getSouth(),
    bounds.getEast(),
    bounds.getNorth(),
  ].join(",");
  buildingLayerStatus.textContent = "Loading building footprints for the visible map area...";
  try {
    const result = await fetchJson(`/api/buildings?bbox=${encodeURIComponent(bbox)}`);
    if (requestId !== buildingRequestId) return;
    L.geoJSON(result, {
      style: {
        color: "#7b2cbf",
        weight: 1,
        fillColor: "#c77dff",
        fillOpacity: 0.48,
      },
      onEachFeature(feature, layer) {
        const properties = feature.properties || {};
        const details = document.createElement("div");
        details.textContent = `Detected building${properties.confidence == null ? "" : ` · confidence ${Math.round(properties.confidence * 100)}%`}${properties.height_m == null || properties.height_m < 0 ? "" : ` · estimated height ${properties.height_m} m`}`;
        layer.bindPopup(details);
      },
    }).addTo(layers.buildings);
    buildingLayerStatus.textContent = result.truncated
      ? `Showing the first ${result.features.length.toLocaleString()} footprints in this view. Zoom in further to see more.`
      : `${result.features.length.toLocaleString()} detected footprints shown in this view. Absence of a footprint does not prove land is vacant.`;
  } catch (error) {
    if (requestId === buildingRequestId) {
      buildingLayerStatus.textContent = `Could not load building footprints: ${error.message}`;
    }
  }
}

function scheduleBuildingLoad() {
  clearTimeout(buildingLoadTimer);
  buildingLoadTimer = setTimeout(loadBuildingFeatures, 200);
}

// ---- Area within a 5 km walk of a baseline health facility ----
// Calculated on the server along roads, tracks and paths; loaded the first
// time the layer is shown.
let healthWalkingArea = null;
let healthWalkingAreaRequest = null;

export function drawHealthWalkingArea() {
  layers.buffers.clearLayers();
  if (!healthWalkingArea?.geometry) return;
  L.geoJSON(healthWalkingArea, {
    interactive: false,
    style: { color: "#2a9d8f", weight: 1.5, dashArray: "4 4", fillColor: "#2a9d8f", fillOpacity: 0.08 },
  }).addTo(layers.buffers);
}

function loadHealthWalkingArea() {
  if (healthWalkingArea || healthWalkingAreaRequest) return;
  healthWalkingAreaRequest = fetch("/api/health-walking-area")
    .then((response) => (response.ok ? response.json() : Promise.reject(new Error(response.statusText))))
    .then((feature) => {
      healthWalkingArea = feature;
      drawHealthWalkingArea();
    })
    .catch(() => {
      healthWalkingAreaRequest = null;
    });
}

// ---- Layer switches ----

export function setupLayerToggles() {
  const bind = (id, group) => {
    document.getElementById(id).addEventListener("change", (ev) => {
      if (ev.target.checked) map.addLayer(group);
      else map.removeLayer(group);
    });
  };
  // Candidate sites: the checkbox hides both the map markers and the results panel.
  const scenarioToggle = document.getElementById("lyr-scenario");
  scenarioToggle.addEventListener("change", () => {
    state.scenarioLayerHidden = !scenarioToggle.checked;
    if (scenarioToggle.checked) map.addLayer(layers.scenario);
    else map.removeLayer(layers.scenario);
    renderScenario(activeData());
  });
  bind("lyr-cells", layers.cells);
  bind("lyr-facilities", layers.facilities);
  bind("lyr-registry", layers.registry);
  bind("lyr-schools", layers.schools);
  bind("lyr-settlements", layers.settlements);
  bind("lyr-buffers", layers.buffers);
  document.getElementById("lyr-buffers").addEventListener("change", (ev) => {
    if (ev.target.checked) loadHealthWalkingArea();
  });
  bind("lyr-roads", layers.roads);
  bind("lyr-water", layers.water);
  bind("lyr-priority", layers.priority);
  bind("lyr-buildings", layers.buildings);
  bind("lyr-slope", layers.slope);
  bind("lyr-land-cover", layers.landCover);
  bind("lyr-flood", layers.flood);
  buildingToggle.addEventListener("change", loadBuildingFeatures);
  map.on("moveend", scheduleBuildingLoad);
}
