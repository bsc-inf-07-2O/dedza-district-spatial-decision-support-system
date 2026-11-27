// The Leaflet map: base tiles, overlay layer groups, on-map legend and symbols.
// Created when this module is first imported (after Leaflet has loaded).

export const mapStatus = document.getElementById("map-status");

// Fractional zoom lets fitBounds fill the map with the district instead of
// dropping a whole zoom level when the bounds are slightly too tall.
export const map = L.map("map", { zoomControl: true, zoomSnap: 0.25, zoomDelta: 0.5 })
  .setView([-14.38, 34.33], 10);

// ---- Base map tiles ----
let tilesFailed = false;
export const baseTilesFailed = () => tilesFailed;
// OSM's own guidance is the single tile.openstreetmap.org host (no a/b/c subdomains).
const baseMap = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
});
// An occasional failed tile is normal (busy tile server, brief network drop),
// so retry each tile up to twice and only warn when tiles are really missing.
// The warning clears itself as soon as tiles load again, and can be clicked away.
const TILE_RETRIES = 2;
let tileWarningTimer = null;
const showTileWarning = (message) => {
  tilesFailed = true;
  mapStatus.hidden = false;
  mapStatus.classList.add("dismissible");
  mapStatus.title = "Click to dismiss";
  mapStatus.textContent = message;
};
const clearTileWarning = () => {
  clearTimeout(tileWarningTimer);
  if (!tilesFailed) return;
  tilesFailed = false;
  mapStatus.hidden = true;
  mapStatus.classList.remove("dismissible");
  mapStatus.removeAttribute("title");
};
mapStatus.addEventListener("click", () => {
  if (mapStatus.classList.contains("dismissible")) clearTileWarning();
});
baseMap.on("tileerror", ({ tile, coords }) => {
  const attempt = Number(tile.dataset.retry || 0);
  if (attempt < TILE_RETRIES) {
    tile.dataset.retry = String(attempt + 1);
    setTimeout(() => {
      tile.src = `${baseMap.getTileUrl(coords)}?retry=${attempt + 1}`;
    }, 800 * (attempt + 1));
    return;
  }
  clearTimeout(tileWarningTimer);
  tileWarningTimer = setTimeout(() => {
    showTileWarning(navigator.onLine === false
      ? "You appear to be offline, so the background map is unavailable. All planning layers and analysis still work."
      : "Some background map tiles could not be loaded. Pan or zoom to retry; planning layers and analysis still work.");
    // A partial failure is not worth a permanent banner.
    if (navigator.onLine !== false) tileWarningTimer = setTimeout(clearTileWarning, 6000);
  }, 1500);
});
baseMap.on("tileload", () => {
  if (tilesFailed) clearTileWarning();
});
window.addEventListener("online", () => {
  clearTileWarning();
  baseMap.redraw();
});
window.addEventListener("offline", () => showTileWarning(
  "You appear to be offline, so the background map is unavailable. All planning layers and analysis still work."
));
baseMap.addTo(map);

// ---- Overlay layers (switched on and off in the Layers tool) ----
export const layers = {
  cells: L.layerGroup().addTo(map),
  facilities: L.layerGroup().addTo(map),
  // Off by default to keep the opening map readable; toggle under "Map layers".
  registry: L.layerGroup(),
  schools: L.layerGroup(),
  settlements: L.layerGroup(),
  buffers: L.layerGroup(),
  roads: L.layerGroup(),
  water: L.layerGroup(),
  priority: L.layerGroup().addTo(map),
  scenario: L.layerGroup().addTo(map),
  buildings: L.layerGroup(),
  slope: L.tileLayer("/api/environmental-tiles/slope/{z}/{x}/{y}.png", {
    maxNativeZoom: 12,
    maxZoom: 18,
    opacity: 0.62,
    attribution: "Mapzen Terrain Tiles; SRTM/GMTED2010 data courtesy of USGS",
  }),
  flood: L.tileLayer("/api/environmental-tiles/flood/{z}/{x}/{y}.png", {
    maxNativeZoom: 14,
    maxZoom: 18,
    opacity: 0.75,
    attribution: "JRC Copernicus EMS GloFAS flood hazard map v2.1 (ODbL)",
  }),
  landCover: L.tileLayer("/api/environmental-tiles/land_cover/{z}/{x}/{y}.png", {
    maxNativeZoom: 15,
    maxZoom: 18,
    opacity: 0.62,
    attribution: "© ESA WorldCover 2021 / Contains modified Copernicus Sentinel data 2021",
  }),
};

