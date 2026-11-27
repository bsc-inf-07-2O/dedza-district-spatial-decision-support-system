// What the user has chosen so far. Every feature module reads and updates this
// one object; render() in static/js/features/render.js redraws from it.

export const state = {
  // Selected area: null / [] for the whole district, else a Traditional Authority.
  name: null,
  areas: [],

  // Candidate sites (New sites tool)
  scenarios: [],
  nextScenarioId: 1,
  placingScenario: false,
  siteScreeningRequestId: 0,
  siteScreeningPending: false,
  scenarioResultsCollapsed: false,
  scenarioLayerHidden: undefined,

  // Health gap thresholds (cell_id -> coverage class under the current sliders)
  gapCoverage: new Map(),
  gapServedKm: 5,
  gapUnderservedKm: 8,
  gapAnalysisRequestId: 0,

  // Priority weights (cell_id -> score under the current weights)
  priorityScores: new Map(),
  baselineTopCells: new Set(),
  priorityRequestId: 0,

  // Schools tool: walking radius and which schools count ("all", "primary" or
  // "secondary"). The level also applies to school candidates in New sites.
  schoolRadiusKm: 5,
  schoolLevel: "all",
};

// Fill in the starting values from the first API responses (see static/js/data.js).
export function initState(data) {
  const { gapAnalysis, priorityScenario } = data;
  state.gapCoverage = new Map(gapAnalysis.cells.map((cell) => [cell.cell_id, cell.coverage]));
  state.gapServedKm = gapAnalysis.served_km;
  state.gapUnderservedKm = gapAnalysis.underserved_km;
  state.priorityScores = new Map(priorityScenario.cells.map((cell) => [Number(cell.cell_id), cell.score]));
  state.baselineTopCells = new Set(priorityScenario.top_cell_ids.map(Number));
  state.schoolRadiusKm = Number(document.getElementById("school-radius").value) || 5;
}
