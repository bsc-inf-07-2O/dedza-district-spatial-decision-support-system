// Insights drawer content: the Briefing (KPIs, recommended actions, signals),
// the Settlement access table, and the coverage bars in the tool panel.
import { data, nearestSchool } from "../data.js";
import { map } from "../map/map.js";
import { state } from "../state.js";
import { escapeHtml, formatMinutes, formatShare, growthText, nearPlace, plural, schoolNoun, travelTimeTone } from "../ui/format.js";
import { cachedSuggestion } from "./suggestions.js";
import { taRanking } from "./ta.js";

export function renderBriefing(active, metrics) {
  const { roadAccessKm } = data;
  const { schoolRadiusKm } = state;
  const areaName = state.name || "Dedza District";
  const population = data.populationByArea.get(areaName) || {};
  const populationTotal = population.total || 0;
  const populationUnserved = population.unserved || 0;
  const populationShare = populationTotal ? populationUnserved / populationTotal * 100 : 0;
  const populationLead = populationTotal
    ? `WorldPop 2025 estimates ${Math.round(populationTotal).toLocaleString()} people in ${areaName}; ${Math.round(populationUnserved).toLocaleString()} (${populationShare.toFixed(1)}%) fall in baseline health-grid cells more than 8 km from a facility.`
    : `No WorldPop summary is available for ${areaName}.`;

  // Population-weighted indicators over the cells in the selected area.
  const cellTotals = active.cells.reduce((totals, cell) => {
    const p = cell.properties;
    const people = Number(p.population) || 0;
    totals.population += people;
    totals.population2030 += Number(p.population_2030) || 0;
    if (p.travel_min != null) {
      totals.travelPeople += people;
      totals.travelWeighted += people * Number(p.travel_min);
    }
    totals.nearRoad += people * (Number(p.road_access_share) || 0);
    totals.flooded += people * (Number(p.flood_pop_share) || 0);
    return totals;
  }, { population: 0, population2030: 0, travelPeople: 0, travelWeighted: 0, nearRoad: 0, flooded: 0 });
  const growthPct = cellTotals.population
    ? (cellTotals.population2030 / cellTotals.population - 1) * 100
    : 0;
  const averageTravel = cellTotals.travelPeople ? cellTotals.travelWeighted / cellTotals.travelPeople : null;

  document.getElementById("briefing-title").textContent = `${areaName} · Public-service access`;
  document.getElementById("briefing-lead").textContent = `${populationLead} WorldPop projects ${Math.round(cellTotals.population2030).toLocaleString()} people by 2030 (+${growthPct.toFixed(1)}%). ${metrics.healthGaps} of ${active.settlements.length} mapped settlements are more than a 5 km walk from a baseline health facility.`;
  document.getElementById("briefing-kpis").innerHTML = [
    [Math.round(populationTotal).toLocaleString(), "WorldPop 2025 estimate"],
    [`${populationShare.toFixed(1)}%`, "Population in unserved health cells"],
    [`+${Math.round(cellTotals.population2030 - cellTotals.population).toLocaleString()}`, "More residents by 2030 (WorldPop projection)"],
    [formatMinutes(averageTravel), "Average road travel time to care"],
    [formatShare(cellTotals.population ? cellTotals.nearRoad / cellTotals.population : 0), `Residents within ${roadAccessKm} km of a classified road`],
    [`${metrics.healthGaps}/${active.settlements.length}`, "Settlements > 5 km walk from baseline health"],
    [`${metrics.schoolReach}/${active.settlements.length}`, `Settlements within ${schoolRadiusKm} km of a ${schoolNoun(state.schoolLevel)}`],
  ].map(([value, label]) => `<div class="briefing-kpi"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`).join("");
  const signals = [
    `<strong>Health access:</strong> ${metrics.unservedCells} grid cells (${metrics.unservedArea.toFixed(0)} km²) are beyond the 8 km baseline threshold.`,
    `<strong>Education proximity:</strong> ${metrics.schoolReach} of ${active.settlements.length} mapped settlements are within ${schoolRadiusKm} km walking distance of a ${schoolNoun(state.schoolLevel)}.`,
    `<strong>Travel and roads:</strong> residents average ${escapeHtml(formatMinutes(averageTravel))} by road to the nearest facility; ${formatShare(cellTotals.population ? cellTotals.nearRoad / cellTotals.population : 0)} live within ${roadAccessKm} km of a classified road.`,
    `<strong>Flood exposure:</strong> about ${Math.round(cellTotals.flooded).toLocaleString()} residents live in the 1-in-100-year river flood zone (JRC GloFAS).`,
    `<strong>Facility sources:</strong> ${active.facilities.length} baseline facilities inform the access grid; ${active.registry.features.length} operational facilities from the official MHFR registry are shown separately.`,
  ];
  document.getElementById("briefing-signals").innerHTML = signals.map((signal) => `<li>${signal}</li>`).join("");
  document.getElementById("briefing-actions").innerHTML = buildRecommendedActions(active)
    .map((action) => `<li>${action.html}</li>`).join("");

  const tbody = document.getElementById("briefing-priorities");
  tbody.replaceChildren();
  metrics.priorityCells.forEach((cell) => {
    const p = cell.properties;
    const point = turf.pointOnFeature(cell).geometry.coordinates;
    const row = document.createElement("tr");
    row.className = "rec-row";
    row.innerHTML = `<td>${escapeHtml(p.cell_id)}</td><td>${escapeHtml(growthText(p))}</td><td>${escapeHtml(p.dist_km)} km</td><td><span class="tag ${travelTimeTone(p.travel_min)}">${escapeHtml(formatMinutes(p.travel_min))}</span></td>`;
    row.addEventListener("click", () => map.setView([point[1], point[0]], 12));
    tbody.appendChild(row);
  });
}

