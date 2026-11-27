// Routes tool: settlement -> health facility routes from /api/route, drawn on
// the map with a comparison card (shortest, quickest, alternatives).
import { data } from "../data.js";
import { map, routeColors, routeLayer } from "../map/map.js";
import { formatEstimatedRouteTime } from "../ui/format.js";

const routeSettlementSelect = document.getElementById("route-settlement");
const routeFacilitySelect = document.getElementById("route-facility");
const routeModeSelect = document.getElementById("route-mode");
const findRouteButton = document.getElementById("find-route");
const clearRouteButton = document.getElementById("clear-route");
const routeResultsPanel = document.getElementById("route-results-panel");
const routeControlStatus = document.getElementById("route-control-status");
const routeStatus = document.getElementById("route-status");
const routeOptionsContainer = document.getElementById("route-options");
const routeRoadDistance = document.getElementById("route-road-distance");
const routeTotalDistance = document.getElementById("route-total-distance");
const routeTravelTime = document.getElementById("route-travel-time");
const routeConnectors = document.getElementById("route-connectors");
const routeResultsToggle = document.getElementById("toggle-route-results");

let routeLoading = false;
let routeChoices = [];

export const routeIsLoading = () => routeLoading;

// Fill the From / To dropdowns (runs before they are turned into custom selects).
export function fillRouteOptions() {
  data.settlements.features.forEach((settlement, index) => {
    const option = document.createElement("option");
    const [lon, lat] = settlement.geometry.coordinates;
    option.value = String(index);
    option.textContent = settlement.properties.name;
    option.dataset.meta = `${settlement.properties.adm3_name || "Dedza"} · ${lat.toFixed(3)}, ${lon.toFixed(3)}`;
    routeSettlementSelect.appendChild(option);
  });
  const baselineFacilities = document.createElement("optgroup");
  baselineFacilities.label = "Baseline health facilities";
  const referenceFacilities = document.createElement("optgroup");
  referenceFacilities.label = "MHFR registry (official MoH list)";
  data.routeFacilities.forEach((facility, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = facility.name;
    option.dataset.meta = facility.type || facility.source;
    (facility.source === "Baseline" ? baselineFacilities : referenceFacilities).appendChild(option);
  });
  routeFacilitySelect.append(baselineFacilities, referenceFacilities);
}

function updateFindRouteButton() {
  findRouteButton.disabled = routeLoading
    || routeSettlementSelect.value === ""
    || routeFacilitySelect.value === "";
}

// ---- Comparing route choices ----

function roadSegmentLengths(choice) {
  const road = choice.route.features.find((feature) => feature.properties.kind === "road");
  const segments = new Map();
  if (!road) return segments;
  const coordinates = road.geometry.coordinates;
  for (let index = 1; index < coordinates.length; index += 1) {
    const start = coordinates[index - 1];
    const end = coordinates[index];
    const startKey = `${start[0]},${start[1]}`;
    const endKey = `${end[0]},${end[1]}`;
    const key = startKey < endKey ? `${startKey}|${endKey}` : `${endKey}|${startKey}`;
    segments.set(key, turf.distance(turf.point(start), turf.point(end), { units: "kilometers" }));
  }
  return segments;
}

function sharedRoadPercentage(choice, shortest) {
  const shortestSegments = roadSegmentLengths(shortest);
  const choiceSegments = roadSegmentLengths(choice);
  let sharedDistance = 0;
  shortestSegments.forEach((distance, segment) => {
    if (choiceSegments.has(segment)) sharedDistance += distance;
  });
  return shortest.network_distance_km > 0
    ? Math.min(100, Math.round((sharedDistance / shortest.network_distance_km) * 100))
    : 100;
}

function routeChoiceDetails(choice, index) {
  const details = `${choice.network_distance_km.toFixed(1)} km on roads · ${choice.total_distance_km.toFixed(1)} km total · about ${formatEstimatedRouteTime(choice.estimated_network_time_min)}`;
  if (index === 0 || !routeChoices[0]) return details;

  const shortest = routeChoices[0];
  const distanceDifference = choice.network_distance_km - shortest.network_distance_km;
  const timeDifference = Math.round(choice.estimated_network_time_min / 5) * 5
    - Math.round(shortest.estimated_network_time_min / 5) * 5;
  const distanceComparison = Math.abs(distanceDifference) < 0.05
    ? "same distance"
    : `${Math.abs(distanceDifference).toFixed(1)} km ${distanceDifference < 0 ? "shorter" : "longer"}`;
  const timeComparison = Math.abs(timeDifference) < 1
    ? "similar time"
    : `${formatEstimatedRouteTime(Math.abs(timeDifference))} ${timeDifference < 0 ? "faster" : "slower"}`;
  const sharedPercent = sharedRoadPercentage(choice, shortest);
  return `${details} · ${distanceComparison}, ${timeComparison} vs shortest · shares ${sharedPercent}% of its road path`;
}

