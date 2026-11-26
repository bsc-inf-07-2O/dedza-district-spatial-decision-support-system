// Health gap tool: served / underserved walking-distance thresholds, re-run in
// SQL on the server (/api/gap-analysis) and shown on the grid and legends.
import { state } from "../state.js";
import { updateRangeFills } from "../ui/range.js";
import { activeData } from "./area.js";
import { renderCellCoverage } from "./grid.js";
import { schedulePriorityScenario } from "./priority.js";

const gapServedInput = document.getElementById("gap-served-km");
const gapUnderservedInput = document.getElementById("gap-underserved-km");
const gapServedValue = document.getElementById("gap-served-value");
const gapUnderservedValue = document.getElementById("gap-underserved-value");
const gapAnalysisResults = document.getElementById("gap-analysis-results");
let gapAnalysisTimer = null;

export function renderGapAnalysisSummary(result) {
  state.gapServedKm = result.served_km;
  state.gapUnderservedKm = result.underserved_km;
  state.gapCoverage = new Map(
    result.cells.map((cell) => [Number(cell.cell_id), cell.coverage])
  );
  gapServedInput.value = String(result.served_km);
  gapUnderservedInput.value = String(result.underserved_km);
  gapUnderservedInput.min = String(result.served_km + 1);
  gapServedValue.textContent = `${result.served_km} km`;
  gapUnderservedValue.textContent = `${result.underserved_km} km`;
  const legendLabels = {
    served: `Served (≤ ${result.served_km} km)`,
    underserved: `Underserved (> ${result.served_km} to ≤ ${result.underserved_km} km)`,
    unserved: `Unserved (> ${result.underserved_km} km)`,
  };
  Object.entries(legendLabels).forEach(([key, text]) => {
    document.getElementById(`legend-${key}`).textContent = text;
    document.getElementById(`map-legend-${key}`).textContent = text;
  });
  gapAnalysisResults.replaceChildren();
  ["served", "underserved", "unserved"].forEach((category) => {
    const values = result.coverage[category];
    const row = document.createElement("div");
    row.className = `gap-analysis-row ${category}`;
    const label = document.createElement("span");
    label.textContent = category;
    const summary = document.createElement("strong");
    summary.textContent =
      `${values.cells.toLocaleString()} cells · ${Number(values.km2).toLocaleString()} km²`;
    row.append(label, summary);
    gapAnalysisResults.appendChild(row);
  });
  updateRangeFills();
}

function scheduleGapAnalysis() {
  const servedKm = Number(gapServedInput.value);
  gapUnderservedInput.min = String(servedKm + 1);
  if (Number(gapUnderservedInput.value) <= servedKm) {
    gapUnderservedInput.value = String(servedKm + 1);
  }
  gapServedValue.textContent = `${servedKm} km`;
  gapUnderservedValue.textContent = `${gapUnderservedInput.value} km`;
  updateRangeFills();
  gapAnalysisResults.textContent = "Recalculating coverage in SQLite...";
  const requestId = ++state.gapAnalysisRequestId;
  clearTimeout(gapAnalysisTimer);
  gapAnalysisTimer = setTimeout(async () => {
    const params = new URLSearchParams({
      served_km: String(servedKm),
      underserved_km: gapUnderservedInput.value,
    });
    try {
      const response = await fetch(`/api/gap-analysis?${params}`);
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || `Gap analysis failed (${response.status}).`);
      }
      if (requestId !== state.gapAnalysisRequestId) return;
      renderGapAnalysisSummary(result);
      renderCellCoverage(activeData().cells);
      // The served-cell discount in the priority score follows this threshold.
      schedulePriorityScenario();
    } catch (error) {
      if (requestId === state.gapAnalysisRequestId) {
        gapAnalysisResults.textContent = `Could not recalculate coverage: ${error.message}`;
      }
    }
  }, 180);
}

// Show the start-up result (from /api/gap-analysis with the default thresholds).
export function showInitialGapAnalysis(gapAnalysis) {
  gapServedInput.value = String(gapAnalysis.served_km);
  gapUnderservedInput.value = String(gapAnalysis.underserved_km);
  gapUnderservedInput.min = String(gapAnalysis.served_km + 1);
  renderGapAnalysisSummary(gapAnalysis);
}

export function setupGapAnalysis() {
  gapServedInput.addEventListener("input", scheduleGapAnalysis);
  gapUnderservedInput.addEventListener("input", scheduleGapAnalysis);
}
