// The 3 km health-access grid: coverage colours, cell popups and the
// numbered top-12 priority cells (map markers + "Priority cells" table).
import { data } from "../data.js";
import { coverageColor, layers, map } from "../map/map.js";
import { state } from "../state.js";
import { escapeHtml, formatMinutes, formatShare, growthText, travelTimeTone } from "../ui/format.js";
import { POPUP_OPTIONS, popupCard } from "../ui/popup.js";

// Score under the current priority weights (falls back to the stored baseline).
export const cellScore = (properties) =>
  state.priorityScores.get(Number(properties.cell_id)) ?? properties.priority_score;

// One-line planning suggestion for a cell, from its own figures.
export function planningPrompt(properties) {
  const { roadAccessKm } = data;
  const prompts = [];
  if (properties.dist_km > 8) prompts.push("Assess a new health post or health centre");
  else if (properties.dist_km > 5) prompts.push("Extend outreach or mobile-clinic coverage");
  if (Number(properties.growth) >= 1) {
    prompts.push(`+${Math.round(properties.growth).toLocaleString()} residents expected by 2030`);
  }
  if (Number(properties.population) > 0 && properties.road_access_share < 0.5) {
    prompts.push(`only ${formatShare(properties.road_access_share)} of residents within ${roadAccessKm} km of a classified road`);
  }
  if (properties.flood_area_share >= 0.05) {
    prompts.push(`${formatShare(properties.flood_area_share)} of the cell in the 1-in-100-year flood zone: site outside it`);
  }
  if (properties.travel_min != null && properties.travel_min > 60) {
    prompts.push(`${formatMinutes(properties.travel_min)} by road to the nearest facility`);
  }
  return prompts.length ? prompts.join("; ") : "Review service quality and staffing";
}

export function renderCellCoverage(activeCells) {
  const { roadAccessKm, baselineWeightsLabel } = data;
  layers.cells.clearLayers();
  L.geoJSON(activeCells, {
    style: (feature) => {
      const coverage =
        state.gapCoverage.get(Number(feature.properties.cell_id)) ||
        feature.properties.coverage;
      return {
        color: "#0f1c1a",
        weight: 0.4,
        fillColor: coverageColor[coverage] || "#999",
        fillOpacity: 0.45,
      };
    },
    onEachFeature: (feature, layer) => {
      const p = feature.properties;
      const coverageOf = () => state.gapCoverage.get(Number(p.cell_id)) || p.coverage;
      const distText = p.dist_km == null ? "unavailable" : `${p.dist_km} km`;
      // Built on open so it always reflects the current slider settings.
      layer.bindPopup(() => {
        const score = Number(cellScore(p));
        const center = turf.pointOnFeature(feature).geometry.coordinates;
        return popupCard({
          kicker: `Grid cell ${p.cell_id} · ${Number(p.area_km2).toFixed(1)} km²`,
          title: "Health access",
          badge: { text: coverageOf(), tone: coverageOf() },
          rows: [
            ["Residents 2025 (WorldPop)", Number(p.population || 0).toLocaleString()],
            ["Projected 2030", growthText(p)],
            ["Walking distance to nearest facility", distText],
            ["Motorcycle travel time to care", formatMinutes(p.travel_min)],
            [`Residents within ${roadAccessKm} km of a classified road`, formatShare(p.road_access_share)],
            ["Area in 1-in-100-year flood zone", formatShare(p.flood_area_share)],
            [`Baseline score (${baselineWeightsLabel})`, p.priority_score],
          ],
          meter: { label: "Priority score (current weights)", percent: score / 2 * 100, text: score.toFixed(3) },
          note: planningPrompt(p),
          actions: [
            { action: "add-site", label: "Add candidate site here", icon: "pin", primary: true, data: { lat: center[1], lng: center[0] } },
            { action: "zoom", label: "Zoom in", icon: "zoom", data: { lat: center[1], lng: center[0], zoom: 12 } },
          ],
        });
      }, POPUP_OPTIONS);
      layer.bindTooltip(() => `<strong>${escapeHtml(coverageOf())}</strong> · ${escapeHtml(distText)} to care`, {
        sticky: true, direction: "top", offset: [0, -8], className: "cell-tooltip", opacity: 1,
      });
      layer.on("mouseover", () => {
        layer.setStyle({ weight: 2.5, color: "#f4c542", fillOpacity: 0.62 });
        layer.bringToFront();
      });
      layer.on("mouseout", () => {
        layer.setStyle({ weight: 0.4, color: "#0f1c1a", fillOpacity: 0.45 });
      });
    },
  }).addTo(layers.cells);
}

