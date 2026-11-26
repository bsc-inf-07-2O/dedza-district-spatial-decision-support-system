// Candidate sites on the map and the "Candidate results" cards beside it.
import { data } from "../data.js";
import { layers, map, scenarioColors } from "../map/map.js";
import { state } from "../state.js";
import { nearPlace, quickKm } from "../ui/format.js";

const scenarioStatus = document.getElementById("scenario-status");
const clearScenarioButton = document.getElementById("clear-scenario");
const scenarioResults = document.getElementById("scenario-results");
const scenarioCandidateList = document.getElementById("scenario-candidate-list");
const scenarioResultsSummary = document.getElementById("scenario-results-summary");
const scenarioResultsToggle = document.getElementById("toggle-scenario-results");

// Settlements within the candidate's radius before and after adding it.
export function getScenarioMetrics(active, candidate = state.scenarios[0]) {
  if (!candidate) return null;
  const nearestService = candidate.type === "school"
    ? data.nearestSchoolBySettlement
    : data.nearestFacilityBySettlement;
  let before = 0;
  let after = 0;
  const walking = candidate.populationEstimate?.reached_settlements
    ? new Map(candidate.populationEstimate.reached_settlements.map((row) => [row.index, row.walk_km]))
    : null;
  active.settlements.forEach((settlement) => {
    const currentDistance = nearestService.get(settlement)?.distance ?? Infinity;
    const candidateDistance = walking
      ? walking.get(data.settlementIndex.get(settlement)) ?? Infinity
      : turf.distance(settlement, candidate.point, { units: "kilometers" });
    if (currentDistance <= candidate.radius) before += 1;
    if (Math.min(currentDistance, candidateDistance) <= candidate.radius) after += 1;
  });
  return {
    before,
    after,
    gain: after - before,
    total: active.settlements.length,
    type: candidate.type,
    radius: candidate.radius,
  };
}

function nearestOf(features, coordinates) {
  let best = null;
  features.forEach((feature) => {
    const distance = quickKm(coordinates, feature.geometry.coordinates);
    if (!best || distance < best.distance) best = { feature, distance };
  });
  return best;
}

// Settlements newly within the 30- and 60-minute road-time bands.
function getScenarioRoadMetrics(active, candidate) {
  const roadAccess = candidate.populationEstimate?.road_time_access;
  if (!roadAccess?.available) {
    return {
      available: false,
      reason: roadAccess?.reason || "Road-time data is unavailable for this candidate.",
    };
  }

  const selectedSettlementKeys = new Set(active.settlements.map((settlement) => {
    const [lon, lat] = settlement.geometry.coordinates;
    return `${Number(lon).toFixed(6)},${Number(lat).toFixed(6)}`;
  }));
  const settlements = roadAccess.settlements.filter((settlement) => {
    const [lon, lat] = settlement.coordinates;
    return selectedSettlementKeys.has(`${Number(lon).toFixed(6)},${Number(lat).toFixed(6)}`);
  });
  const connected = settlements.filter((settlement) => settlement.road_accessible);
  const bands = roadAccess.time_bands_minutes.map((minutes) => {
    const reachableFromCandidate = connected.filter(
      (settlement) => settlement.candidate_time_min !== null
        && settlement.candidate_time_min <= minutes
    );
    const alreadyReachable = connected.filter(
      (settlement) => settlement.existing_time_min !== null
        && settlement.existing_time_min <= minutes
    );
    const newlyReachable = reachableFromCandidate.filter(
      (settlement) => settlement.existing_time_min === null
        || settlement.existing_time_min > minutes
    );
    return {
      minutes,
      candidateReachableCount: reachableFromCandidate.length,
      alreadyReachableCount: alreadyReachable.length,
      newlyReachable,
    };
  });
  return {
    available: true,
    bands,
    settlements,
    connectedCount: connected.length,
    excludedCount: settlements.length - connected.length,
    roadAccess,
  };
}