// Concrete, data-backed actions for the selected area. Each item carries
// HTML for the briefing and plain text for the CSV export. The buttons are
// handled in static/js/features/popup-actions.js (setupBriefingActions).
export function buildRecommendedActions(active) {
  const { schoolRadiusKm } = state;
  const actions = [];
  const areaName = state.name || "Dedza District";
  const fmt = (value) => Math.round(value).toLocaleString();
  const button = (label, attrs) => `<button type="button" class="inline-action" ${attrs}>${escapeHtml(label)}</button>`;

  const ranked = taRanking().filter((row) => row.hasPopulationData);
  if (!state.name && ranked.length) {
    const top = ranked[0];
    const share = top.populationTotal ? top.populationUnserved / top.populationTotal * 100 : 0;
    const text = `Prioritise ${top.name}: about ${fmt(top.populationUnserved)} people (${share.toFixed(0)}% of the TA) live more than 8 km from a health facility, the largest gap in the district.`;
    actions.push({ text, html: `<strong>Prioritise ${escapeHtml(top.name)}:</strong> about ${fmt(top.populationUnserved)} people (${share.toFixed(0)}% of the TA) live more than 8 km from a health facility, the largest gap in the district. ${button("Open TA", `data-action="select-ta" data-value="${escapeHtml(top.name)}"`)}` });
  } else if (state.name) {
    const position = ranked.findIndex((row) => row.name === state.name);
    if (position >= 0) {
      const row = ranked[position];
      const text = `${areaName} ranks #${position + 1} of ${ranked.length} populated areas in Dedza for people more than 8 km from a health facility (about ${fmt(row.populationUnserved)}).`;
      actions.push({ text, html: `<strong>Priority level:</strong> ${escapeHtml(text)}` });
    }
  }

  const healthSite = cachedSuggestion("health", 5);
  if (healthSite === undefined) {
    actions.push({ text: "Calculating the best health-centre location…", html: "<strong>Best health-centre location:</strong> calculating walking reach…" });
  } else if (healthSite) {
    const text = `Plan a new health centre ${nearPlace(healthSite.name)}: within a 5 km walk it would serve about ${fmt(healthSite.people)} people by 2030 in ${plural(healthSite.settlement_count, "settlement")} that are now more than a 5 km walk from care. The site passes the flood, road, slope and land-cover checks.`;
    actions.push({ text, html: `<strong>Best health-centre location:</strong> ${escapeHtml(nearPlace(healthSite.name))}. Within a 5 km walk it would serve about ${fmt(healthSite.people)} people by 2030 (WorldPop projection) in ${plural(healthSite.settlement_count, "settlement")} that are now more than a 5 km walk from care, and the site passes the flood, road, slope and land-cover checks. ${button("Compare top 3 sites", 'data-action="suggest" data-value="health" data-radius="5"')}` });
  }

  const remote = active.settlements
    .map((settlement) => ({ settlement, distance: data.nearestFacilityBySettlement.get(settlement)?.distance ?? Infinity }))
    .filter(({ distance }) => distance > 8)
    .sort((a, b) => (Number(b.settlement.properties.population) || 0) - (Number(a.settlement.properties.population) || 0))
    .slice(0, 3);
  if (remote.length) {
    const list = remote.map(({ settlement, distance }) => `${settlement.properties.name} (${fmt(Number(settlement.properties.population) || 0)} people, ${distance.toFixed(1)} km)`);
    const text = `Schedule outreach or mobile clinics for the largest settlements beyond 8 km: ${list.join("; ")}.`;
    const htmlList = remote.map(({ settlement, distance }) => {
      const [lon, lat] = settlement.geometry.coordinates;
      return `${button(settlement.properties.name, `data-action="zoom" data-value="${lon},${lat}" title="Zoom to settlement"`)} ${fmt(Number(settlement.properties.population) || 0)} people, ${distance.toFixed(1)} km`;
    });
    actions.push({ text, html: `<strong>Outreach until a facility is built:</strong> the largest settlements beyond 8 km are ${htmlList.join("; ")}.` });
  }

  // School advice follows the School level filter (all, primary or secondary).
  const { schoolLevel } = state;
  const noun = schoolNoun(schoolLevel);
  const schoolSite = cachedSuggestion("school", schoolRadiusKm, schoolLevel);
  const schoolGapPeople = active.settlements
    .filter((settlement) => (nearestSchool(settlement)?.distance ?? Infinity) > schoolRadiusKm)
    .reduce((sum, settlement) => sum + (Number(settlement.properties.population) || 0), 0);
  if (schoolSite && schoolGapPeople >= 1000) {
    const text = `Assess a new ${noun} ${nearPlace(schoolSite.name)}: it would bring about ${fmt(schoolSite.people)} people (2030) in ${plural(schoolSite.settlement_count, "settlement")} within a ${schoolRadiusKm} km walk of a ${noun}.`;
    actions.push({ text, html: `<strong>Best ${escapeHtml(noun)} location:</strong> ${escapeHtml(nearPlace(schoolSite.name))}, bringing about ${fmt(schoolSite.people)} people (2030) in ${plural(schoolSite.settlement_count, "settlement")} within a ${schoolRadiusKm} km walk of a ${escapeHtml(noun)}. ${button("Compare top 3 sites", `data-action="suggest" data-value="school" data-radius="${schoolRadiusKm}"`)}` });
  } else if (schoolSite === undefined && schoolGapPeople >= 1000) {
    actions.push({ text: `Calculating the best ${noun} location…`, html: `<strong>Best ${escapeHtml(noun)} location:</strong> calculating walking reach…` });
  } else {
    const text = schoolGapPeople > 0
      ? `Only about ${fmt(schoolGapPeople)} people in mapped settlements live more than a ${schoolRadiusKm} km walk from a ${noun}, so new ${noun} sites are not the main gap here; direct education funds to classrooms, teachers and quality. Lower the School access radius to test a stricter standard.`
      : `Every mapped settlement is within a ${schoolRadiusKm} km walk of a ${noun}; direct education funds to classrooms, teachers and quality rather than new sites.`;
    actions.push({ text, html: `<strong>Schools:</strong> ${escapeHtml(text)}` });
  }

  const verify = "Verify land, staffing and community needs on the ground before committing funds; these are screening results.";
  actions.push({ text: verify, html: `<em>${escapeHtml(verify)}</em>` });
  return actions;
}

