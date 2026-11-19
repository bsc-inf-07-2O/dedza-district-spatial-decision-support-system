const mapStatus = document.getElementById("map-status");
if (typeof L === "undefined") {
  mapStatus.textContent = "The map library did not load. Check your internet connection or whether unpkg.com is blocked, then reload.";
} else {
const map = L.map("map", { zoomControl: true }).setView([-14.38, 34.33], 10);
let baseTilesFailed = false;
const baseMap = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
});
baseMap.on("tileerror", () => {
  baseTilesFailed = true;
  mapStatus.hidden = false;
  mapStatus.textContent = "Base map tiles could not be loaded. Check your internet connection; planning overlays may still be available.";
});
baseMap.addTo(map);

const layers = {
  cells: L.layerGroup().addTo(map),
  facilities: L.layerGroup().addTo(map),
  health2026: L.layerGroup().addTo(map),
  schools: L.layerGroup().addTo(map),
  settlements: L.layerGroup().addTo(map),
  buffers: L.layerGroup(),
  roads: L.layerGroup().addTo(map),
  water: L.layerGroup().addTo(map),
  priority: L.layerGroup().addTo(map),
  scenario: L.layerGroup().addTo(map),
  buildings: L.layerGroup(),
  slope: L.tileLayer("/api/environmental-tiles/slope/{z}/{x}/{y}.png", {
    maxNativeZoom: 12,
    maxZoom: 18,
    opacity: 0.62,
    attribution: "Mapzen Terrain Tiles; SRTM/GMTED2010 data courtesy of USGS",
  }),
  landCover: L.tileLayer("/api/environmental-tiles/land_cover/{z}/{x}/{y}.png", {
    maxNativeZoom: 15,
    maxZoom: 18,
    opacity: 0.62,
    attribution: "© ESA WorldCover 2021 / Contains modified Copernicus Sentinel data 2021",
  }),
};

const selectedBoundary = L.geoJSON(null, {
  style: { color: "#f4a261", weight: 3, fillColor: "#f4a261", fillOpacity: 0.08 },
}).addTo(map);
const routeLayer = L.featureGroup().addTo(map);
const routeColors = ["#c49bff", "#4ea8de", "#f4a261"];
const scenarioColors = ["#e76f51", "#2a9d8f", "#4ea8de", "#c49bff", "#e9c46a"];

const coverageColor = {
  served: "#2a9d8f",
  underserved: "#e9c46a",
  unserved: "#e76f51",
};