export function renderScenario(active) {
  const layer = layers.scenario;
  layer.clearLayers();
  scenarioCandidateList.replaceChildren();
  const hasScenarioResults = state.scenarios.length > 0 && !state.scenarioLayerHidden;
  scenarioResults.hidden = !hasScenarioResults;
  const mapContainer = map.getContainer();
  if (mapContainer.classList.contains("has-scenario-results") !== hasScenarioResults) {
    mapContainer.classList.toggle("has-scenario-results", hasScenarioResults);
    requestAnimationFrame(() => map.invalidateSize({ pan: false }));
  }
  scenarioResults.classList.toggle("collapsed", state.scenarioResultsCollapsed);
  scenarioResultsToggle.textContent = state.scenarioResultsCollapsed ? "Show" : "Hide";
  scenarioResultsToggle.setAttribute("aria-expanded", String(!state.scenarioResultsCollapsed));
  scenarioResultsToggle.setAttribute(
    "aria-label",
    state.scenarioResultsCollapsed ? "Expand candidate results" : "Minimize candidate results"
  );
  clearScenarioButton.disabled = state.scenarios.length === 0 && !state.placingScenario;
  if (!state.scenarios.length) {
    scenarioResultsSummary.textContent = "";
    scenarioStatus.textContent = state.placingScenario
      ? "Click inside the selected district or TA to add a candidate site."
      : "No candidate sites yet. Add a site on the map to compare its potential population reach.";
    return;
  }

  // Rank estimated candidates: physically suitable sites first, then by the
  // residents newly brought within reach by 2030 (WorldPop projection).
  const reach2030 = (candidate) =>
    candidate.populationEstimate.additional_population_2030 ?? candidate.populationEstimate.additional_population_estimate;
  const isSuitable = (candidate) => candidate.populationEstimate.site_suitability?.suitable !== false;
  const rankedCandidates = state.scenarios
    .filter((candidate) => candidate.populationEstimate)
    .sort((a, b) => (isSuitable(b) - isSuitable(a)) || (reach2030(b) - reach2030(a)));
  const rankById = new Map(rankedCandidates.map((candidate, rank) => [candidate.id, rank + 1]));
  const showRanks = rankedCandidates.length > 1;

  state.scenarios.forEach((candidate, index) => {
    const [lon, lat] = candidate.point.geometry.coordinates;
    const metrics = getScenarioMetrics(active, candidate);
    const color = scenarioColors[index % scenarioColors.length];
    const label = candidate.type === "school" ? "School" : "Health centre";
    // The shaded area is the real walking reach along roads and paths. Until
    // it is calculated, a faint straight-line circle marks the radius.
    const reachGeometry = candidate.populationEstimate?.walking_reach_geojson;
    if (reachGeometry) {
      L.geoJSON(reachGeometry, {
        interactive: false,
        style: { color, weight: 2, fillColor: color, fillOpacity: 0.14 },
      }).addTo(layer);
    } else {
      L.circle([lat, lon], {
        radius: candidate.radius * 1000,
        interactive: false,
        color,
        weight: 1,
        opacity: 0.6,
        fill: false,
        dashArray: "2 6",
      }).addTo(layer);
    }
    const marker = L.circleMarker([lat, lon], {
      radius: 8,
      color: "#10251f",
      weight: 2,
      fillColor: color,
      fillOpacity: 1,
    }).bindTooltip(String(candidate.id), {
      permanent: true,
      direction: "center",
      className: "scenario-marker-label",
    }).addTo(layer);
    candidate.marker = marker;

    const popup = document.createElement("div");
    const popupTitle = document.createElement("strong");
    popupTitle.textContent = `Candidate ${candidate.id} · ${label}`;
    const popupRadius = document.createElement("div");
    popupRadius.textContent = reachGeometry
      ? `Shaded: within ${candidate.radius} km walking distance along roads and paths`
      : `${candidate.radius} km walking radius (calculating the reachable area…)`;
    const occupancy = document.createElement("div");
    occupancy.textContent = candidate.siteScreening.buildings_available
      ? "No detected footprint contains the candidate point; this does not confirm the parcel is vacant."
      : "Building footprints are unavailable; occupancy was not screened.";
    popup.append(popupTitle, popupRadius, occupancy);
    if (candidate.populationEstimate) {
      const population = document.createElement("div");
      population.textContent = `${Math.round(candidate.populationEstimate.additional_population_estimate).toLocaleString()} estimated additional residents`;
      popup.append(population);
    }
    marker.bindPopup(popup);

    const card = document.createElement("li");
    card.className = "scenario-candidate";
    card.style.setProperty("--scenario-color", color);
    const focus = document.createElement("button");
    focus.type = "button";
    focus.className = "scenario-candidate-title";
    focus.textContent = `Candidate ${candidate.id} · ${label}`;
    focus.addEventListener("click", () => {
      map.setView([lat, lon], 12);
      candidate.marker?.openPopup();
    });
    const rank = rankById.get(candidate.id);
    if (showRanks && rank) {
      const badge = document.createElement("span");
      badge.className = `scenario-rank${rank === 1 ? " best" : ""}`;
      badge.textContent = rank === 1 ? "Best reach" : `#${rank}`;
      focus.appendChild(badge);
    }
    card.appendChild(focus);
    if (candidate.suggestion) {
      const origin = document.createElement("div");
      origin.className = "scenario-origin";
      origin.textContent = `Suggested by the system, ${nearPlace(candidate.suggestion.name)}`;
      card.appendChild(origin);
    }
    if (showRanks && rank === 1) {
      const decision = document.createElement("p");
      decision.className = "scenario-decision";
      const runnerUp = rankedCandidates[1];
      const margin = Math.round(reach2030(candidate) - reach2030(runnerUp));
      decision.textContent = !isSuitable(candidate)
        ? `None of the ${rankedCandidates.length} sites passes the site checks; move them out of the flagged areas.`
        : !isSuitable(runnerUp)
          ? `Recommended of the ${rankedCandidates.length} sites compared: the only site without a blocking flood, road, slope or land-cover issue.`
          : `Recommended of the ${rankedCandidates.length} sites compared: reaches ${margin.toLocaleString()} more people by 2030 than Candidate ${runnerUp.id}.`;
      card.appendChild(decision);
    }

    // Duplication check: is an existing service already close to this site?
    const existingPool = candidate.type === "school" ? data.schools.features : data.registry.features;
    const closest = nearestOf(existingPool, candidate.point.geometry.coordinates);
    if (closest) {
      const nearby = document.createElement("p");
      nearby.className = "scenario-screening-note";
      const what = candidate.type === "school" ? "school" : "operational facility (MHFR registry, any owner)";
      const owner = candidate.type === "school" ? "" : closest.feature.properties.ownership ? ` · ${closest.feature.properties.ownership}` : "";
      nearby.textContent = `Nearest existing ${what}: ${closest.feature.properties.name}${owner}, ${closest.distance.toFixed(1)} km.`
        + (closest.distance < candidate.radius / 2 ? " Close enough to risk duplicating it; consider upgrading that site instead." : "");
      card.appendChild(nearby);
    }

    const siteScreening = document.createElement("p");
    siteScreening.className = "scenario-screening-note";
    siteScreening.textContent = candidate.siteScreening.buildings_available
      ? "No detected building footprint contains this point. This does not confirm the parcel is vacant."
      : "Building footprints are not installed; occupancy was not screened.";
    card.appendChild(siteScreening);

    if (candidate.loading) {
      const loading = document.createElement("p");
      loading.textContent = "Estimating population and road-time access…";
      card.appendChild(loading);
    } else if (candidate.populationError) {
      const error = document.createElement("p");
      error.className = "scenario-error";
      error.textContent = `Population estimate unavailable: ${candidate.populationError}`;
      card.appendChild(error);
    } else if (candidate.populationEstimate) {
      appendEstimate(card, active, candidate, metrics);
    }
    scenarioCandidateList.appendChild(card);
  });

  renderScenarioSummary(active);
}

