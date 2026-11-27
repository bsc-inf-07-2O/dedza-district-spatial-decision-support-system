// Everything the page loads from the API at start-up, plus lookups built from it.
// `data` is filled once by loadData() and then only read.
import { fetchJson } from "./api.js";
import { state } from "./state.js";

export const data = {};

// Criteria of the priority score, in slider order, with their labels.
export const PRIORITY_CRITERIA = {
  population: "population 2030",
  growth: "growth",
  distance: "distance to care",
  road: "road access",
  flood: "flood safety",
};

export async function loadData() {
  const [summary, boundary, facilities, registry, populationSummary, schools, settlements, cells, roads, waterways, taData, siteLayers, gapAnalysis, priorityScenario] = await Promise.all([
    fetchJson("/api/summary"),
    fetchJson("/api/boundary"),
    fetchJson("/api/facilities"),
    fetchJson("/api/registry-facilities"),
    fetchJson("/api/population-summary"),
    fetchJson("/api/schools"),
    fetchJson("/api/settlements"),
    fetchJson("/api/cells"),
    fetchJson("/api/roads"),
    fetchJson("/api/waterways"),
    fetchJson("/api/traditional-authorities"),
    fetchJson("/api/site-layers"),
    fetchJson("/api/gap-analysis"),
    fetchJson("/api/priority-scenario"),
  ]);
  Object.assign(data, {
    summary, boundary, facilities, registry, populationSummary, schools, settlements,
    cells, roads, waterways, taData, siteLayers, gapAnalysis, priorityScenario,
  });

  // Traditional Authorities by name (one TA can have several polygons).
  const taFeatures = taData.features;
  data.taNames = [...new Set(taFeatures.map((feature) => feature.properties.name))]
    .sort((a, b) => a.localeCompare(b));
  data.taByName = new Map(data.taNames.map((name) => [
    name.toLocaleLowerCase(), taFeatures.filter((feature) => feature.properties.name === name),
  ]));

  // WorldPop totals per area: { total, served, underserved, unserved }.
  data.populationByArea = new Map();
  populationSummary.forEach((row) => {
    const area = data.populationByArea.get(row.area_name) || {};
    area[row.coverage] = Number(row.population_est) || 0;
    data.populationByArea.set(row.area_name, area);
  });

  data.roadAccessKm = Number(summary.meta.road_access_km) || 2;
  // Baseline weights come from the database (meta table) via the API.
  data.baselineWeights = priorityScenario.baseline_weights;
  data.baselineWeightsLabel = Object.keys(PRIORITY_CRITERIA)
    .map((key) => Math.round(data.baselineWeights[key]))
    .join(" / ");
  data.baselineWeightsText = Object.entries(PRIORITY_CRITERIA)
    .map(([key, label]) => `${label} ${Math.round(data.baselineWeights[key])}%`)
    .join(", ");

  // Route destinations: baseline facilities first, then the MHFR registry.
  data.routeFacilities = [
    ...facilities.map((facility) => ({
      name: facility.name,
      source: "Baseline",
      type: facility.facility_type,
      lon: Number(facility.lon),
      lat: Number(facility.lat),
    })),
    ...registry.features.map((facility) => ({
      name: facility.properties.name,
      source: "MHFR registry",
      type: `${facility.properties.facility_type} · ${facility.properties.ownership}`,
      lon: Number(facility.geometry.coordinates[0]),
      lat: Number(facility.geometry.coordinates[1]),
    })),
  ];
  // Object -> dropdown index, so map popups can pre-fill the route form.
  data.routeIndexByFacility = new Map();
  facilities.forEach((facility, index) => data.routeIndexByFacility.set(facility, index));
  registry.features.forEach((feature, index) =>
    data.routeIndexByFacility.set(feature, facilities.length + index));
  data.settlementIndex = new Map(settlements.features.map((feature, index) => [feature, index]));

  // Walking distance (km) along roads, tracks and paths, calculated on the server:
  // to the nearest school of any level, and to the nearest primary / secondary school.
  data.nearestSchoolByLevel = { all: new WeakMap(), primary: new WeakMap(), secondary: new WeakMap() };
  data.nearestFacilityBySettlement = new WeakMap();
  settlements.features.forEach((settlement) => {
    const p = settlement.properties;
    Object.entries(data.nearestSchoolByLevel).forEach(([level, lookup]) => {
      const suffix = level === "all" ? "" : `_${level}`;
      const index = p[`nearest_school${suffix}_index`];
      const distance = p[`walk_km_school${suffix}`];
      const school = index == null ? null : schools.features[index];
      lookup.set(settlement, school && distance != null ? { school, distance: Number(distance) } : null);
    });
    data.nearestFacilityBySettlement.set(
      settlement,
      p.nearest_health && p.walk_km_health != null ? { facility: p.nearest_health, distance: Number(p.walk_km_health) } : null
    );
  });
}

// Nearest school of the chosen level ({ school, distance } or null).
export function nearestSchool(settlement, level = state.schoolLevel) {
  return data.nearestSchoolByLevel[level]?.get(settlement) ?? null;
}

// School features of the chosen level. Schools whose names give no level
// (level_group null) only count under "all".
export function schoolsAtLevel(features, level = state.schoolLevel) {
  return level === "all" ? features : features.filter((school) => school.properties.level_group === level);
}
