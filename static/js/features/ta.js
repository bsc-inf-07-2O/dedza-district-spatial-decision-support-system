// Traditional Authority ranking: the "TA priorities" table and the TA dropdown.
import { data } from "../data.js";
import { state } from "../state.js";
import { escapeHtml } from "../ui/format.js";
import { selectTA } from "./area.js";

let rankingCache = null;

// TAs ranked by population in unserved health cells (the ranking never changes,
// so it is computed once).
export function taRanking() {
  rankingCache ||= buildTAComparison();
  return rankingCache;
}

export function buildTAComparison() {
  const { cells, settlements, facilities, registry, schools } = data;
  const cellCenters = cells.features.map((cell) => ({
    cell,
    center: turf.centroid(cell),
  }));
  return data.taNames.map((name) => {
    const areas = data.taByName.get(name.toLocaleLowerCase());
    const includes = (point) => areas.some((area) => turf.booleanPointInPolygon(point, area));
    const taSettlements = settlements.features.filter((feature) => includes(feature));
    const schoolReach = taSettlements.filter(
      (feature) => data.nearestSchoolBySettlement.get(feature)?.distance <= 1
    ).length;
    const healthGaps = taSettlements.filter(
      (feature) => (data.nearestFacilityBySettlement.get(feature)?.distance ?? Infinity) > 5
    ).length;
    const unservedCells = cellCenters.filter(
      ({ cell, center }) => cell.properties.coverage === "unserved" && includes(center)
    ).length;
    const population = data.populationByArea.get(name) || {};
    const populationTotal = Number(population.total) || 0;
    const populationUnserved = Number(population.unserved) || 0;
    return {
      name,
      facilities: facilities.filter((facility) => includes(turf.point([facility.lon, facility.lat]))).length,
      registryFacilities: registry.features.filter((feature) => includes(feature)).length,
      schools: schools.features.filter((feature) => includes(feature)).length,
      settlements: taSettlements.length,
      schoolReach,
      healthGaps,
      unservedCells,
      hasPopulationData: populationTotal > 0,
      populationTotal,
      populationUnserved,
    };
  }).sort((a, b) => {
    if (a.hasPopulationData !== b.hasPopulationData) return a.hasPopulationData ? -1 : 1;
    const populationDifference = b.populationUnserved - a.populationUnserved;
    if (populationDifference) return populationDifference;
    const shareA = a.populationTotal ? a.populationUnserved / a.populationTotal : 0;
    const shareB = b.populationTotal ? b.populationUnserved / b.populationTotal : 0;
    return shareB - shareA || b.unservedCells - a.unservedCells || a.name.localeCompare(b.name);
  });
}

export function renderTAComparison(rows) {
  const tbody = document.getElementById("ta-comparison-rows");
  tbody.replaceChildren();
  let rank = 0;
  rows.forEach((metrics) => {
    const priorityRank = metrics.hasPopulationData ? ++rank : null;
    const row = document.createElement("tr");
    row.className = "rec-row";
    row.innerHTML = `
        <td class="ta-rank-cell">${priorityRank ? `<span class="ta-rank">${priorityRank}</span>` : "—"}</td>
        <td>${escapeHtml(metrics.name)}</td>
        <td>${metrics.facilities}</td>
        <td>${metrics.registryFacilities}</td>
        <td>${metrics.schools}</td>
        <td>${metrics.settlements}</td>
        <td>${metrics.schoolReach}/${metrics.settlements}</td>
        <td>${metrics.healthGaps}/${metrics.settlements}</td>
        <td>${metrics.unservedCells}</td>
        <td>${metrics.hasPopulationData ? Math.round(metrics.populationUnserved).toLocaleString() : "—"}</td>
        <td>${metrics.populationTotal ? `${(metrics.populationUnserved / metrics.populationTotal * 100).toFixed(1)}%` : "—"}</td>`;
    row.addEventListener("click", () => selectTA(metrics.name));
    tbody.appendChild(row);
  });
}

// TA dropdown options, alphabetical, each with its screening rank and gap size.
export function populateTASelect(rows) {
  const taSelect = document.getElementById("ta-select");
  const rankByName = new Map();
  let rank = 0;
  rows.forEach((row) => { if (row.hasPopulationData) rankByName.set(row.name, ++rank); });
  const rowByName = new Map(rows.map((row) => [row.name, row]));
  const fragment = document.createDocumentFragment();
  data.taNames.forEach((name) => {
    const row = rowByName.get(name);
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    const count = row?.facilities ?? 0;
    const facilityText = `${count} ${count === 1 ? "facility" : "facilities"}`;
    option.dataset.meta = row?.hasPopulationData
      ? `Priority #${rankByName.get(name)} · ${Math.round(row.populationUnserved).toLocaleString()} people > 8 km · ${facilityText}`
      : `${facilityText} · no population summary`;
    fragment.appendChild(option);
  });
  taSelect.querySelectorAll("option:not([value=''])").forEach((option) => option.remove());
  taSelect.appendChild(fragment);
  taSelect.value = state.name || "";
}