// Schools tool: share of settlements within 1, 3, 5 and 8 km of a school.
export function renderSchoolProfile(areaSettlements) {
  const thresholds = [1, 3, 5, 8];
  const profile = document.getElementById("school-thresholds");
  profile.replaceChildren();
  thresholds.forEach((threshold) => {
    const covered = areaSettlements.filter(
      (settlement) => (nearestSchool(settlement)?.distance ?? Infinity) <= threshold
    ).length;
    const percent = areaSettlements.length ? covered / areaSettlements.length * 100 : 0;
    const item = document.createElement("div");
    item.className = "threshold-row";
    item.innerHTML = `
        <span>${threshold} km</span>
        <span class="threshold-track"><span style="width:${percent}%"></span></span>
        <b>${covered}/${areaSettlements.length}</b>`;
    profile.appendChild(item);
  });
}

// Overview tool: WorldPop population by baseline health-access class.
export function renderPopulationProfile(population) {
  const profile = document.getElementById("population-access-profile");
  profile.replaceChildren();
  const total = population.total || 0;
  ["served", "underserved", "unserved"].forEach((category) => {
    const count = population[category] || 0;
    const percent = total ? count / total * 100 : 0;
    const row = document.createElement("div");
    row.className = "population-row";
    row.innerHTML = `
        <span>${category === "served" ? "≤ 5 km" : category === "underserved" ? "5–8 km" : "> 8 km"}</span>
        <span class="threshold-track"><span class="${category}" style="width:${percent}%"></span></span>
        <strong>${Math.round(count).toLocaleString()} · ${percent.toFixed(1)}%</strong>`;
    profile.appendChild(row);
  });
}

