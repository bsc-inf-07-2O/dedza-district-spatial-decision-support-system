// render(active): redraw every map layer, summary and table for the selected
// area. Called whenever the area, a threshold, a weight or a candidate changes.
import { data } from "../data.js";
import { layers, mapSymbol, roadWeight, selectedBoundary } from "../map/map.js";
import { state } from "../state.js";
import { escapeHtml, schoolLevel } from "../ui/format.js";
import { POPUP_OPTIONS, popupCard } from "../ui/popup.js";
import { clippedCellArea } from "./area.js";
import { renderBriefing, renderPopulationProfile, renderSchoolProfile, renderSettlementAccess } from "./briefing.js";
import { cellScore, renderCellCoverage, renderPriorityCells } from "./grid.js";
import { drawHealthWalkingArea } from "./layers.js";
import { renderScenario } from "./site-results.js";

const areaStatus = document.getElementById("area-status");

export function render(active) {
  Object.values(layers).forEach((layer) => {
    if (layer instanceof L.LayerGroup) layer.clearLayers();
  });
  selectedBoundary.clearLayers();

  if (state.areas.length) {
    selectedBoundary.addData({ type: "FeatureCollection", features: state.areas });
  }

  renderCellCoverage(active.cells);
  drawReferenceLayers(active);
  renderPriorityCells(active);
  renderSummaries(active);
  renderScenario(active);
}

// Roads, waterways, facilities, schools and settlements with their popups.
function drawReferenceLayers(active) {
  const { schoolRadiusKm } = state;
  L.geoJSON(active.roads, {
    style: (feature) => ({
      color: "#d4d0c8",
      weight: roadWeight(feature.properties.highway),
      opacity: 0.75,
    }),
  }).addTo(layers.roads);

  L.geoJSON(active.registry, {
    pointToLayer: (_, latlng) => L.marker(
      latlng,
      { icon: mapSymbol("diamond", "#8bd4c4", "#176a7b", 11) }
    ),
    onEachFeature: (feature, layer) => {
      const p = feature.properties;
      layer.bindPopup(popupCard({
        kicker: "MHFR · official facility registry",
        title: p.name,
        badge: { text: p.facility_type, tone: "reference" },
        rows: [["Ownership", p.ownership], ["MHFR code", p.facility_code]],
        note: "Reference layer from the Ministry of Health registry; the coverage grid uses the MOH baseline facilities.",
        actions: [
          { action: "route-to", label: "Route to here", icon: "route", primary: true, data: { index: data.routeIndexByFacility.get(feature) } },
        ],
      }), POPUP_OPTIONS);
    },
  }).addTo(layers.registry);

  L.geoJSON(active.waterways, {
    style: { color: "#51b5d5", weight: 1.6, opacity: 0.9 },
  }).addTo(layers.water);

  active.facilities.forEach((facility) => {
    L.marker([facility.lat, facility.lon], {
      icon: mapSymbol("circle", "#fff", "#cf5c36", facility.facility_type === "Hospital" ? 14 : 10),
    })
      .bindPopup(popupCard({
        kicker: "MOH baseline health facility",
        title: facility.name,
        badge: { text: facility.facility_type, tone: "facility" },
        rows: [["Owner", facility.owner], ["District health office", facility.dho]],
        actions: [
          { action: "route-to", label: "Route to here", icon: "route", primary: true, data: { index: data.routeIndexByFacility.get(facility) } },
          { action: "zoom", label: "Zoom in", icon: "zoom", data: { lat: facility.lat, lng: facility.lon, zoom: 13 } },
        ],
      }), POPUP_OPTIONS)
      .addTo(layers.facilities);
  });
  drawHealthWalkingArea();

  active.schools.forEach((school) => {
    const [lon, lat] = school.geometry.coordinates;
    L.marker([lat, lon], {
      icon: mapSymbol("square", "#f4a261", "#873e2e", 8),
    })
      .bindPopup(popupCard({
        kicker: "School",
        title: school.properties.name,
        badge: { text: schoolLevel(school.properties.name), tone: "school" },
        rows: [["School ID", school.properties.school_id], ["Status", school.properties.status]],
        actions: [
          { action: "zoom", label: "Zoom in", icon: "zoom", data: { lat, lng: lon, zoom: 14 } },
        ],
      }), POPUP_OPTIONS)
      .addTo(layers.schools);
  });

  active.settlements.forEach((settlement) => {
    const [lon, lat] = settlement.geometry.coordinates;
    const population = settlement.properties.population;
    const nearest = data.nearestSchoolBySettlement.get(settlement);
    const nearestFacility = data.nearestFacilityBySettlement.get(settlement);
    const withinSchoolRadius = nearest && nearest.distance <= schoolRadiusKm;
    const status = withinSchoolRadius ? "Within" : "Beyond";
    const buildings = settlement.properties.building_count;
    // Vector circles (cheap for ~1,200 points), sized by estimated population.
    L.circleMarker([lat, lon], {
      radius: Math.min(13, 3 + Math.sqrt(Number(population) || 0) / 18),
      color: withinSchoolRadius ? "#174b43" : "#7b2c25",
      weight: 1.5,
      fillColor: withinSchoolRadius ? "#8bd4c4" : "#e76f51",
      fillOpacity: 0.85,
    })
      .bindPopup(popupCard({
        kicker: `${settlement.properties.place || "Settlement"} · ${settlement.properties.adm3_name || "Dedza"}`,
        title: settlement.properties.name,
        badge: { text: `${status} ${schoolRadiusKm} km of a school`, tone: withinSchoolRadius ? "served" : "unserved" },
        rows: [
          ["Est. population (WorldPop 2025)", population ? Math.round(population).toLocaleString() : null],
          ["Buildings (GRID3)", buildings ? Number(buildings).toLocaleString() : null],
          ["Nearest school", nearest ? `${nearest.school.properties.name} (${nearest.distance.toFixed(1)} km)` : "none mapped"],
          ["Nearest health facility", nearestFacility
            ? `${nearestFacility.facility.name} (${nearestFacility.distance.toFixed(1)} km)`
            : "none mapped"],
        ],
        note: settlement.properties.source === "GRID3 v3.0"
          ? "Settlement extent from GRID3 v3.0; the name is borrowed from the nearest OSM place. Distances are walking distances along roads and paths."
          : "Distances are walking distances along roads and paths.",
        actions: [
          { action: "route-from", label: "Route from here", icon: "route", primary: true, data: { index: data.settlementIndex.get(settlement) } },
        ],
      }), POPUP_OPTIONS)
      .addTo(layers.settlements);
  });
}