// ---- On-map legend: lists only the layers that are on the map ----
const mapLegend = L.control({ position: "bottomright" });
mapLegend.onAdd = () => {
  const div = L.DomUtil.create("div", "map-legend");
  // Each entry carries data-layer; updateMapLegend() shows only entries whose
  // layer is on the map, and hides a section when none of its entries are.
  // The title is a button that collapses the legend; on phones it starts
  // collapsed so the legend does not cover most of the small map.
  div.innerHTML = `
    <button type="button" class="map-legend-title" aria-expanded="true" aria-controls="map-legend-body">Map legend</button>
    <div id="map-legend-body" class="map-legend-body">
    <div class="map-legend-section" data-section="health">
      <span class="map-legend-heading">Health access</span>
      <ul class="legend">
        <li data-layer="cells"><span class="swatch served"></span> <span id="map-legend-served">Served (≤ 5 km)</span></li>
        <li data-layer="cells"><span class="swatch underserved"></span> <span id="map-legend-underserved">Underserved (5–8 km)</span></li>
        <li data-layer="cells"><span class="swatch unserved"></span> <span id="map-legend-unserved">Unserved (&gt; 8 km)</span></li>
        <li data-layer="facilities"><span class="swatch facility"></span> Health facility</li>
        <li data-layer="priority"><span class="priority-rank legend-rank">1</span> Priority cell (rank)</li>
      </ul>
    </div>
    <div class="map-legend-section" data-section="other">
      <span class="map-legend-heading">Other layers</span>
      <ul class="legend">
        <li data-layer="registry"><span class="swatch facility-registry"></span> MHFR registry facility</li>
        <li data-layer="buffers"><span class="swatch buffer-ring"></span> Within 5 km walk of a health facility</li>
        <li data-layer="schools"><span class="swatch school"></span> School</li>
        <li data-layer="settlements"><span class="swatch settlement-in"></span> Settlement within school radius</li>
        <li data-layer="settlements"><span class="swatch settlement-out"></span> Settlement beyond school radius</li>
        <li data-layer="roads"><span class="swatch road"></span> Road</li>
        <li data-layer="water"><span class="swatch water"></span> Mapped waterway</li>
        <li data-layer="scenario"><span class="swatch scenario-site"></span> Candidate site and walking reach</li>
        <li data-layer="buildings"><span class="swatch building"></span> Detected building footprint</li>
        <li data-layer="slope"><span class="swatch environmental-slope"></span> Slope: low to high</li>
        <li data-layer="landCover"><span class="swatch environmental-landcover"></span> WorldCover land cover</li>
        <li data-layer="flood"><span class="swatch flood-zone"></span> 1-in-100-year flood zone (deeper = darker)</li>
      </ul>
    </div>
    </div>`;
  const toggle = div.querySelector(".map-legend-title");
  const setExpanded = (expanded) => {
    toggle.setAttribute("aria-expanded", String(expanded));
    div.classList.toggle("collapsed", !expanded);
  };
  toggle.addEventListener("click", () => setExpanded(toggle.getAttribute("aria-expanded") !== "true"));
  setExpanded(!window.matchMedia("(max-width: 600px)").matches);
  L.DomEvent.disableClickPropagation(div);
  // Let the mouse wheel scroll the legend rather than zoom the map.
  L.DomEvent.disableScrollPropagation(div);
  return div;
};
mapLegend.addTo(map);

function layerIsShown(key) {
  const layer = layers[key];
  if (!layer || !map.hasLayer(layer)) return false;
  // Candidate sites are "on" by default but empty until a site is added.
  if (key === "scenario") return layer.getLayers().length > 0;
  return true;
}

function updateMapLegend() {
  const container = mapLegend.getContainer();
  if (!container) return;
  let anyVisible = false;
  container.querySelectorAll(".map-legend-section").forEach((section) => {
    let sectionVisible = false;
    section.querySelectorAll("li[data-layer]").forEach((item) => {
      const shown = layerIsShown(item.dataset.layer);
      item.hidden = !shown;
      sectionVisible = sectionVisible || shown;
    });
    section.hidden = !sectionVisible;
    anyVisible = anyVisible || sectionVisible;
  });
  container.hidden = !anyVisible;
}

// layeradd/layerremove fire for every marker as well as for whole groups, so
// batch the legend refresh into one update per frame.
let legendUpdateQueued = false;
function queueMapLegendUpdate() {
  if (legendUpdateQueued) return;
  legendUpdateQueued = true;
  requestAnimationFrame(() => {
    legendUpdateQueued = false;
    updateMapLegend();
  });
}
map.on("layeradd layerremove", queueMapLegendUpdate);
updateMapLegend();

// ---- Shared drawing layers and colours ----
export const selectedBoundary = L.geoJSON(null, {
  style: { color: "#f4a261", weight: 3, fillColor: "#f4a261", fillOpacity: 0.08 },
}).addTo(map);
export const routeLayer = L.featureGroup().addTo(map);
export const routeColors = ["#c49bff", "#4ea8de", "#f4a261"];
export const scenarioColors = ["#e76f51", "#2a9d8f", "#4ea8de", "#c49bff", "#e9c46a"];

export const coverageColor = {
  served: "#2a9d8f",
  underserved: "#e9c46a",
  unserved: "#e76f51",
};

export function roadWeight(highway) {
  if (["trunk", "primary", "motorway"].includes(highway)) return 2.4;
  if (["secondary", "tertiary"].includes(highway)) return 1.6;
  return 0.8;
}

// Small CSS-drawn point marker (styles: .map-symbol in static/css/map.css).
export function mapSymbol(shape, fill, stroke, size = 12) {
  return L.divIcon({
    className: "map-symbol",
    html: `<span class="${shape}" style="--symbol-fill:${fill};--symbol-stroke:${stroke}"></span>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}