function roadWeight(highway) {
  if (["trunk", "primary", "motorway"].includes(highway)) return 2.4;
  if (["secondary", "tertiary"].includes(highway)) return 1.6;
  return 0.8;
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load ${url}`);
  return res.json();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}
function travelAdjustedScore(properties) {
  if (Number.isFinite(Number(properties.road_access_score))) {
    return Math.max(0, Math.min(100, Number(properties.road_access_score)));
  }
  const distKm = Number(properties.dist_km) || 0;
  const roadKm = Number(properties.road_km) || 0;
  const waterKm = Number(properties.water_km) || 0;

  const roadDistanceProxyKm = distKm + Math.max(0, 5 - roadKm) * 1.8 + Math.max(0, 2 - waterKm) * 1.2;
  return Math.max(0, Math.min(100, Math.round(100 - roadDistanceProxyKm * 6)));
}

function travelAdjustedLabel(score) {
  if (score >= 75) return "strong";
  if (score >= 50) return "moderate";
  return "weak";
}
function kpi(label, value) {
  return `<div class="kpi"><b>${value}</b><span>${label}</span></div>`;
}

function mapSymbol(shape, fill, stroke, size = 12) {
  return L.divIcon({
    className: "map-symbol",
    html: `<span class="${shape}" style="--symbol-fill:${fill};--symbol-stroke:${stroke}"></span>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

function asFeature(geometry, properties = {}) {
  return { type: "Feature", properties, geometry };
}

async function init() {
  const [summary, boundary, facilities, health2026, populationSummary, schools, settlements, cells, roads, waterways, taData, siteLayers, gapAnalysis] = await Promise.all([
    fetchJson("/api/summary"),
    fetchJson("/api/boundary"),
    fetchJson("/api/facilities"),
    fetchJson("/api/health-facilities-2026"),
    fetchJson("/api/population-summary"),
    fetchJson("/api/schools"),
    fetchJson("/api/settlements"),
    fetchJson("/api/cells"),
    fetchJson("/api/roads"),
    fetchJson("/api/waterways"),
    fetchJson("/api/traditional-authorities"),
    fetchJson("/api/site-layers"),
    fetchJson("/api/gap-analysis"),
  ]);

  const taFeatures = taData.features;
  const taNames = [...new Set(taFeatures.map((feature) => feature.properties.name))]
    .sort((a, b) => a.localeCompare(b));
  const taByName = new Map(taNames.map((name) => [
    name.toLocaleLowerCase(), taFeatures.filter((feature) => feature.properties.name === name),
  ]));
  const populationByArea = new Map();
  populationSummary.forEach((row) => {
    const area = populationByArea.get(row.area_name) || {};
    area[row.coverage] = Number(row.population_est) || 0;
    populationByArea.set(row.area_name, area);
  });
  const districtLayer = L.geoJSON(boundary, {
    style: { color: "#e8f1ee", weight: 2, fill: false },
  }).addTo(map);
  map.fitBounds(districtLayer.getBounds().pad(0.04));

  const state = {
    name: null,
    areas: [],
    scenarios: [],
    nextScenarioId: 1,
    placingScenario: false,
    siteScreeningRequestId: 0,
    siteScreeningPending: false,
    scenarioResultsCollapsed: false,
    gapCoverage: new Map(gapAnalysis.cells.map((cell) => [cell.cell_id, cell.coverage])),
    gapServedKm: gapAnalysis.served_km,
    gapUnderservedKm: gapAnalysis.underserved_km,
    gapAnalysisRequestId: 0,
  };
  const areaStatus = document.getElementById("area-status");
  const taSearch = document.getElementById("ta-search");
  const taOptions = document.getElementById("ta-options");
  const taOptionsToggle = document.getElementById("toggle-ta-options");
  const clearTASearch = document.getElementById("clear-ta-search");
  const districtButton = document.getElementById("clear-ta");
  const routeSettlementSelect = document.getElementById("route-settlement");
  const routeFacilitySelect = document.getElementById("route-facility");
  const findRouteButton = document.getElementById("find-route");
  const clearRouteButton = document.getElementById("clear-route");
  const routeResultsPanel = document.getElementById("route-results-panel");
  const routeControlStatus = document.getElementById("route-control-status");
  L.DomEvent.disableClickPropagation(routeResultsPanel);
  const routeStatus = document.getElementById("route-status");
  const routeOptionsContainer = document.getElementById("route-options");
  const routeRoadDistance = document.getElementById("route-road-distance");
  const routeTotalDistance = document.getElementById("route-total-distance");
  const routeTravelTime = document.getElementById("route-travel-time");
  const routeConnectors = document.getElementById("route-connectors");
  const schoolRadiusInput = document.getElementById("school-radius");
  const scenarioTypeInput = document.getElementById("scenario-type");
  const scenarioRadiusInput = document.getElementById("scenario-radius");
  const scenarioStatus = document.getElementById("scenario-status");
  const placeScenarioButton = document.getElementById("place-scenario");
  const clearScenarioButton = document.getElementById("clear-scenario");
  const scenarioResults = document.getElementById("scenario-results");
  const scenarioCandidateList = document.getElementById("scenario-candidate-list");
  const scenarioResultsSummary = document.getElementById("scenario-results-summary");
  const scenarioResultsToggle = document.getElementById("toggle-scenario-results");
  const scenarioNotice = document.getElementById("scenario-notice");
  const scenarioNoticeTitle = document.getElementById("scenario-notice-title");
  const scenarioNoticeMessage = document.getElementById("scenario-notice-message");
  const scenarioNoticeIcon = document.getElementById("scenario-notice-icon");
  const closeScenarioNotice = document.getElementById("close-scenario-notice");
  const buildingToggle = document.getElementById("lyr-buildings");
  const slopeToggle = document.getElementById("lyr-slope");
  const landCoverToggle = document.getElementById("lyr-land-cover");
  const buildingLayerStatus = document.getElementById("building-layer-status");
  const environmentLayerStatus = document.getElementById("environment-layer-status");
  const gapServedInput = document.getElementById("gap-served-km");
  const gapUnderservedInput = document.getElementById("gap-underserved-km");
  const gapServedValue = document.getElementById("gap-served-value");
  const gapUnderservedValue = document.getElementById("gap-underserved-value");
  const gapAnalysisResults = document.getElementById("gap-analysis-results");
  gapServedInput.value = String(gapAnalysis.served_km);
  gapUnderservedInput.value = String(gapAnalysis.underserved_km);
  gapUnderservedInput.min = String(gapAnalysis.served_km + 1);
  renderGapAnalysisSummary(gapAnalysis);
  L.DomEvent.disableClickPropagation(scenarioResults);
  L.DomEvent.disableScrollPropagation(scenarioResults);
  let schoolRadiusKm = Number(schoolRadiusInput.value) || 5;
  const buildingLayer = siteLayers.buildings;
  buildingToggle.disabled = !buildingLayer.available;
  buildingToggle.title = buildingLayer.available
    ? "Show detected footprints; coverage may be incomplete or out of date."
    : buildingLayer.message;
  buildingLayerStatus.textContent = buildingLayer.available
    ? `${buildingLayer.count.toLocaleString()} detected footprints · ${buildingLayer.source} (${buildingLayer.date}; ${buildingLayer.license}). ${buildingLayer.message}`
    : `${buildingLayer.message} Run python scripts/import_buildings.py to fetch the Malawi tiles and clip them to Dedza.`;
  slopeToggle.disabled = !siteLayers.terrain.available;
  slopeToggle.title = siteLayers.terrain.message;
  landCoverToggle.disabled = !siteLayers.land_cover.available;
  landCoverToggle.title = siteLayers.land_cover.message;
  environmentLayerStatus.textContent = [
    `Slope: ${siteLayers.terrain.message}`,
    `Land cover: ${siteLayers.land_cover.message}`,
  ].join(" ");
  let buildingRequestId = 0;
  let buildingLoadTimer = null;

  let scenarioNoticeTimer = null;
  function showScenarioNotice(title, message, kind = "warning", temporary = false) {
    clearTimeout(scenarioNoticeTimer);
    scenarioNotice.className = `scenario-notice ${kind}`;
    scenarioNotice.hidden = false;
    scenarioNoticeTitle.textContent = title;
    scenarioNoticeMessage.textContent = message;
    scenarioNoticeIcon.textContent = kind === "error" ? "!" : kind === "success" ? "✓" : "i";
    if (temporary) {
      scenarioNoticeTimer = setTimeout(() => {
        scenarioNotice.hidden = true;
      }, 4000);
    }
  }
  closeScenarioNotice.addEventListener("click", () => {
    clearTimeout(scenarioNoticeTimer);
    scenarioNotice.hidden = true;
  });

  async function loadBuildingFeatures() {
    const requestId = ++buildingRequestId;
    layers.buildings.clearLayers();
    if (!buildingToggle.checked || !buildingLayer.available) return;
    if (map.getZoom() < 12) {
      buildingLayerStatus.textContent = "Zoom in to level 12 or closer to display detected building footprints.";
      return;
    }
    const bounds = map.getBounds();
    const bbox = [
      bounds.getWest(),
      bounds.getSouth(),
      bounds.getEast(),
      bounds.getNorth(),
    ].join(",");
    buildingLayerStatus.textContent = "Loading building footprints for the visible map area...";
    try {
      const data = await fetchJson(`/api/buildings?bbox=${encodeURIComponent(bbox)}`);
      if (requestId !== buildingRequestId) return;
      L.geoJSON(data, {
        style: {
          color: "#7b2cbf",
          weight: 1,
          fillColor: "#c77dff",
          fillOpacity: 0.48,
        },
        onEachFeature(feature, layer) {
          const properties = feature.properties || {};
          const details = document.createElement("div");
          details.textContent = `Detected building${properties.confidence == null ? "" : ` · confidence ${Math.round(properties.confidence * 100)}%`}${properties.height_m == null || properties.height_m < 0 ? "" : ` · estimated height ${properties.height_m} m`}`;
          layer.bindPopup(details);
        },
      }).addTo(layers.buildings);
      buildingLayerStatus.textContent = data.truncated
        ? `Showing the first ${data.features.length.toLocaleString()} footprints in this view. Zoom in further to see more.`
        : `${data.features.length.toLocaleString()} detected footprints shown in this view. Absence of a footprint does not prove land is vacant.`;
    } catch (error) {
      if (requestId === buildingRequestId) {
        buildingLayerStatus.textContent = `Could not load building footprints: ${error.message}`;
      }
    }
  }

  function scheduleBuildingLoad() {
    clearTimeout(buildingLoadTimer);
    buildingLoadTimer = setTimeout(loadBuildingFeatures, 200);
  }

  async function screenCandidateSite(coordinates) {
    const response = await fetch("/api/site-screening", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ coordinates }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || `Site screening failed (${response.status}).`);
    }
    return result;
  }
  const routeFacilities = [
    ...facilities.map((facility) => ({
      name: facility.name,
      source: "Baseline",
      lon: Number(facility.lon),
      lat: Number(facility.lat),
    })),
    ...health2026.features.map((facility) => ({
      name: facility.properties.name,
      source: "2026 reference",
      lon: Number(facility.geometry.coordinates[0]),
      lat: Number(facility.geometry.coordinates[1]),
    })),
  ];
  const nearestSchoolBySettlement = new WeakMap();
  const nearestFacilityBySettlement = new WeakMap();

  settlements.features.forEach((settlement, index) => {
    const option = document.createElement("option");
    const [lon, lat] = settlement.geometry.coordinates;
    option.value = String(index);
    option.textContent = `${settlement.properties.name} · ${settlement.properties.adm3_name || "Dedza"} (${lon.toFixed(3)}, ${lat.toFixed(3)})`;
    routeSettlementSelect.appendChild(option);
  });
  const baselineFacilities = document.createElement("optgroup");
  baselineFacilities.label = "Baseline health facilities";
  const referenceFacilities = document.createElement("optgroup");
  referenceFacilities.label = "2026 reference facilities";
  routeFacilities.forEach((facility, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = facility.name;
    (facility.source === "Baseline" ? baselineFacilities : referenceFacilities).appendChild(option);
  });
  routeFacilitySelect.append(baselineFacilities, referenceFacilities);

  function enhanceSelect(select) {
    const label = document.querySelector(`label[for="${select.id}"]`);
    const wrapper = document.createElement("div");
    wrapper.className = "custom-select";
    select.before(wrapper);
    wrapper.appendChild(select);
    select.classList.add("custom-select-source");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");

    const trigger = document.createElement("button");
    trigger.id = `${select.id}-button`;
    trigger.type = "button";
    trigger.className = "custom-select-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", `${select.id}-listbox`);
    if (label) {
      trigger.setAttribute("aria-label", label.textContent.trim());
      label.htmlFor = trigger.id;
    }
    const describedBy = select.getAttribute("aria-describedby");
    if (describedBy) trigger.setAttribute("aria-describedby", describedBy);

    const menu = document.createElement("div");
    menu.id = `${select.id}-listbox`;
    menu.className = "custom-select-menu";
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-labelledby", trigger.id);
    menu.hidden = true;
    wrapper.append(trigger, menu);

    let typeahead = "";
    let typeaheadTimer = null;
    const options = () => [...menu.querySelectorAll(".custom-select-option")];
    const sync = () => {
      const selected = select.options[select.selectedIndex];
      trigger.textContent = selected?.textContent.trim() || "Select an option";
      trigger.disabled = select.disabled;
      options().forEach((option) => {
        option.setAttribute("aria-selected", String(option.dataset.value === select.value));
      });
    };
    const close = (returnFocus = false) => {
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      if (returnFocus) trigger.focus();
    };
    const choose = (option) => {
      if (!option || option.getAttribute("aria-disabled") === "true" || select.disabled) return;
      select.value = option.dataset.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      sync();
      close(true);
    };
    const addOption = (nativeOption) => {
      const option = document.createElement("div");
      option.className = "custom-select-option";
      option.setAttribute("role", "option");
      option.tabIndex = -1;
      option.dataset.value = nativeOption.value;
      option.textContent = nativeOption.textContent.trim();
      if (nativeOption.disabled) option.setAttribute("aria-disabled", "true");
      menu.appendChild(option);
    };
    const renderOptions = () => {
      menu.replaceChildren();
      [...select.children].forEach((child) => {
        if (child instanceof HTMLOptGroupElement) {
          const groupLabel = document.createElement("div");
          groupLabel.className = "custom-select-group";
          groupLabel.textContent = child.label;
          menu.appendChild(groupLabel);
          Array.from(child.children).forEach(addOption);
        } else if (child instanceof HTMLOptionElement) {
          addOption(child);
        }
      });
      sync();
    };
    const open = (focusSelected) => {
      if (select.disabled) return;
      renderOptions();
      menu.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      if (focusSelected) {
        (options().find((option) => option.dataset.value === select.value) || options()[0])?.focus();
      }
    };
    const focusTypeaheadMatch = (key) => {
      const character = key.toLocaleLowerCase();
      typeahead = typeahead && Array.from(typeahead).every((item) => item === character)
        ? character
        : typeahead + character;
      clearTimeout(typeaheadTimer);
      typeaheadTimer = setTimeout(() => { typeahead = ""; }, 700);
      const match = options().find((option) =>
        option.textContent.trim().toLocaleLowerCase().startsWith(typeahead)
      );
      if (match) match.focus();
    };

    trigger.addEventListener("click", () => {
      if (menu.hidden) open(false);
      else close();
    });
    trigger.addEventListener("keydown", (event) => {
      if (event.key.length === 1 && event.key !== " " && !event.altKey && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        open(false);
        focusTypeaheadMatch(event.key);
        return;
      }
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
        event.preventDefault();
        open(true);
      } else if (event.key === "Escape") {
        close();
      }
    });
    menu.addEventListener("click", (event) => {
      const option = event.target.closest(".custom-select-option");
      if (option) choose(option);
    });
    menu.addEventListener("keydown", (event) => {
      const list = options();
      const index = list.indexOf(document.activeElement);
      if (event.key.length === 1 && event.key !== " " && !event.altKey && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        focusTypeaheadMatch(event.key);
      } else if (event.key === "Escape") {
        event.preventDefault();
        close(true);
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const direction = event.key === "ArrowDown" ? 1 : -1;
        list[(index + direction + list.length) % list.length]?.focus();
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        list[event.key === "Home" ? 0 : list.length - 1]?.focus();
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        choose(document.activeElement);
      }
    });
    document.addEventListener("click", (event) => {
      if (!wrapper.contains(event.target)) close();
    });
    select.addEventListener("change", sync);
    new MutationObserver(() => {
      renderOptions();
      sync();
    }).observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "label"] });
    renderOptions();
  }
  [routeSettlementSelect, routeFacilitySelect, scenarioTypeInput].forEach(enhanceSelect);

  function enhanceNumberInput(input) {
    const label = document.querySelector(`label[for="${input.id}"]`);
    const wrapper = document.createElement("div");
    wrapper.className = "number-input-control";
    input.before(wrapper);
    wrapper.appendChild(input);

    const stepper = document.createElement("div");
    stepper.className = "number-stepper";
    const createStepButton = (name, icon, direction) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "number-stepper-button";
      button.setAttribute("aria-label", `${name} ${label?.textContent.trim() || input.id}`);
      button.setAttribute("aria-controls", input.id);
      button.textContent = icon;
      button.addEventListener("click", () => {
        const previousValue = input.value;
        if (direction > 0) input.stepUp();
        else input.stepDown();
        if (input.value !== previousValue) {
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
        input.focus();
        updateButtons();
      });
      return button;
    };
    const increase = createStepButton("Increase", "▲", 1);
    const decrease = createStepButton("Decrease", "▼", -1);
    stepper.append(increase, decrease);
    wrapper.appendChild(stepper);

    const updateButtons = () => {
      const value = input.valueAsNumber;
      const min = input.min === "" ? -Infinity : Number(input.min);
      const max = input.max === "" ? Infinity : Number(input.max);
      increase.disabled = input.disabled || (Number.isFinite(value) && value >= max);
      decrease.disabled = input.disabled || (Number.isFinite(value) && value <= min);
    };
    input.addEventListener("input", updateButtons);
    input.addEventListener("change", updateButtons);
    updateButtons();
  }
  [schoolRadiusInput, scenarioRadiusInput].forEach(enhanceNumberInput);

  settlements.features.forEach((settlement) => {
    let nearest = null;
    schools.features.forEach((school) => {
      const distance = turf.distance(settlement, school, { units: "kilometers" });
      if (!nearest || distance < nearest.distance) nearest = { school, distance };
    });
    nearestSchoolBySettlement.set(settlement, nearest);

    let nearestFacility = null;
    facilities.forEach((facility) => {
      const schoolDistance = turf.distance(
        settlement,
        turf.point([facility.lon, facility.lat]),
        { units: "kilometers" }
      );
      if (!nearestFacility || schoolDistance < nearestFacility.distance) {
        nearestFacility = { facility, distance: schoolDistance };
      }
    });
    nearestFacilityBySettlement.set(settlement, nearestFacility);
  });

  function intersectsSelectedArea(feature) {
    if (!state.areas.length) return true;
    try {
      return state.areas.some((area) => turf.booleanIntersects(feature, area));
    } catch {
      return false;
    }
  }

  function selectedFacilities() {
    return facilities.filter((facility) => {
      if (!state.areas.length) return true;
      const point = turf.point([facility.lon, facility.lat]);
      return state.areas.some((area) => turf.booleanPointInPolygon(point, area));
    });
  }

  function selectedFacilities2026() {
    return health2026.features.filter((facility) => intersectsSelectedArea(facility));
  }

  function selectedSchools() {
    return schools.features.filter((school) => {
      if (!state.areas.length) return true;
      return state.areas.some((area) => turf.booleanPointInPolygon(school, area));
    });
  }

  function selectedSettlements() {
    return settlements.features.filter((settlement) => {
      if (!state.areas.length) return true;
      return state.areas.some((area) => turf.booleanPointInPolygon(settlement, area));
    });
  }

  function clippedCellArea(cell) {
    if (!state.areas.length) return Number(cell.properties.area_km2) || 0;
    return state.areas.reduce((areaTotal, area) => {
      try {
        const overlap = turf.intersect(turf.featureCollection([cell, area]));
        return areaTotal + (overlap ? turf.area(overlap) / 1e6 : 0);
      } catch {
        return areaTotal;
      }
    }, 0);
  }

  function buildTAComparison() {
    const cellCenters = cells.features.map((cell) => ({
      cell,
      center: turf.centroid(cell),
    }));
    return taNames.map((name) => {
      const areas = taByName.get(name.toLocaleLowerCase());
      const includes = (point) => areas.some((area) => turf.booleanPointInPolygon(point, area));
      const taSettlements = settlements.features.filter((feature) => includes(feature));
      const schoolReach = taSettlements.filter(
        (feature) => nearestSchoolBySettlement.get(feature)?.distance <= 1
      ).length;
      const healthGaps = taSettlements.filter(
        (feature) => (nearestFacilityBySettlement.get(feature)?.distance ?? Infinity) > 5
      ).length;
      const unservedCells = cellCenters.filter(
        ({ cell, center }) => cell.properties.coverage === "unserved" && includes(center)
      ).length;
      const population = populationByArea.get(name) || {};
      const populationTotal = Number(population.total) || 0;
      const populationUnserved = Number(population.unserved) || 0;
      return {
        name,
        facilities: facilities.filter((facility) => includes(turf.point([facility.lon, facility.lat]))).length,
        facilities2026: health2026.features.filter((feature) => includes(feature)).length,
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

  function renderTAComparison(rows) {
    const tbody = document.getElementById("ta-comparison-rows");
    tbody.replaceChildren();
    let rank = 0;
    rows.forEach((metrics) => {
      const priorityRank = metrics.hasPopulationData ? ++rank : null;
      const row = document.createElement("tr");
      row.className = "rec-row";
      row.innerHTML = `
        <td class="priority-rank">${priorityRank ?? "—"}</td>
        <td>${escapeHtml(metrics.name)}</td>
        <td>${metrics.facilities}</td>
        <td>${metrics.facilities2026}</td>
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

  function renderSchoolProfile(areaSettlements) {
    const thresholds = [1, 3, 5, 8];
    const profile = document.getElementById("school-thresholds");
    profile.replaceChildren();
    thresholds.forEach((threshold) => {
      const covered = areaSettlements.filter(
        (settlement) => (nearestSchoolBySettlement.get(settlement)?.distance ?? Infinity) <= threshold
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

  function renderPopulationProfile(population) {
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

  function renderBriefing(active, metrics) {
    const areaName = state.name || "Dedza District";
    const population = populationByArea.get(areaName) || {};
    const populationTotal = population.total || 0;
    const populationUnserved = population.unserved || 0;
    const populationShare = populationTotal ? populationUnserved / populationTotal * 100 : 0;
    const populationLead = populationTotal
      ? `WorldPop 2025 estimates ${Math.round(populationTotal).toLocaleString()} people in ${areaName}; ${Math.round(populationUnserved).toLocaleString()} (${populationShare.toFixed(1)}%) fall in baseline health-grid cells more than 8 km from a facility.`
      : `No WorldPop summary is available for ${areaName}.`;

    const travelProxyScore = active.cells.length
      ? Math.round(active.cells.reduce((total, cell) => total + travelAdjustedScore(cell.properties), 0) / active.cells.length)
      : 0;

    document.getElementById("briefing-title").textContent = `${areaName} · Public-service access`;
    document.getElementById("briefing-lead").textContent = `${populationLead} ${metrics.healthGaps} of ${active.settlements.length} mapped settlements are more than 5 km from a baseline health facility.`;
    document.getElementById("briefing-kpis").innerHTML = [
      [Math.round(populationTotal).toLocaleString(), "WorldPop 2025 estimate"],
      [`${populationShare.toFixed(1)}%`, "Population in unserved health cells"],
      [`${travelProxyScore}/100`, "Road-distance access proxy"],
      [`${metrics.healthGaps}/${active.settlements.length}`, "Settlements > 5 km from baseline health"],
      [`${metrics.schoolReach}/${active.settlements.length}`, `Settlements within ${schoolRadiusKm} km of school`],
    ].map(([value, label]) => `<div class="briefing-kpi"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`).join("");
    const signals = [
      `<strong>Health access:</strong> ${metrics.unservedCells} grid cells (${metrics.unservedArea.toFixed(0)} km²) are beyond the 8 km baseline threshold.`,
      `<strong>Education proximity:</strong> ${metrics.schoolReach} of ${active.settlements.length} mapped settlements are within ${schoolRadiusKm} km straight-line distance of a school.`,
      `<strong>Road-distance access proxy:</strong> ${travelProxyScore}/100 when adjusting for facility distance, road network reach and mapped waterway context.`,
      `<strong>Facility sources:</strong> ${active.facilities.length} baseline facilities inform the access grid; ${active.health2026.features.length} 2026 reference facilities are displayed separately.`,
    ];
    document.getElementById("briefing-signals").innerHTML = signals.map((signal) => `<li>${signal}</li>`).join("");

    const tbody = document.getElementById("briefing-priorities");
    tbody.replaceChildren();
    metrics.priorityCells.forEach((cell) => {
      const p = cell.properties;
      const point = turf.pointOnFeature(cell).geometry.coordinates;
      const accessScore = travelAdjustedScore(p);
      const row = document.createElement("tr");
      row.className = "rec-row";
      row.innerHTML = `<td>${escapeHtml(p.cell_id)}</td><td>${escapeHtml(p.dist_km)} km</td><td><span class="tag ${travelAdjustedLabel(accessScore)}">${accessScore}/100</span></td>`;
      row.addEventListener("click", () => map.setView([point[1], point[0]], 12));
      tbody.appendChild(row);
    });
  }

  function closeTAOptions() {
    taOptions.hidden = true;
    taOptions.replaceChildren();
    taSearch.setAttribute("aria-expanded", "false");
    taOptionsToggle.setAttribute("aria-expanded", "false");
  }

  function showTAOptions(query) {
    const matches = taNames.filter((name) => name.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
    taOptions.replaceChildren();
    matches.forEach((name) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "ta-option";
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", String(name === state.name));
      option.textContent = name;
      option.addEventListener("click", () => {
        selectTA(name);
        taSearch.focus();
      });
      taOptions.appendChild(option);
    });
    taOptions.hidden = matches.length === 0;
    taSearch.setAttribute("aria-expanded", String(!taOptions.hidden));
    taOptionsToggle.setAttribute("aria-expanded", String(!taOptions.hidden));
    return matches.length;
  }

  function renderSettlementAccess(areaSettlements) {
    const tbody = document.getElementById("settlement-access-rows");
    tbody.replaceChildren();
    areaSettlements.forEach((settlement) => {
      const school = nearestSchoolBySettlement.get(settlement);
      const facility = nearestFacilityBySettlement.get(settlement);
      const [lon, lat] = settlement.geometry.coordinates;
      const row = document.createElement("tr");
      row.className = "rec-row";
      row.innerHTML = `
        <td>${escapeHtml(settlement.properties.name)}</td>
        <td>${escapeHtml(settlement.properties.adm3_name)}</td>
        <td>${escapeHtml(school?.school.properties.name || "No mapped school")}</td>
        <td>${school ? `${school.distance.toFixed(1)} km` : "—"}</td>
        <td>${escapeHtml(facility?.facility.name || "No mapped facility")}</td>
        <td>${facility ? `${facility.distance.toFixed(1)} km` : "—"}</td>`;
      row.addEventListener("click", () => map.setView([lat, lon], 13));
      tbody.appendChild(row);
    });
  }

  function getScenarioMetrics(active, candidate = state.scenarios[0]) {
    if (!candidate) return null;
    const nearestService = candidate.type === "school"
      ? nearestSchoolBySettlement
      : nearestFacilityBySettlement;
    let before = 0;
    let after = 0;
    active.settlements.forEach((settlement) => {
      const currentDistance = nearestService.get(settlement)?.distance ?? Infinity;
      const candidateDistance = turf.distance(settlement, candidate.point, { units: "kilometers" });
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

  function renderScenario(active) {
    const layer = layers.scenario;
    layer.clearLayers();
    scenarioCandidateList.replaceChildren();
    scenarioResults.hidden = state.scenarios.length === 0;
    const mapContainer = map.getContainer();
    const hasScenarioResults = state.scenarios.length > 0;
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

    state.scenarios.forEach((candidate, index) => {
      const [lon, lat] = candidate.point.geometry.coordinates;
      const metrics = getScenarioMetrics(active, candidate);
      const color = scenarioColors[index % scenarioColors.length];
      const label = candidate.type === "school" ? "School" : "Health centre";
      L.circle([lat, lon], {
        radius: candidate.radius * 1000,
        color,
        weight: 2,
        fillColor: color,
        fillOpacity: 0.1,
        dashArray: "6 4",
      }).addTo(layer);
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
      popupRadius.textContent = `${candidate.radius} km straight-line radius`;
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
      card.appendChild(focus);

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
        const result = candidate.populationEstimate;
        const existingServiceLabel = candidate.type === "school" ? "school" : "baseline health facility";
        const gain = document.createElement("strong");
        gain.textContent = `${Math.round(result.additional_population_estimate).toLocaleString()} estimated additional residents in Dedza`;
        const detail = document.createElement("p");
        detail.textContent = `Within ${candidate.radius} km: ${Math.round(result.population_within_radius).toLocaleString()} estimated residents; ${Math.round(result.already_within_existing_service_radius).toLocaleString()} are already within an existing ${existingServiceLabel} zone.`;
        card.append(gain, detail);

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
      scenarioCandidateList.appendChild(card);
    });

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

  async function estimateScenarioCandidate(candidate) {
    candidate.estimateVersion = (candidate.estimateVersion || 0) + 1;
    const version = candidate.estimateVersion;
    candidate.populationEstimate = null;
    candidate.populationError = "";
    candidate.loading = true;
    renderScenario(activeData());
    try {
      const response = await fetch("/api/scenario-population", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          coordinates: candidate.point.geometry.coordinates,
          type: candidate.type,
          radius_km: candidate.radius,
        }),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || `Population estimate failed (${response.status}).`);
      }
      if (!Number.isFinite(result.additional_population_estimate)) {
        throw new Error("The population estimate response was incomplete.");
      }
      if (state.scenarios.includes(candidate) && candidate.estimateVersion === version) {
        candidate.populationEstimate = result;
      }
    } catch (error) {
      if (state.scenarios.includes(candidate) && candidate.estimateVersion === version) {
        candidate.populationError = error.message;
      }
    } finally {
      if (state.scenarios.includes(candidate) && candidate.estimateVersion === version) {
        candidate.loading = false;
        renderScenario(activeData());
      }
    }
  }

  function refreshScenarioEstimates() {
    state.scenarios.forEach((candidate) => {
      candidate.type = scenarioTypeInput.value;
      candidate.radius = Number(scenarioRadiusInput.value);
      estimateScenarioCandidate(candidate);
    });
  }

  function csvField(value) {
    return `"${String(value ?? "").replace(/"/g, '""')}"`;
  }

  function exportPlanningBrief() {
    const active = activeData();
    const areaLabel = state.name || "Dedza District";
    const rows = [[
      "section", "area", "metric", "value", "settlement", "osm_adm3_label",
      "nearest_school", "school_distance_km", "nearest_health_facility", "health_distance_km",
    ]];
    const schoolCoverage = [1, 3, 5, 8].map((threshold) => [
      threshold,
      active.settlements.filter(
        (settlement) => (nearestSchoolBySettlement.get(settlement)?.distance ?? Infinity) <= threshold
      ).length,
    ]);
    const healthGaps = active.settlements.filter(
      (settlement) => (nearestFacilityBySettlement.get(settlement)?.distance ?? Infinity) > 5
    ).length;
    const unservedCells = active.cells.filter((cell) => cell.properties.coverage === "unserved").length;
    const population = populationByArea.get(areaLabel) || {};
    const totalPopulation = population.total || 0;
    const unservedPopulation = population.unserved || 0;
    rows.push(["summary", areaLabel, "schools", active.schools.length]);
    rows.push(["summary", areaLabel, "health facilities", active.facilities.length]);
    rows.push(["summary", areaLabel, "2026 health reference facilities", active.health2026.features.length]);
    rows.push(["summary", areaLabel, "mapped settlements", active.settlements.length]);
    rows.push(["summary", areaLabel, "settlements over 5 km from health", healthGaps]);
    rows.push(["summary", areaLabel, "unserved health grid cells", unservedCells]);
    rows.push(["population", areaLabel, "WorldPop 2025 total estimate", Math.round(totalPopulation)]);
    rows.push(["population", areaLabel, "WorldPop 2025 in unserved cells", Math.round(unservedPopulation)]);
    rows.push(["population", areaLabel, "share in unserved cells", totalPopulation ? `${(unservedPopulation / totalPopulation * 100).toFixed(1)}%` : ""]);
    rows.push(["source", areaLabel, "population dataset", "WorldPop 2025 R2025A v1; 100 m population-count raster; CC BY 4.0"]);
    rows.push(["method", areaLabel, "population coverage", "Raster cells assigned to existing 3 km straight-line health-access classes; not network travel or school access"]);
    rows.push(["source", areaLabel, "health baseline", "Existing MOH facility dataset used to derive the 3 km access grid"]);
    rows.push(["source", areaLabel, "2026 health reference", "ArcGIS Malawi Health 2026 FeatureServer; separate comparison layer; license/provenance requires confirmation"]);
    rows.push([
      "source",
      areaLabel,
      "building footprints",
      siteLayers.buildings.available
        ? `${siteLayers.buildings.source}; ${siteLayers.buildings.date}; ${siteLayers.buildings.license}`
        : "Unavailable; occupancy not screened",
    ]);
    schoolCoverage.forEach(([threshold, count]) => {
      rows.push(["school coverage", areaLabel, `settlements within ${threshold} km`, `${count}/${active.settlements.length}`]);
    });

    state.scenarios.forEach((candidate) => {
      const label = `candidate ${candidate.id}`;
      const metrics = getScenarioMetrics(active, candidate);
      rows.push(["scenario", areaLabel, `${label} facility type`, candidate.type === "school" ? "School" : "Health centre"]);
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
      const school = nearestSchoolBySettlement.get(settlement);
      const facility = nearestFacilityBySettlement.get(settlement);
      rows.push([
        "settlement", areaLabel, "nearest-service distances", "straight-line km",
        settlement.properties.name,
        settlement.properties.adm3_name,
        school?.school.properties.name || "",
        school?.distance.toFixed(2) || "",
        facility?.facility.name || "",
        facility?.distance.toFixed(2) || "",
      ]);
    });

    const csv = `\ufeff${rows.map((row) => row.map(csvField).join(",")).join("\r\n")}`;
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

  function planningPrompt(properties) {
    const prompts = [];
    const accessScore = travelAdjustedScore(properties);
    if (properties.dist_km > 8) prompts.push("Assess a new health post or outreach point");
    else if (properties.dist_km > 5) prompts.push("Review outreach or mobile-clinic coverage");
    if (properties.road_km < 1.5) prompts.push("Assess feeder-road access");
    if (properties.water_km < 0.5) prompts.push("Verify local water-point access");
    if (accessScore < 50) prompts.push(`Travel-adjusted proxy is weak (${accessScore}/100)`);
    return prompts.length ? prompts.join("; ") : "Review service quality and staffing";
  }

  function renderGapAnalysisSummary(result) {
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
    document.getElementById("legend-served").textContent =
      `Served (≤ ${result.served_km} km)`;
    document.getElementById("legend-underserved").textContent =
      `Underserved (> ${result.served_km} to ≤ ${result.underserved_km} km)`;
    document.getElementById("legend-unserved").textContent =
      `Unserved (> ${result.underserved_km} km)`;
    gapAnalysisResults.replaceChildren();
    ["served", "underserved", "unserved"].forEach((category) => {
      const values = result.coverage[category];
      const row = document.createElement("div");
      row.className = "gap-analysis-row";
      const label = document.createElement("span");
      label.textContent = category;
      const summary = document.createElement("strong");
      summary.textContent =
        `${values.cells.toLocaleString()} cells · ${Number(values.km2).toLocaleString()} km²`;
      row.append(label, summary);
      gapAnalysisResults.appendChild(row);
    });
  }

  function renderCellCoverage(activeCells) {
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
        const coverage =
          state.gapCoverage.get(Number(p.cell_id)) || p.coverage;
        layer.bindPopup(
          `<strong>Cell ${escapeHtml(p.cell_id)}</strong><br>
           Health access (${state.gapServedKm}/${state.gapUnderservedKm} km scenario): ${escapeHtml(coverage)}<br>
           Nearest facility: ${p.dist_km == null ? "Unavailable" : `${escapeHtml(p.dist_km)} km`}<br>
           Road length: ${escapeHtml(p.road_km)} km<br>
           Mapped waterway length: ${escapeHtml(p.water_km)} km<br>
           Baseline screening score: ${escapeHtml(p.priority_score)}`
        );
      },
    }).addTo(layers.cells);
  }

  function render(active) {
    Object.values(layers).forEach((layer) => {
      if (layer instanceof L.LayerGroup) layer.clearLayers();
    });
    selectedBoundary.clearLayers();

    if (state.areas.length) {
      selectedBoundary.addData({ type: "FeatureCollection", features: state.areas });
    }

    renderCellCoverage(active.cells);

    L.geoJSON(active.roads, {
      style: (feature) => ({
        color: "#d4d0c8",
        weight: roadWeight(feature.properties.highway),
        opacity: 0.75,
      }),
    }).addTo(layers.roads);

    L.geoJSON(active.health2026, {
      pointToLayer: (_, latlng) => L.marker(
        latlng,
        { icon: mapSymbol("diamond", "#8bd4c4", "#176a7b", 11) }
      ),
      onEachFeature: (feature, layer) => {
        const p = feature.properties;
        layer.bindPopup(
          `<strong>${escapeHtml(p.name)}</strong><br>ArcGIS Malawi Health 2026 reference<br>${escapeHtml(p.ownership)}<br>${escapeHtml(p.dho)}`
        );
      },
    }).addTo(layers.health2026);

    L.geoJSON(active.waterways, {
      style: { color: "#51b5d5", weight: 1.6, opacity: 0.9 },
    }).addTo(layers.water);

    active.facilities.forEach((facility) => {
      L.marker([facility.lat, facility.lon], {
        icon: mapSymbol("circle", "#fff", "#cf5c36", facility.facility_type === "Hospital" ? 14 : 10),
      })
        .bindPopup(`<strong>${escapeHtml(facility.name)}</strong><br>${escapeHtml(facility.facility_type)}`)
        .addTo(layers.facilities);

      L.circle([facility.lat, facility.lon], {
        radius: 5000,
        color: "#2a9d8f",
        weight: 1,
        fill: false,
        dashArray: "4 4",
      }).addTo(layers.buffers);
    });

    active.schools.forEach((school) => {
      const [lon, lat] = school.geometry.coordinates;
      L.marker([lat, lon], {
        icon: mapSymbol("square", "#f4a261", "#873e2e", 8),
      })
        .bindPopup(`<strong>${escapeHtml(school.properties.name)}</strong><br>School<br>Status: ${escapeHtml(school.properties.status || "Not recorded")}`)
        .addTo(layers.schools);
    });

    active.settlements.forEach((settlement) => {
      const [lon, lat] = settlement.geometry.coordinates;
      const population = settlement.properties.population;
      const nearest = nearestSchoolBySettlement.get(settlement);
      const withinSchoolRadius = nearest && nearest.distance <= schoolRadiusKm;
      const status = withinSchoolRadius ? "Within" : "Beyond";
      const nearestSchoolInfo = nearest
        ? `Nearest school: ${escapeHtml(nearest.school.properties.name)} (${nearest.distance.toFixed(1)} km straight-line)`
        : "No Dedza school point available";
      L.marker([lat, lon], {
        icon: mapSymbol(
          "circle",
          withinSchoolRadius ? "#8bd4c4" : "#e76f51",
          withinSchoolRadius ? "#174b43" : "#7b2c25",
          9
        ),
      })
        .bindPopup(`<strong>${escapeHtml(settlement.properties.name)}</strong><br>${escapeHtml(settlement.properties.place || "Settlement")}<br>${escapeHtml(settlement.properties.adm3_name)}<br>${nearestSchoolInfo}<br>${status} ${schoolRadiusKm} km radius${population ? `<br>OSM population tag: ${escapeHtml(population)}` : ""}`)
        .addTo(layers.settlements);
    });

    const topCells = [...active.cells]
      .sort((a, b) => b.properties.priority_score - a.properties.priority_score)
      .slice(0, 12);
    const recBody = document.getElementById("recs");
    recBody.replaceChildren();
    topCells.forEach((cell, index) => {
      const p = cell.properties;
      const point = turf.pointOnFeature(cell).geometry.coordinates;
      const accessScore = travelAdjustedScore(p);
      L.circleMarker([point[1], point[0]], {
        radius: 7,
        color: "#fff",
        weight: 1.5,
        fillColor: "#c44536",
        fillOpacity: 0.95,
      })
        .bindPopup(`<strong>Screening cell ${escapeHtml(p.cell_id)}</strong><br>${escapeHtml(planningPrompt(p))}<br>Road-distance access proxy: ${escapeHtml(accessScore)}/100`)
        .addTo(layers.priority);

      const row = document.createElement("tr");
      row.className = "rec-row";
      row.innerHTML = `
        <td>${index + 1}</td>
        <td><span class="tag ${escapeHtml(p.coverage)}">${escapeHtml(p.coverage)}</span></td>
        <td>${escapeHtml(p.dist_km)} km</td>
        <td>${escapeHtml(p.road_km)} km</td>
        <td>${escapeHtml(p.water_km)} km</td>
        <td><span class="tag ${travelAdjustedLabel(accessScore)}">${accessScore}/100</span></td>
        <td>${escapeHtml(planningPrompt(p))}</td>`;
      row.addEventListener("click", () => map.setView([point[1], point[0]], 12));
      recBody.appendChild(row);
    });

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
      .map((settlement) => nearestSchoolBySettlement.get(settlement)?.distance)
      .filter((distance) => Number.isFinite(distance));
    const healthDistances = active.settlements
      .map((settlement) => nearestFacilityBySettlement.get(settlement)?.distance)
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
    const population = populationByArea.get(areaName) || {};
    const populationTotal = population.total || 0;
    const populationUnserved = population.unserved || 0;
    const populationUnservedShare = populationTotal
      ? populationUnserved / populationTotal * 100
      : 0;
    const priorityCells = [...active.cells]
      .filter((cell) => cell.properties.coverage === "unserved")
      .sort((a, b) => b.properties.priority_score - a.properties.priority_score)
      .slice(0, 3);
    const schoolReach = active.settlements.filter(
      (settlement) => (nearestSchoolBySettlement.get(settlement)?.distance ?? Infinity) <= schoolRadiusKm
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
      kpi("Health facilities · 2026 reference", active.health2026.features.length),
      kpi("Schools", active.schools.length),
      kpi("Settlements", active.settlements.length),
      kpi(`Settlements within ${schoolRadiusKm} km of a school`, `${withinSchoolRadius}/${schoolDistances.length}`),
      kpi("Mean nearest-school distance", `${meanSchoolDistance.toFixed(1)} km`),
      kpi("Settlements > 5 km from health", `${beyondHealthRadius}/${healthDistances.length}`),
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
    areaStatus.textContent = `${areaLabel} · ${active.schools.length} schools · ${active.facilities.length} baseline / ${active.health2026.features.length} separate 2026 health references · ${withinSchoolRadius}/${schoolDistances.length} settlements within ${schoolRadiusKm} km of school · ${beyondHealthRadius}/${healthDistances.length} settlements > 5 km from baseline health`;
    document.getElementById("school-access-status").textContent = `Updated: ${withinSchoolRadius} of ${schoolDistances.length} mapped settlements are within ${schoolRadiusKm} km of a school; settlement dots show within/beyond this threshold.`;
    renderScenario(active);
  }

  function activeData() {
    const inArea = (feature) => intersectsSelectedArea(feature);
    return {
      cells: cells.features.filter(inArea),
      facilities: selectedFacilities(),
      health2026: { type: "FeatureCollection", features: selectedFacilities2026() },
      schools: selectedSchools(),
      settlements: selectedSettlements(),
      roads: { type: "FeatureCollection", features: roads.features.filter(inArea) },
      waterways: { type: "FeatureCollection", features: waterways.features.filter(inArea) },
    };
  }

  let routeLoading = false;
  let routeChoices = [];

  function updateFindRouteButton() {
    findRouteButton.disabled = routeLoading
      || routeSettlementSelect.value === ""
      || routeFacilitySelect.value === "";
  }

  function routeChoiceDetails(choice, index) {
    const details = `${choice.network_distance_km.toFixed(1)} km on roads · ${choice.total_distance_km.toFixed(1)} km total · about ${formatEstimatedRouteTime(choice.estimated_network_time_min)}`;
    if (index === 0 || !routeChoices[0]) return details;

    const shortest = routeChoices[0];
    const distanceDifference = choice.network_distance_km - shortest.network_distance_km;
    const timeDifference = Math.round(choice.estimated_network_time_min / 5) * 5
      - Math.round(shortest.estimated_network_time_min / 5) * 5;
    const distanceComparison = Math.abs(distanceDifference) < 0.05
      ? "same distance"
      : `${Math.abs(distanceDifference).toFixed(1)} km ${distanceDifference < 0 ? "shorter" : "longer"}`;
    const timeComparison = Math.abs(timeDifference) < 1
      ? "similar time"
      : `${formatEstimatedRouteTime(Math.abs(timeDifference))} ${timeDifference < 0 ? "faster" : "slower"}`;
    const sharedPercent = sharedRoadPercentage(choice, shortest);
    return `${details} · ${distanceComparison}, ${timeComparison} vs shortest · shares ${sharedPercent}% of its road path`;
  }

  function roadSegmentLengths(choice) {
    const road = choice.route.features.find((feature) => feature.properties.kind === "road");
    const segments = new Map();
    if (!road) return segments;
    const coordinates = road.geometry.coordinates;
    for (let index = 1; index < coordinates.length; index += 1) {
      const start = coordinates[index - 1];
      const end = coordinates[index];
      const startKey = `${start[0]},${start[1]}`;
      const endKey = `${end[0]},${end[1]}`;
      const key = startKey < endKey ? `${startKey}|${endKey}` : `${endKey}|${startKey}`;
      segments.set(key, turf.distance(turf.point(start), turf.point(end), { units: "kilometers" }));
    }
    return segments;
  }

  function sharedRoadPercentage(choice, shortest) {
    const shortestSegments = roadSegmentLengths(shortest);
    const choiceSegments = roadSegmentLengths(choice);
    let sharedDistance = 0;
    shortestSegments.forEach((distance, segment) => {
      if (choiceSegments.has(segment)) sharedDistance += distance;
    });
    return shortest.network_distance_km > 0
      ? Math.min(100, Math.round((sharedDistance / shortest.network_distance_km) * 100))
      : 100;
  }

  function clearRouteMetrics() {
    routeRoadDistance.textContent = "—";
    routeTotalDistance.textContent = "—";
    routeTravelTime.textContent = "—";
    routeConnectors.textContent = "—";
  }

  function updateSelectedRouteStatus(index, settlement, facility) {
    const choice = routeChoices[index];
    if (!choice) return;
    const connectorKm = (choice.origin_snap_m + choice.destination_snap_m) / 1000;
    const estimatedTime = formatEstimatedRouteTime(choice.estimated_network_time_min);
    routeRoadDistance.textContent = `${choice.network_distance_km.toFixed(1)} km`;
    routeTotalDistance.textContent = `${choice.total_distance_km.toFixed(1)} km`;
    routeTravelTime.textContent = `~${estimatedTime}`;
    routeConnectors.textContent = `${connectorKm.toFixed(2)} km`;
    routeStatus.textContent = `${choice.label}: ${settlement.properties.name} to ${facility.name}. Approximate time; excludes traffic, road conditions, and access time.`;
  }

  function clearRoute() {
    routeLayer.clearLayers();
    routeChoices = [];
    routeOptionsContainer.replaceChildren();
    routeResultsPanel.hidden = true;
    clearRouteButton.disabled = true;
    routeControlStatus.textContent = "Choose a settlement and facility to estimate a route.";
    routeStatus.textContent = "Choose a settlement and facility to estimate a route.";
    clearRouteMetrics();
  }

  function formatEstimatedRouteTime(minutes) {
    const roundedMinutes = Math.round(minutes / 5) * 5;
    if (roundedMinutes >= 60) {
      return `${Math.floor(roundedMinutes / 60)} hr ${roundedMinutes % 60} min`;
    }
    return roundedMinutes < 5 ? "under 5 min" : `${roundedMinutes} min`;
  }

  function displayRouteChoice(index, settlement, facility) {
    const choice = routeChoices[index];
    if (!choice) return;
    routeLayer.clearLayers();
    const drawRoute = (routeIndex, selected) => {
      L.geoJSON(routeChoices[routeIndex].route, {
        filter: (feature) => feature.properties.kind === "road"
          || (selected && feature.properties.kind === "connector"),
        style: (feature) => feature.properties.kind === "connector"
          ? { color: "#f4d8ff", weight: 3, dashArray: "5 6" }
          : {
            color: routeColors[routeIndex % routeColors.length],
            weight: selected ? 6 : 3,
            opacity: selected ? 1 : 0.72,
            dashArray: selected ? null : "7 5",
          },
      }).addTo(routeLayer);
    };
    routeChoices.forEach((_, routeIndex) => {
      if (routeIndex !== index) drawRoute(routeIndex, false);
    });
    drawRoute(index, true);
    const endpoints = choice.route.features.filter(
      (feature) => feature.properties.kind === "origin"
        || feature.properties.kind === "destination",
    );
    L.geoJSON({ type: "FeatureCollection", features: endpoints }, {
      pointToLayer: (feature, latlng) => {
        const isOrigin = feature.properties.kind === "origin";
        return L.circleMarker(latlng, {
          radius: 8,
          color: isOrigin ? "#174b43" : "#7b2c25",
          weight: 2,
          fillColor: isOrigin ? "#8bd4c4" : "#e76f51",
          fillOpacity: 1,
        }).bindPopup(isOrigin ? "Route starts here" : "Route ends here");
      },
    }).addTo(routeLayer);
    map.fitBounds(routeLayer.getBounds().pad(0.12));
    [...routeOptionsContainer.children].forEach((button, buttonIndex) => {
      button.setAttribute("aria-pressed", String(buttonIndex === index));
    });
    updateSelectedRouteStatus(index, settlement, facility);
  }

  async function findRoute() {
    const settlement = settlements.features[Number(routeSettlementSelect.value)];
    const facility = routeFacilities[Number(routeFacilitySelect.value)];
    if (!settlement || !facility) return;

    routeLoading = true;
    updateFindRouteButton();
    routeSettlementSelect.disabled = true;
    routeFacilitySelect.disabled = true;
    clearRouteButton.disabled = true;
    routeLayer.clearLayers();
    routeResultsPanel.hidden = true;
    routeOptionsContainer.replaceChildren();
    clearRouteMetrics();
    routeControlStatus.textContent = "Calculating the shortest mapped-road route. The first request may take several seconds to prepare the road network.";
    findRouteButton.textContent = "Finding route...";
    try {
      const response = await fetch("/api/route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          origin: settlement.geometry.coordinates,
          destination: [facility.lon, facility.lat],
        }),
      });
      let payload;
      try {
        payload = await response.json();
      } catch {
        throw new Error(`The route service returned an invalid response (${response.status}).`);
      }
      if (!response.ok) throw new Error(payload.error || `Route request failed (${response.status}).`);

      routeChoices = payload.alternatives || [payload];
      routeOptionsContainer.replaceChildren();
      routeChoices.forEach((choice, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "route-option";
        button.style.setProperty("--route-color", routeColors[index % routeColors.length]);
        button.setAttribute("aria-pressed", "false");
        const title = document.createElement("strong");
        title.textContent = choice.label;
        const details = document.createElement("span");
        details.textContent = routeChoiceDetails(choice, index);
        button.append(title, details);
        button.addEventListener("click", () => displayRouteChoice(index, settlement, facility));
        routeOptionsContainer.append(button);
      });
      displayRouteChoice(0, settlement, facility);
      routeResultsPanel.hidden = false;
      routeControlStatus.textContent = "";
      clearRouteButton.disabled = false;
    } catch (error) {
      routeLayer.clearLayers();
      routeChoices = [];
      routeOptionsContainer.replaceChildren();
      routeResultsPanel.hidden = true;
      routeControlStatus.textContent = `Route unavailable: ${error.message}`;
      clearRouteButton.disabled = false;
    } finally {
      routeLoading = false;
      routeSettlementSelect.disabled = false;
      routeFacilitySelect.disabled = false;
      findRouteButton.textContent = "Find route";
      updateFindRouteButton();
    }
  }

  function selectTA(name) {
    const areas = taByName.get(name.toLocaleLowerCase());
    if (!areas) return false;
    state.name = areas[0].properties.name;
    state.areas = areas;
    state.scenarios = [];
    state.nextScenarioId = 1;
    state.placingScenario = false;
    state.scenarioResultsCollapsed = false;
    placeScenarioButton.textContent = "Add site on map";
    closeTAOptions();
    districtButton.setAttribute("aria-pressed", "false");
    taSearch.value = state.name;
    clearTASearch.hidden = false;
    const areaLayer = L.geoJSON({ type: "FeatureCollection", features: areas });
    map.fitBounds(areaLayer.getBounds().pad(0.12));
    render(activeData());
    return true;
  }

  function selectDistrict() {
    state.name = null;
    state.areas = [];
    taSearch.value = "";
    clearTASearch.hidden = true;
    closeTAOptions();
    districtButton.setAttribute("aria-pressed", "true");
    map.fitBounds(districtLayer.getBounds().pad(0.04));
    render(activeData());
  }

  taSearch.addEventListener("input", () => {
    const value = taSearch.value.trim();
    clearTASearch.hidden = value.length === 0;
    if (!value) {
      selectDistrict();
    } else {
      const exactName = taNames.find((name) => name.toLocaleLowerCase() === value.toLocaleLowerCase());
      if (exactName) {
        selectTA(exactName);
      } else {
        const matches = showTAOptions(value);
        areaStatus.textContent = matches
          ? `${matches} Traditional Authorities match. Select one to filter the map.`
          : "No Traditional Authorities match that search.";
      }
    }
  });
  taSearch.addEventListener("focus", () => {
    showTAOptions(taSearch.value.trim());
  });
  taSearch.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeTAOptions();
      return;
    }
    if (event.key === "ArrowDown" && !taOptions.hidden) {
      event.preventDefault();
      taOptions.querySelector(".ta-option")?.focus();
    }
  });
  clearTASearch.addEventListener("click", () => {
    selectDistrict();
    taSearch.focus();
  });
  taOptionsToggle.addEventListener("click", () => {
    if (taOptions.hidden) showTAOptions(taSearch.value.trim());
    else closeTAOptions();
  });
  districtButton.addEventListener("click", selectDistrict);
  const routeSelectionChanged = () => {
    routeLayer.clearLayers();
    routeChoices = [];
    routeOptionsContainer.replaceChildren();
    routeResultsPanel.hidden = true;
    clearRouteButton.disabled = true;
    clearRouteMetrics();
    routeControlStatus.textContent = routeSettlementSelect.value && routeFacilitySelect.value
      ? "Route choices changed. Click Find route to update the map."
      : "Choose a settlement and facility to estimate a route.";
    routeStatus.textContent = routeControlStatus.textContent;
    updateFindRouteButton();
  };
  routeSettlementSelect.addEventListener("change", routeSelectionChanged);
  routeFacilitySelect.addEventListener("change", routeSelectionChanged);
  findRouteButton.addEventListener("click", findRoute);
  clearRouteButton.addEventListener("click", clearRoute);
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".area-control")) closeTAOptions();
  });
  placeScenarioButton.addEventListener("click", () => {
    state.placingScenario = !state.placingScenario;
    placeScenarioButton.textContent = state.placingScenario
      ? "Cancel placement"
      : state.scenarios.length ? "Add another site" : "Add site on map";
    if (state.placingScenario) {
      showScenarioNotice(
        "Place candidate site",
        "Click inside your selected area to place a site. Detected buildings block placement.",
        "warning",
        false
      );
    } else {
      scenarioNotice.hidden = true;
    }
    render(activeData());
  });
  scenarioResultsToggle.addEventListener("click", () => {
    state.scenarioResultsCollapsed = !state.scenarioResultsCollapsed;
    renderScenario(activeData());
  });
  clearScenarioButton.addEventListener("click", () => {
    const clearedCandidates = state.scenarios.length > 0;
    state.siteScreeningRequestId += 1;
    state.siteScreeningPending = false;
    clearTimeout(scenarioNoticeTimer);
    scenarioNotice.hidden = true;
    state.scenarios = [];
    state.nextScenarioId = 1;
    state.placingScenario = false;
    state.scenarioResultsCollapsed = false;
    placeScenarioButton.textContent = "Add site on map";
    render(activeData());
    scenarioStatus.textContent = clearedCandidates
      ? "Candidate sites cleared from the map."
      : "Placement cancelled.";
  });
  document.getElementById("print-brief").addEventListener("click", () => window.print());
  scenarioRadiusInput.addEventListener("change", () => {
    const radius = Number(scenarioRadiusInput.value);
    if (!Number.isFinite(radius) || radius < 1 || radius > 50) {
      scenarioRadiusInput.value = String(state.scenarios[0]?.radius || 5);
      return;
    }
    if (state.scenarios.length) {
      refreshScenarioEstimates();
    }
  });
  scenarioTypeInput.addEventListener("change", () => {
    if (state.scenarios.length) {
      refreshScenarioEstimates();
    }
  });
  map.getContainer().addEventListener("click", async (event) => {
    if (event.target instanceof Element && event.target.closest(".map-scenario-results, .map-route-results")) return;
    if (!state.placingScenario || state.siteScreeningPending) return;
    L.DomEvent.stopPropagation(event);
    L.DomEvent.preventDefault(event);
    const latlng = map.mouseEventToLatLng(event);
    const point = turf.point([latlng.lng, latlng.lat]);
    const inScope = state.areas.length
      ? state.areas.some((area) => turf.booleanPointInPolygon(point, area))
      : boundary.features.some((feature) => turf.booleanPointInPolygon(point, feature));
    if (!inScope) {
      const message = "Place the proposed facility inside the selected district or Traditional Authority.";
      scenarioStatus.textContent = message;
      showScenarioNotice("Candidate outside selected area", message, "error", true);
      return;
    }
    const radius = Number(scenarioRadiusInput.value);
    if (!Number.isFinite(radius) || radius < 1 || radius > 50) {
      const message = "Enter a scenario radius between 1 and 50 km.";
      scenarioStatus.textContent = message;
      showScenarioNotice("Invalid radius", message, "error", true);
      return;
    }
    const checkingMessage = "Checking for detected building footprints...";
    scenarioStatus.textContent = checkingMessage;
    showScenarioNotice("Screening candidate site", checkingMessage, "warning", false);
    const requestId = ++state.siteScreeningRequestId;
    state.siteScreeningPending = true;
    let siteScreening;
    try {
      siteScreening = await screenCandidateSite(point.geometry.coordinates);
    } catch (error) {
      if (requestId === state.siteScreeningRequestId) {
        scenarioStatus.textContent = `Could not screen candidate site: ${error.message}`;
        showScenarioNotice("Screening unavailable", `Could not screen candidate site: ${error.message}`, "error", true);
      }
      return;
    } finally {
      if (requestId === state.siteScreeningRequestId) {
        state.siteScreeningPending = false;
      }
    }
    if (requestId !== state.siteScreeningRequestId || !state.placingScenario) return;
    if (siteScreening.occupied) {
      const message = "Not added: the candidate point overlaps a detected building footprint. Choose another point.";
      scenarioStatus.textContent = message;
      showScenarioNotice("Building occupies this site", message, "error", true);
      return;
    }
    const candidate = {
      id: state.nextScenarioId++,
      point,
      type: scenarioTypeInput.value,
      radius,
      siteScreening,
      loading: true,
    };
    state.scenarios.push(candidate);
    state.placingScenario = false;
    state.scenarioResultsCollapsed = false;
    placeScenarioButton.textContent = "Add another site";
    const successMessage = siteScreening.buildings_available
      ? "Candidate added. Review the estimates and verify the site in the field."
      : "Candidate added. Building data is unavailable, so occupancy was not screened.";
    scenarioStatus.textContent = successMessage;
    showScenarioNotice(
      siteScreening.buildings_available ? "Candidate accepted for analysis" : "Candidate added with limited screening",
      successMessage,
      siteScreening.buildings_available ? "success" : "warning",
      true
    );
    render(activeData());
    if (window.matchMedia("(max-width: 900px)").matches) {
      map.getContainer().scrollIntoView({
        block: "start",
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      });
    }
    estimateScenarioCandidate(candidate);
  }, true);
  document.getElementById("export-brief").addEventListener("click", exportPlanningBrief);
  let gapAnalysisTimer = null;
  const scheduleGapAnalysis = () => {
    const servedKm = Number(gapServedInput.value);
    gapUnderservedInput.min = String(servedKm + 1);
    if (Number(gapUnderservedInput.value) <= servedKm) {
      gapUnderservedInput.value = String(servedKm + 1);
    }
    gapServedValue.textContent = `${servedKm} km`;
    gapUnderservedValue.textContent = `${gapUnderservedInput.value} km`;
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
      } catch (error) {
        if (requestId === state.gapAnalysisRequestId) {
          gapAnalysisResults.textContent = `Could not recalculate coverage: ${error.message}`;
        }
      }
    }, 180);
  };
  gapServedInput.addEventListener("input", scheduleGapAnalysis);
  gapUnderservedInput.addEventListener("input", scheduleGapAnalysis);
  schoolRadiusInput.addEventListener("change", () => {
    const value = Number(schoolRadiusInput.value);
    if (!Number.isFinite(value) || value < 1 || value > 50) {
      schoolRadiusInput.value = String(schoolRadiusKm);
      return;
    }
    schoolRadiusKm = value;
    render(activeData());
  });

  document.getElementById("sources").textContent = `${summary.meta.sources}; ArcGIS Malawi Health 2026 FeatureServer (separate reference layer); WorldPop 2025 R2025A v1 (CC BY 4.0)`;
  renderTAComparison(buildTAComparison());
  selectDistrict();
  if (!baseTilesFailed) mapStatus.hidden = true;

  document.querySelectorAll(".analysis-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".analysis-tab").forEach((item) => {
        const selected = item === tab;
        item.classList.toggle("active", selected);
        item.setAttribute("aria-selected", String(selected));
      });
      document.querySelectorAll(".analysis-panel").forEach((panel) => {
        panel.hidden = panel.id !== tab.dataset.panel;
        panel.classList.toggle("active", !panel.hidden);
      });
    });
  });

  const bind = (id, group) => {
    document.getElementById(id).addEventListener("change", (ev) => {
      if (ev.target.checked) map.addLayer(group);
      else map.removeLayer(group);
    });
  };
  bind("lyr-cells", layers.cells);
  bind("lyr-facilities", layers.facilities);
  bind("lyr-health2026", layers.health2026);
  bind("lyr-schools", layers.schools);
  bind("lyr-settlements", layers.settlements);
  bind("lyr-buffers", layers.buffers);
  bind("lyr-roads", layers.roads);
  bind("lyr-water", layers.water);
  bind("lyr-priority", layers.priority);
  bind("lyr-buildings", layers.buildings);
  bind("lyr-slope", layers.slope);
  bind("lyr-land-cover", layers.landCover);
  buildingToggle.addEventListener("change", loadBuildingFeatures);
  map.on("moveend", scheduleBuildingLoad);
}

init().catch((err) => {
  mapStatus.hidden = false;
  mapStatus.textContent = `Could not load the Dedza planning layers: ${err.message}`;
  document.getElementById("kpis").innerHTML =
    `<p>Could not load the Dedza planning layers: ${escapeHtml(err.message)}</p>`;
});
}