// ---- Drawing ----

function clearRouteMetrics() {
  routeRoadDistance.textContent = "—";
  routeTotalDistance.textContent = "—";
  routeTravelTime.textContent = "—";
  routeConnectors.textContent = "—";
}

function updateSelectedRouteStatus(index, settlement, facility) {
  const choice = routeChoices[index];
  if (!choice) return;
  const connectorKm = (choice.origin_snap_m + choice.destination_snap_m) / 1000;
  const estimatedTime = formatEstimatedRouteTime(choice.estimated_network_time_min);
  routeRoadDistance.textContent = `${choice.network_distance_km.toFixed(1)} km`;
  routeTotalDistance.textContent = `${choice.total_distance_km.toFixed(1)} km`;
  routeTravelTime.textContent = `~${estimatedTime}`;
  routeConnectors.textContent = `${connectorKm.toFixed(2)} km`;
  const how = {
    walking: "on foot at 5 km/h along roads and paths. Excludes rests, terrain and river crossings.",
    bicycle: "by bicycle at 12 km/h along roads, tracks and paths. Excludes rests, hills and river crossings.",
    vehicle: "by motorcycle or ambulance on mapped roads. Approximate time; excludes traffic, road conditions, and access time.",
  }[routeModeSelect.value];
  routeStatus.textContent = `${choice.label}: ${settlement.properties.name} to ${facility.name}, ${how}`;
}

function clearRoute() {
  routeLayer.clearLayers();
  routeChoices = [];
  routeOptionsContainer.replaceChildren();
  routeResultsPanel.hidden = true;
  clearRouteButton.disabled = true;
  routeControlStatus.textContent = "Choose a settlement and facility to estimate a route.";
  routeStatus.textContent = "Choose a settlement and facility to estimate a route.";
  clearRouteMetrics();
}

function displayRouteChoice(index, settlement, facility) {
  const choice = routeChoices[index];
  if (!choice) return;
  routeLayer.clearLayers();
  const drawRoute = (routeIndex, selected) => {
    L.geoJSON(routeChoices[routeIndex].route, {
      filter: (feature) => feature.properties.kind === "road"
        || (selected && feature.properties.kind === "connector"),
      style: (feature) => feature.properties.kind === "connector"
        ? { color: "#f4d8ff", weight: 3, dashArray: "5 6" }
        : {
          color: routeColors[routeIndex % routeColors.length],
          weight: selected ? 6 : 3,
          opacity: selected ? 1 : 0.72,
          dashArray: selected ? null : "7 5",
        },
    }).addTo(routeLayer);
  };
  routeChoices.forEach((_, routeIndex) => {
    if (routeIndex !== index) drawRoute(routeIndex, false);
  });
  drawRoute(index, true);
  const endpoints = choice.route.features.filter(
    (feature) => feature.properties.kind === "origin"
      || feature.properties.kind === "destination",
  );
  L.geoJSON({ type: "FeatureCollection", features: endpoints }, {
    pointToLayer: (feature, latlng) => {
      const isOrigin = feature.properties.kind === "origin";
      return L.circleMarker(latlng, {
        radius: 8,
        color: isOrigin ? "#174b43" : "#7b2c25",
        weight: 2,
        fillColor: isOrigin ? "#8bd4c4" : "#e76f51",
        fillOpacity: 1,
      }).bindPopup(isOrigin ? "Route starts here" : "Route ends here");
    },
  }).addTo(routeLayer);
  map.fitBounds(routeLayer.getBounds().pad(0.12));
  [...routeOptionsContainer.children].forEach((button, buttonIndex) => {
    button.setAttribute("aria-pressed", String(buttonIndex === index));
  });
  updateSelectedRouteStatus(index, settlement, facility);
}

