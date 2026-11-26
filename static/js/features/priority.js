// Priorities tool: criterion weights for the cell priority score, re-scored in
// SQL on the server (/api/priority-scenario).
import { data, PRIORITY_CRITERIA } from "../data.js";
import { state } from "../state.js";
import { ordinal } from "../ui/format.js";
import { updateRangeFills } from "../ui/range.js";
import { activeData } from "./area.js";
import { render } from "./render.js";

const weightInputs = Object.fromEntries(
  Object.keys(PRIORITY_CRITERIA).map((key) => [key, document.getElementById(`w-${key}`)])
);
const weightOutputs = Object.fromEntries(
  Object.keys(PRIORITY_CRITERIA).map((key) => [key, document.getElementById(`w-${key}-value`)])
);
const priorityWeightsStatus = document.getElementById("priority-weights-status");
const resetWeightsButton = document.getElementById("reset-weights");
let priorityTimer = null;

// Show each slider's share of the total as a percentage.
function showNormalisedWeights() {
  const raw = Object.fromEntries(
    Object.entries(weightInputs).map(([key, input]) => [key, Number(input.value)])
  );
  const total = Object.values(raw).reduce((sum, value) => sum + value, 0);
  Object.entries(raw).forEach(([key, value]) => {
    weightOutputs[key].textContent = total ? `${Math.round(value / total * 100)}%` : "0%";
  });
  updateRangeFills();
  return { raw, total };
}

export function schedulePriorityScenario() {
  const { raw, total } = showNormalisedWeights();
  const requestId = ++state.priorityRequestId;
  clearTimeout(priorityTimer);
  if (!total) {
    priorityWeightsStatus.textContent = "Set at least one weight above zero.";
    return;
  }
  priorityWeightsStatus.textContent = "Re-scoring cells in SQLite...";
  priorityTimer = setTimeout(async () => {
    const params = new URLSearchParams({
      ...Object.fromEntries(Object.keys(PRIORITY_CRITERIA).map((key) => [`w_${key}`, String(raw[key])])),
      served_km: String(state.gapServedKm),
    });
    try {
      const response = await fetch(`/api/priority-scenario?${params}`);
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || `Priority scoring failed (${response.status}).`);
      }
      if (requestId !== state.priorityRequestId) return;
      state.priorityScores = new Map(
        result.cells.map((cell) => [Number(cell.cell_id), cell.score])
      );
      render(activeData());
      const changed = result.top_cell_ids
        .filter((id) => !state.baselineTopCells.has(Number(id))).length;
      const w = result.weights;
      priorityWeightsStatus.textContent =
        `Weights: ${Object.entries(PRIORITY_CRITERIA).map(([key, label]) => `${label} ${w[key]}%`).join(", ")}. ` +
        (changed
          ? `${changed} of the district top 12 cells differ from the baseline ranking.`
          : "District top 12 matches the baseline ranking.");
    } catch (error) {
      if (requestId === state.priorityRequestId) {
        priorityWeightsStatus.textContent = `Could not re-score cells: ${error.message}`;
      }
    }
  }, 200);
}

export function setupPriorityWeights() {
  const { baselineWeights, baselineWeightsLabel, baselineWeightsText, priorityScenario, roadAccessKm } = data;
  Object.values(weightInputs).forEach((input) => {
    input.addEventListener("input", schedulePriorityScenario);
  });
  resetWeightsButton.addEventListener("click", () => {
    Object.entries(baselineWeights).forEach(([key, value]) => {
      weightInputs[key].value = String(value);
    });
    schedulePriorityScenario();
  });
  Object.entries(baselineWeights).forEach(([key, value]) => {
    weightInputs[key].value = String(value);
  });
  resetWeightsButton.textContent = `Reset to baseline (${baselineWeightsLabel})`;
  resetWeightsButton.title = `Restore ${baselineWeightsText}`;

  // Methods note in the briefing, from the scales stored in the database.
  const scales = priorityScenario.scales;
  document.getElementById("method-priority").textContent =
    `The priority score combines WorldPop's 2030 projected residents per cell (full score at ${Math.round(scales.population_full).toLocaleString()}, ` +
    `the ${ordinal(Math.round((scales.full_score_percentile ?? 0.9) * 100))} percentile of cells), residents added 2025–2030 (full score at ${Math.round(scales.growth_full).toLocaleString()}), ` +
    `walking distance along roads and paths to the nearest facility (full score at ${scales.distance_full_m / 1000} km), the share of residents within ${roadAccessKm} km ` +
    `of a classified road, and the share of the cell outside the JRC 1-in-100-year flood zone (baseline ${baselineWeightsText}; adjustable under Priority weights). ` +
    `Served cells are multiplied by ${scales.served_multiplier}, and border slivers under ${priorityScenario.min_ranked_cell_km2} km² are scored but not ranked. ` +
    `WorldPop projects similar growth rates across most cells, so growth mostly follows where people already live.`;
  document.getElementById("method-settlement-count").textContent = data.settlements.features.length.toLocaleString();
  showNormalisedWeights();
}