// The estimate part of a candidate card: population, site checks, road time, TAs.
function appendEstimate(card, active, candidate, metrics) {
  const result = candidate.populationEstimate;
  const existingServiceLabel = candidate.type === "school" ? "school" : "baseline health facility";
  const gain = document.createElement("strong");
  gain.textContent = `${Math.round(result.additional_population_estimate).toLocaleString()} estimated additional residents in Dedza`;
  const detail = document.createElement("p");
  detail.textContent = `Within ${candidate.radius} km: ${Math.round(result.population_within_radius).toLocaleString()} estimated residents; ${Math.round(result.already_within_existing_service_radius).toLocaleString()} are already within an existing ${existingServiceLabel} zone.`;
  card.append(gain, detail);
  if (result.additional_population_2030 != null) {
    const future = document.createElement("p");
    future.className = "scenario-future";
    const added = result.additional_population_2030 - result.additional_population_estimate;
    future.textContent = `By 2030 (WorldPop projection): ${Math.round(result.additional_population_2030).toLocaleString()} additional residents, ${added >= 0 ? "+" : ""}${Math.round(added).toLocaleString()} more than today.`;
    card.appendChild(future);
  }
  if (result.site_suitability) {
    const checks = document.createElement("div");
    checks.className = "site-checks";
    const heading = document.createElement("strong");
    heading.textContent = result.site_suitability.suitable
      ? "Site checks: no blocking issue"
      : "Site checks: not suitable as placed";
    const list = document.createElement("ul");
    result.site_suitability.checks.forEach((check) => {
      const item = document.createElement("li");
      item.className = `site-check ${check.status}`;
      item.textContent = `${check.label}: ${check.detail}`;
      list.appendChild(item);
    });
    checks.append(heading, list);
    card.appendChild(checks);
  }

  const settlements = document.createElement("p");
  settlements.textContent = `Mapped settlements newly within the radius in the selected geography: +${metrics.gain} of ${metrics.total}.`;
  card.appendChild(settlements);

  const roadMetrics = getScenarioRoadMetrics(active, candidate);
  const roadSummary = document.createElement("p");
  roadSummary.className = "scenario-road-summary";
  const capacityNote = document.createElement("p");
  capacityNote.className = "scenario-capacity-note";
  capacityNote.textContent = "Capacity: unknown. Staffing, beds, and service-volume data are unavailable, so this comparison does not estimate facility capacity.";
  let roadDetails = null;
  if (!roadMetrics.available) {
    roadSummary.textContent = `Road-time access unavailable: ${roadMetrics.reason}`;
  } else {
    const bandSummaries = roadMetrics.bands.map(
      (band) => `≤${band.minutes} min +${band.newlyReachable.length}`
    );
    roadSummary.textContent = `New mapped settlements within road-time bands vs existing ${existingServiceLabel}: ${bandSummaries.join("; ")}.`;
    const details = document.createElement("details");
    details.className = "scenario-road-details";
    roadDetails = details;
    const detailsSummary = document.createElement("summary");
    detailsSummary.textContent = "Settlement access details and assumptions";
    details.appendChild(detailsSummary);
    const explanation = document.createElement("p");
    explanation.textContent = `Times compare travel from mapped settlements to this candidate and the nearest existing ${existingServiceLabel} along the mapped road network.`;
    details.appendChild(explanation);
    roadMetrics.bands.forEach((band) => {
      const bandLabel = document.createElement("strong");
      bandLabel.textContent = `${band.minutes}-minute band: ${band.candidateReachableCount} settlements reachable from this candidate; ${band.newlyReachable.length} newly within reach.`;
      details.appendChild(bandLabel);
      if (band.newlyReachable.length) {
        const names = document.createElement("ul");
        names.className = "scenario-road-settlements";
        band.newlyReachable.forEach((settlement) => {
          const item = document.createElement("li");
          item.textContent = `${settlement.name} (${Math.round(settlement.candidate_time_min)} min)`;
          names.appendChild(item);
        });
        details.appendChild(names);
      } else {
        const none = document.createElement("p");
        none.textContent = "No settlements cross this time threshold compared with existing facilities.";
        details.appendChild(none);
      }
    });
    if (roadMetrics.excludedCount) {
      const excluded = document.createElement("p");
      excluded.textContent = `${roadMetrics.excludedCount} of ${roadMetrics.settlements.length} mapped settlements are more than 1 km from a mapped road and are excluded from road-time counts.`;
      details.appendChild(excluded);
    }
    const speeds = Object.entries(roadMetrics.roadAccess.speed_assumptions_kmh)
      .map(([roadClass, speed]) => `${roadClass}: ${speed} km/h`)
      .join("; ");
    const assumptions = document.createElement("p");
    assumptions.textContent = `Indicative motorcycle speeds (${speeds}). Straight-line connector time is excluded; traffic, road condition, stops, and season are not modeled.`;
    details.appendChild(assumptions);
  }
  card.append(roadSummary, capacityNote);
  if (roadDetails) {
    card.appendChild(roadDetails);
  }

  const byTA = document.createElement("ul");
  byTA.className = "scenario-ta-list";
  result.by_traditional_authority.forEach((area) => {
    const item = document.createElement("li");
    item.textContent = `${area.name}: ${Math.round(area.population_estimate).toLocaleString()}`;
    byTA.appendChild(item);
  });
  if (result.by_traditional_authority.length) {
    card.appendChild(byTA);
  } else {
    const none = document.createElement("p");
    none.textContent = "No newly reached residents are estimated within this radius.";
    card.appendChild(none);
  }
}

