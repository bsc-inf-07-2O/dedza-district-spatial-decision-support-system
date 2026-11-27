// "Export planning CSV": the selected area's summary, recommendations,
// candidate sites and per-settlement distances as one CSV file.
import { data, nearestSchool, schoolsAtLevel } from "../data.js";
import { state } from "../state.js";
import { csvField, nearPlace, schoolNoun } from "../ui/format.js";
import { activeData } from "./area.js";
import { buildRecommendedActions } from "./briefing.js";
import { candidateLabel, getScenarioMetrics } from "./site-results.js";

function exportPlanningBrief() {
  const { siteLayers } = data;
  const active = activeData();
  const areaLabel = state.name || "Dedza District";
  // School columns and figures follow the Schools tool's level filter.
  const schoolLevelLabel = { all: "All schools", primary: "Primary", secondary: "Secondary" }[state.schoolLevel];
  const noun = schoolNoun(state.schoolLevel);
  const rows = [[
    "section", "area", "metric", "value", "settlement", "osm_adm3_label",
    "nearest_school", "school_distance_km", "nearest_health_facility", "health_distance_km",
    "nearest_school_level",
  ]];
  const schoolCoverage = [1, 3, 5, 8].map((threshold) => [
    threshold,
    active.settlements.filter(
      (settlement) => (nearestSchool(settlement)?.distance ?? Infinity) <= threshold
    ).length,
  ]);
  const healthGaps = active.settlements.filter(
    (settlement) => (data.nearestFacilityBySettlement.get(settlement)?.distance ?? Infinity) > 5
  ).length;
  const unservedCells = active.cells.filter((cell) => cell.properties.coverage === "unserved").length;
  const population = data.populationByArea.get(areaLabel) || {};
  const totalPopulation = population.total || 0;
  const unservedPopulation = population.unserved || 0;
  rows.push(["summary", areaLabel, "school level analysed", schoolLevelLabel]);
  rows.push(["summary", areaLabel, state.schoolLevel === "all" ? "schools" : `${noun}s`, schoolsAtLevel(active.schools).length]);
  rows.push(["summary", areaLabel, "health facilities", active.facilities.length]);
  rows.push(["summary", areaLabel, "MHFR registry facilities (operational)", active.registry.features.length]);
  rows.push(["summary", areaLabel, "mapped settlements", active.settlements.length]);
  rows.push(["summary", areaLabel, "settlements over 5 km from health", healthGaps]);
  rows.push(["summary", areaLabel, "unserved health grid cells", unservedCells]);
  rows.push(["population", areaLabel, "WorldPop 2025 total estimate", Math.round(totalPopulation)]);
  rows.push(["population", areaLabel, "WorldPop 2025 in unserved cells", Math.round(unservedPopulation)]);
  rows.push(["population", areaLabel, "share in unserved cells", totalPopulation ? `${(unservedPopulation / totalPopulation * 100).toFixed(1)}%` : ""]);
  rows.push(["source", areaLabel, "population dataset", "WorldPop 2025 R2025A v1; 100 m population-count raster; CC BY 4.0"]);
  rows.push(["method", areaLabel, "population coverage", "Raster cells assigned to the 3 km grid's health-access classes (walking distance along roads and paths from each cell's population centre); not school access"]);
  rows.push(["source", areaLabel, "health baseline", "Existing MOH facility dataset used to derive the 3 km access grid"]);
  rows.push(["source", areaLabel, "MHFR registry", "Malawi Health Facility Registry (zipatala.health.gov.mw), Ministry of Health; operational facilities; separate reference layer"]);
  rows.push([
    "source",
    areaLabel,
    "building footprints",
    siteLayers.buildings.available
      ? `${siteLayers.buildings.source}; ${siteLayers.buildings.date}; ${siteLayers.buildings.license}`
      : "Unavailable; occupancy not screened",
  ]);
  schoolCoverage.forEach(([threshold, count]) => {
    rows.push(["school coverage", areaLabel, `settlements within ${threshold} km of a ${noun}`, `${count}/${active.settlements.length}`]);
  });
  buildRecommendedActions(active).forEach((action, index) => {
    rows.push(["recommendation", areaLabel, `action ${index + 1}`, action.text]);
  });
  const ranked = state.scenarios
    .filter((candidate) => candidate.populationEstimate)
    .sort((a, b) => b.populationEstimate.additional_population_estimate - a.populationEstimate.additional_population_estimate);
  ranked.forEach((candidate, index) => {
    rows.push([
      "candidate ranking", areaLabel, `rank ${index + 1}`,
      `Candidate ${candidate.id}${candidate.suggestion ? ` (suggested ${nearPlace(candidate.suggestion.name)})` : ""}: +${Math.round(candidate.populationEstimate.additional_population_estimate)} people`,
    ]);
  });

  state.scenarios.forEach((candidate) => {
    const label = `candidate ${candidate.id}`;
    const metrics = getScenarioMetrics(active, candidate);
    rows.push(["scenario", areaLabel, `${label} facility type`, candidateLabel(candidate)]);
    rows.push([
      "site screening",
      areaLabel,
      `${label} detected building occupancy`,
      candidate.siteScreening.buildings_available
        ? "No detected footprint contains the candidate point; vacancy not confirmed"
        : "Not screened; building data unavailable",
    ]);
    rows.push(["scenario", areaLabel, `${label} radius km`, candidate.radius]);
    rows.push(["scenario", areaLabel, `${label} additional settlements in selected geography`, metrics.gain]);
    if (candidate.populationEstimate) {
      rows.push([
        "scenario",
        "Dedza District",
        `${label} estimated additional population`,
        Math.round(candidate.populationEstimate.additional_population_estimate),
      ]);
      rows.push(["method", "Dedza District", `${label} population estimate`, candidate.populationEstimate.method]);
      candidate.populationEstimate.by_traditional_authority.forEach((area) => {
        rows.push([
          "scenario",
          area.name,
          `${label} estimated additional population`,
          Math.round(area.population_estimate),
        ]);
      });
    } else if (candidate.populationError) {
      rows.push(["scenario", areaLabel, `${label} population estimate`, candidate.populationError]);
    } else if (candidate.loading) {
      rows.push(["scenario", "Dedza District", `${label} population estimate`, "Pending; export again after the estimate finishes"]);
    }
  });

  active.settlements.forEach((settlement) => {
    const school = nearestSchool(settlement);
    const facility = data.nearestFacilityBySettlement.get(settlement);
    rows.push([
      "settlement", areaLabel, "nearest-service distances", "walking km along roads and paths",
      settlement.properties.name,
      settlement.properties.adm3_name,
      school?.school.properties.name || "",
      school?.distance.toFixed(2) || "",
      facility?.facility.name || "",
      facility?.distance.toFixed(2) || "",
      school?.school.properties.level || "",
    ]);
  });

  const csv = `﻿${rows.map((row) => row.map(csvField).join(",")).join("\r\n")}`;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${areaLabel.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-planning-brief.csv`;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function setupExport() {
  document.getElementById("export-brief").addEventListener("click", exportPlanningBrief);
  document.getElementById("print-brief").addEventListener("click", () => window.print());
}