// Overview KPIs, coverage bars, tables, briefing and the area status line.
function renderSummaries(active) {
  const { schoolRadiusKm } = state;
  const areaStats = active.cells.reduce((stats, cell) => {
    const area = clippedCellArea(cell);
    stats.area += area;
    stats.distance += (Number(cell.properties.dist_km) || 0) * area;
    stats[cell.properties.coverage] += area;
    if (cell.properties.coverage === "unserved") stats.unservedCells += 1;
    return stats;
  }, { area: 0, distance: 0, served: 0, underserved: 0, unserved: 0, unservedCells: 0 });
  const meanDistance = areaStats.area ? areaStats.distance / areaStats.area : 0;
  const schoolDistances = active.settlements
    .map((settlement) => data.nearestSchoolBySettlement.get(settlement)?.distance)
    .filter((distance) => Number.isFinite(distance));
  const healthDistances = active.settlements
    .map((settlement) => data.nearestFacilityBySettlement.get(settlement)?.distance)
    .filter((distance) => Number.isFinite(distance));
  const withinSchoolRadius = schoolDistances.filter((distance) => distance <= schoolRadiusKm).length;
  const meanSchoolDistance = schoolDistances.length
    ? schoolDistances.reduce((total, distance) => total + distance, 0) / schoolDistances.length
    : 0;
  const beyondHealthRadius = healthDistances.filter((distance) => distance > 5).length;
  const meanHealthDistance = healthDistances.length
    ? healthDistances.reduce((total, distance) => total + distance, 0) / healthDistances.length
    : 0;
  const areaName = state.name || "Dedza District";
  const population = data.populationByArea.get(areaName) || {};
  const populationTotal = population.total || 0;
  const populationUnserved = population.unserved || 0;
  const populationUnservedShare = populationTotal
    ? populationUnserved / populationTotal * 100
    : 0;
  const priorityCells = [...active.cells]
    .filter((cell) =>
      cell.properties.coverage === "unserved" && Number(cell.properties.area_km2) >= 1)
    .sort((a, b) => cellScore(b.properties) - cellScore(a.properties))
    .slice(0, 3);
  const schoolReach = active.settlements.filter(
    (settlement) => (data.nearestSchoolBySettlement.get(settlement)?.distance ?? Infinity) <= schoolRadiusKm
  ).length;
  renderSchoolProfile(active.settlements);
  renderPopulationProfile(population);
  renderSettlementAccess(active.settlements);
  renderBriefing(active, {
    healthGaps: beyondHealthRadius,
    schoolReach,
    unservedCells: areaStats.unservedCells,
    unservedArea: areaStats.unserved,
    priorityCells,
  });
  document.getElementById("kpis").innerHTML = [
    kpi("Health facilities · baseline", active.facilities.length),
    kpi("Health facilities · MHFR registry", active.registry.features.length),
    kpi("Schools", active.schools.length),
    kpi("Settlements", active.settlements.length),
    kpi(`Settlements within ${schoolRadiusKm} km of a school`, `${withinSchoolRadius}/${schoolDistances.length}`),
    kpi("Mean nearest-school distance", `${meanSchoolDistance.toFixed(1)} km`),
    kpi("Settlements > 5 km walk from health", `${beyondHealthRadius}/${healthDistances.length}`),
    kpi("Mean nearest-health distance", `${meanHealthDistance.toFixed(1)} km`),
    kpi("WorldPop 2025 estimate", Math.round(populationTotal).toLocaleString()),
    kpi("Population in unserved cells", `${Math.round(populationUnserved).toLocaleString()} · ${populationUnservedShare.toFixed(1)}%`),
    kpi(state.name ? "TA area (grid estimate)" : "District area", `${areaStats.area.toFixed(0)} km²`),
    kpi("Unserved area · > 8 km", `${areaStats.unserved.toFixed(0)} km²`),
    kpi("Underserved · 5–8 km", `${areaStats.underserved.toFixed(0)} km²`),
    kpi("Mean distance to care", `${meanDistance.toFixed(1)} km`),
    kpi("Unserved grid cells", areaStats.unservedCells),
  ].join("");

  const areaLabel = state.name ? escapeHtml(state.name) : "Dedza District";
  areaStatus.textContent = `${areaLabel} · ${active.schools.length} schools · ${active.facilities.length} baseline / ${active.registry.features.length} MHFR registry facilities · ${withinSchoolRadius}/${schoolDistances.length} settlements within ${schoolRadiusKm} km of school · ${beyondHealthRadius}/${healthDistances.length} settlements > 5 km from baseline health`;
  document.getElementById("school-access-status").textContent = `Updated: ${withinSchoolRadius} of ${schoolDistances.length} mapped settlements are within ${schoolRadiusKm} km of a school; settlement dots show within/beyond this threshold.`;
}

function kpi(label, value) {
  return `<div class="kpi"><b>${value}</b><span>${label}</span></div>`;
}