// "Settlement access" tab: most remote from health care first.
export function renderSettlementAccess(areaSettlements) {
  const tbody = document.getElementById("settlement-access-rows");
  tbody.replaceChildren();
  const ordered = [...areaSettlements].sort((a, b) =>
    (data.nearestFacilityBySettlement.get(b)?.distance ?? Infinity) - (data.nearestFacilityBySettlement.get(a)?.distance ?? Infinity));
  const noun = schoolNoun(state.schoolLevel);
  document.getElementById("settlement-school-heading").textContent = `Nearest ${noun}`;
  document.getElementById("settlement-school-distance-heading").textContent =
    `${noun[0].toUpperCase()}${noun.slice(1)} distance`;
  ordered.forEach((settlement) => {
    const school = nearestSchool(settlement);
    const facility = data.nearestFacilityBySettlement.get(settlement);
    const [lon, lat] = settlement.geometry.coordinates;
    const population = Number(settlement.properties.population) || 0;
    const row = document.createElement("tr");
    row.className = "rec-row";
    row.innerHTML = `
        <td>${escapeHtml(settlement.properties.name)}</td>
        <td>${escapeHtml(settlement.properties.adm3_name)}</td>
        <td>${population ? Math.round(population).toLocaleString() : "—"}</td>
        <td>${escapeHtml(school?.school.properties.name || `No mapped ${noun}`)}</td>
        <td>${school ? `${school.distance.toFixed(1)} km` : "—"}</td>
        <td>${escapeHtml(facility?.facility.name || "No mapped facility")}</td>
        <td>${facility ? `${facility.distance.toFixed(1)} km` : "—"}</td>`;
    row.addEventListener("click", () => map.setView([lat, lon], 13));
    tbody.appendChild(row);
  });
}