export async function findRoute() {
  const settlement = data.settlements.features[Number(routeSettlementSelect.value)];
  const facility = data.routeFacilities[Number(routeFacilitySelect.value)];
  if (!settlement || !facility) return;

  routeLoading = true;
  updateFindRouteButton();
  routeSettlementSelect.disabled = true;
  routeFacilitySelect.disabled = true;
  clearRouteButton.disabled = true;
  routeLayer.clearLayers();
  routeResultsPanel.hidden = true;
  routeOptionsContainer.replaceChildren();
  clearRouteMetrics();
  routeControlStatus.textContent = {
    walking: "Calculating the shortest walking route along roads and paths…",
    bicycle: "Calculating the shortest cycling route along roads, tracks and paths. The first request may take several seconds to prepare the network.",
    vehicle: "Calculating motorcycle / ambulance routes on mapped roads. The first request may take several seconds to prepare the road network.",
  }[routeModeSelect.value];
  findRouteButton.textContent = "Finding route...";
  findRouteButton.classList.add("is-loading");
  findRouteButton.setAttribute("aria-busy", "true");
  try {
    const response = await fetch("/api/route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        origin: settlement.geometry.coordinates,
        destination: [facility.lon, facility.lat],
        mode: routeModeSelect.value,
      }),
    });
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error(`The route service returned an invalid response (${response.status}).`);
    }
    if (!response.ok) throw new Error(payload.error || `Route request failed (${response.status}).`);

    routeChoices = payload.alternatives || [payload];
    routeOptionsContainer.replaceChildren();
    routeChoices.forEach((choice, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "route-option";
      button.style.setProperty("--route-color", routeColors[index % routeColors.length]);
      button.setAttribute("aria-pressed", "false");
      const title = document.createElement("strong");
      title.textContent = choice.label;
      const details = document.createElement("span");
      details.textContent = routeChoiceDetails(choice, index);
      button.append(title, details);
      button.addEventListener("click", () => displayRouteChoice(index, settlement, facility));
      routeOptionsContainer.append(button);
    });
    displayRouteChoice(0, settlement, facility);
    routeResultsPanel.hidden = false;
    routeControlStatus.textContent = "";
    clearRouteButton.disabled = false;
  } catch (error) {
    routeLayer.clearLayers();
    routeChoices = [];
    routeOptionsContainer.replaceChildren();
    routeResultsPanel.hidden = true;
    routeControlStatus.textContent = `Route unavailable: ${error.message}`;
    clearRouteButton.disabled = false;
  } finally {
    routeLoading = false;
    routeSettlementSelect.disabled = false;
    routeFacilitySelect.disabled = false;
    findRouteButton.textContent = "Find route";
    findRouteButton.classList.remove("is-loading");
    findRouteButton.removeAttribute("aria-busy");
    updateFindRouteButton();
  }
}

// The route card has its own Clear and Hide buttons, so it never traps the user.
function setRouteResultsCollapsed(collapsed) {
  routeResultsPanel.classList.toggle("collapsed", collapsed);
  routeResultsToggle.textContent = collapsed ? "Show" : "Hide";
  routeResultsToggle.setAttribute("aria-expanded", String(!collapsed));
  routeResultsToggle.title = collapsed ? "Show the route comparison" : "Minimise the route comparison";
}

export function setupRoutes() {
  const routeSelectionChanged = () => {
    routeLayer.clearLayers();
    routeChoices = [];
    routeOptionsContainer.replaceChildren();
    routeResultsPanel.hidden = true;
    clearRouteButton.disabled = true;
    clearRouteMetrics();
    routeControlStatus.textContent = routeSettlementSelect.value && routeFacilitySelect.value
      ? "Route choices changed. Click Find route to update the map."
      : "Choose a settlement and facility to estimate a route.";
    routeStatus.textContent = routeControlStatus.textContent;
    updateFindRouteButton();
  };
  routeSettlementSelect.addEventListener("change", routeSelectionChanged);
  routeModeSelect.addEventListener("change", () => {
    if (routeChoices.length && !routeLoading) findRoute();
  });
  routeFacilitySelect.addEventListener("change", routeSelectionChanged);
  findRouteButton.addEventListener("click", findRoute);
  clearRouteButton.addEventListener("click", clearRoute);
  routeResultsToggle.addEventListener("click", () =>
    setRouteResultsCollapsed(!routeResultsPanel.classList.contains("collapsed")));
  document.getElementById("close-route-results").addEventListener("click", clearRoute);
  new MutationObserver(() => {
    if (!routeResultsPanel.hidden) setRouteResultsCollapsed(false);
  }).observe(routeResultsPanel, { attributes: true, attributeFilter: ["hidden"] });
}