export function renderPriorityCells(active) {
  const { roadAccessKm } = data;
  layers.priority.clearLayers();
  // Border slivers (< 1 km², see MIN_RANKED_CELL_KM2) are scored but not ranked.
  const topCells = active.cells
    .filter((cell) => Number(cell.properties.area_km2) >= 1)
    .sort((a, b) =>
      cellScore(b.properties) - cellScore(a.properties)
      || Number(b.properties.cell_id) - Number(a.properties.cell_id))
    .slice(0, 12);
  const recBody = document.getElementById("recs");
  recBody.replaceChildren();
  topCells.forEach((cell, index) => {
    const p = cell.properties;
    const rank = index + 1;
    const point = turf.pointOnFeature(cell).geometry.coordinates;
    const isNew = !state.baselineTopCells.has(Number(p.cell_id));
    L.marker([point[1], point[0]], {
      icon: L.divIcon({
        className: "priority-rank-icon",
        html: `<span class="priority-rank">${rank}</span>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      }),
      zIndexOffset: 1000 - rank,
      title: `Priority rank ${rank}`,
    })
      .bindPopup(popupCard({
        kicker: `Priority rank ${rank} of 12 · grid cell ${p.cell_id}`,
        title: isNew ? "New in top 12 with these weights" : "Highest-priority health gap",
        badge: { text: p.coverage, tone: p.coverage },
        rows: [
          ["Residents 2025 → 2030", `${Number(p.population || 0).toLocaleString()} → ${growthText(p)}`],
          ["Walking distance to nearest facility", p.dist_km == null ? "unavailable" : `${p.dist_km} km`],
          ["Motorcycle travel time to care", formatMinutes(p.travel_min)],
          [`Within ${roadAccessKm} km of a classified road`, formatShare(p.road_access_share)],
          ["Area in flood zone", formatShare(p.flood_area_share)],
        ],
        meter: { label: "Priority score (current weights)", percent: Number(cellScore(p)) / 2 * 100, text: Number(cellScore(p)).toFixed(3) },
        note: planningPrompt(p),
        actions: [
          { action: "add-site", label: "Add candidate site here", icon: "pin", primary: true, data: { lat: point[1], lng: point[0] } },
          { action: "zoom", label: "Zoom in", icon: "zoom", data: { lat: point[1], lng: point[0], zoom: 12 } },
        ],
      }), POPUP_OPTIONS)
      .addTo(layers.priority);

    const row = document.createElement("tr");
    row.className = "rec-row";
    row.innerHTML = `
        <td>${rank}${isNew ? ' <span class="tag new-rank" title="Not in the baseline top 12">new</span>' : ""}</td>
        <td>${escapeHtml(Number(cellScore(p)).toFixed(3))}</td>
        <td><span class="tag ${escapeHtml(p.coverage)}">${escapeHtml(p.coverage)}</span></td>
        <td>${escapeHtml(growthText(p))}</td>
        <td>${escapeHtml(p.dist_km)} km</td>
        <td><span class="tag ${travelTimeTone(p.travel_min)}">${escapeHtml(formatMinutes(p.travel_min))}</span></td>
        <td>${escapeHtml(formatShare(p.road_access_share))}</td>
        <td>${escapeHtml(formatShare(p.flood_area_share))}</td>
        <td>${escapeHtml(planningPrompt(p))}</td>`;
    row.addEventListener("click", () => map.setView([point[1], point[0]], 12));
    recBody.appendChild(row);
  });
}