// Header line of the results panel and the status line in the New sites tool.
function renderScenarioSummary(active) {
  const pending = state.scenarios.filter((candidate) => candidate.loading).length;
  const failed = state.scenarios.filter((candidate) => candidate.populationError);
  if (pending) {
    scenarioResultsSummary.textContent = `${state.scenarios.length} candidate site${state.scenarios.length === 1 ? "" : "s"} · estimating population…`;
    scenarioStatus.textContent = `Comparing ${state.scenarios.length} candidate site${state.scenarios.length === 1 ? "" : "s"}; estimating population reach for ${pending}.`;
  } else if (failed.length) {
    scenarioResultsSummary.textContent = `${state.scenarios.length} candidate site${state.scenarios.length === 1 ? "" : "s"} · some estimates unavailable`;
    scenarioStatus.textContent = `Population estimate unavailable for ${failed.map((candidate) => `Candidate ${candidate.id}`).join(", ")}. See the candidate cards for details.`;
  } else {
    const highestPopulation = [...state.scenarios].sort(
      (a, b) => b.populationEstimate.additional_population_estimate - a.populationEstimate.additional_population_estimate
    )[0];
    const roadCandidates = state.scenarios
      .map((candidate) => ({
        candidate,
        metrics: getScenarioRoadMetrics(active, candidate),
      }))
      .filter(({ metrics }) => metrics.available);
    const highestThirtyMinuteGain = roadCandidates
      .map(({ candidate, metrics }) => ({
        candidate,
        gain: metrics.bands.find((band) => band.minutes === 30)?.newlyReachable.length ?? 0,
      }))
      .sort((a, b) => b.gain - a.gain)[0];
    const populationGain = Math.round(
      highestPopulation.populationEstimate.additional_population_estimate
    ).toLocaleString();
    const siteCount = state.scenarios.length;
    const populationSummary = siteCount === 1
      ? `Pop +${populationGain} people`
      : `Highest pop #${highestPopulation.id}: +${populationGain} people`;
    const roadSummary = highestThirtyMinuteGain
      ? siteCount === 1
        ? `New settlements ≤30 min: ${highestThirtyMinuteGain.gain}`
        : `Most new settlements ≤30 min #${highestThirtyMinuteGain.candidate.id}: +${highestThirtyMinuteGain.gain}`
      : "";
    scenarioResultsSummary.textContent = `${siteCount} site${siteCount === 1 ? "" : "s"} · ${populationSummary}${roadSummary ? ` · ${roadSummary}` : ""} · Capacity unknown`;
    scenarioStatus.textContent = `Compared ${state.scenarios.length} candidate site${state.scenarios.length === 1 ? "" : "s"}. Population gain and road-time settlement reach are separate signals; facility capacity is not assessed.`;
  }
}
