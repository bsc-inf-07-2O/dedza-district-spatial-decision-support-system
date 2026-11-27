// App shell: tool rail, floating tool panel and the resizable insights drawer.
// The open tool, drawer state and drawer height are remembered in localStorage.
import { map } from "../map/map.js";

const COMPACT_QUERY = window.matchMedia("(max-width: 900px)");
const TOOL_KEY = "dd-sdss-active-tool";
const DECK_KEY = "dd-sdss-insights-collapsed";
const DECK_SIZE_KEY = "dd-sdss-insights-height";
const DECK_MIN = 140;

const workspace = document.querySelector(".workspace");
const toolPanel = document.getElementById("tool-panel");
const toolPanelTitle = document.getElementById("tool-panel-title");
const toolPanelBody = toolPanel.querySelector(".tool-panel-body");
const toolButtons = [...document.querySelectorAll(".tool-button")];
const toolViews = [...document.querySelectorAll("[data-tool-view]")];
const insightsDeck = document.getElementById("insights-deck");
const deckToggle = document.getElementById("toggle-deck");
const deckResizer = document.getElementById("deck-resizer");
let activeTool = null;

function storeSetting(key, value) {
  try { localStorage.setItem(key, value); } catch { /* storage unavailable */ }
}
function readSetting(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

// ---- Tool panel ----

export function openTool(name) {
  const view = toolViews.find((item) => item.dataset.toolView === name);
  if (!view) return;
  const switching = activeTool !== name;
  activeTool = name;
  toolViews.forEach((item) => { item.hidden = item !== view; });
  toolButtons.forEach((button) => {
    const selected = button.dataset.tool === name;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-expanded", String(selected));
  });
  toolPanelTitle.textContent = view.dataset.title;
  toolPanel.hidden = false;
  document.body.classList.add("tool-open");
  if (switching) toolPanelBody.scrollTop = 0;
  storeSetting(TOOL_KEY, name);
}

function closeTool() {
  activeTool = null;
  toolPanel.hidden = true;
  document.body.classList.remove("tool-open");
  toolButtons.forEach((button) => {
    button.classList.remove("active");
    button.setAttribute("aria-expanded", "false");
  });
  storeSetting(TOOL_KEY, "");
}

function setupToolPanel() {
  toolButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (activeTool === button.dataset.tool) closeTool();
      else openTool(button.dataset.tool);
    });
  });
  document.getElementById("close-tool-panel").addEventListener("click", () => {
    const button = toolButtons.find((item) => item.dataset.tool === activeTool);
    closeTool();
    button?.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !activeTool) return;
    if (document.querySelector(".custom-select.is-open")) return; // let the dropdown close first
    const button = toolButtons.find((item) => item.dataset.tool === activeTool);
    closeTool();
    button?.focus();
  });
  // On small screens the panel covers the map, so close it when the next step is a map click.
  ["place-scenario", "suggest-sites", "find-route"].forEach((id) => {
    document.getElementById(id)?.addEventListener("click", () => {
      if (COMPACT_QUERY.matches) closeTool();
    });
  });

  const savedTool = readSetting(TOOL_KEY);
  if (savedTool) openTool(savedTool);
  else if (savedTool === null && !COMPACT_QUERY.matches) openTool("overview");
}

// ---- Insights drawer ----

function setDeckCollapsed(collapsed) {
  insightsDeck.classList.toggle("collapsed", collapsed);
  deckToggle.setAttribute("aria-expanded", String(!collapsed));
  deckToggle.title = collapsed ? "Show the insights drawer" : "Hide the insights drawer";
  storeSetting(DECK_KEY, collapsed ? "1" : "0");
}

const deckMax = () => Math.max(DECK_MIN, Math.round(workspace.getBoundingClientRect().height * 0.7));
function setDeckSize(pixels, remember = true) {
  const size = Math.min(deckMax(), Math.max(DECK_MIN, Math.round(pixels)));
  insightsDeck.style.setProperty("--deck-size", `${size}px`);
  if (remember) storeSetting(DECK_SIZE_KEY, String(size));
}

// The resize handle is an ARIA separator: screen readers need its current,
// smallest and largest drawer height.
function syncResizerValues() {
  const height = Math.round(insightsDeck.getBoundingClientRect().height);
  deckResizer.setAttribute("aria-valuemin", String(DECK_MIN));
  deckResizer.setAttribute("aria-valuemax", String(Math.max(deckMax(), height)));
  deckResizer.setAttribute("aria-valuenow", String(height));
  deckResizer.setAttribute("aria-valuetext", `Insights drawer ${height} pixels high`);
}

function setupDeck() {
  syncResizerValues();
  deckToggle.addEventListener("click", () => setDeckCollapsed(!insightsDeck.classList.contains("collapsed")));
  document.querySelectorAll(".analysis-tab").forEach((tab) => {
    tab.addEventListener("click", () => setDeckCollapsed(false));
  });
  const savedDeck = readSetting(DECK_KEY);
  setDeckCollapsed(savedDeck === null ? COMPACT_QUERY.matches : savedDeck === "1");

  const savedDeckSize = Number(readSetting(DECK_SIZE_KEY));
  if (savedDeckSize > 0) setDeckSize(savedDeckSize, false);
  // Drag the handle to resize; double-click or the arrow keys also work.
  deckResizer.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    if (insightsDeck.classList.contains("collapsed")) setDeckCollapsed(false);
    const bottom = insightsDeck.getBoundingClientRect().bottom;
    insightsDeck.classList.add("resizing");
    deckResizer.setPointerCapture(event.pointerId);
    const move = (moveEvent) => setDeckSize(bottom - moveEvent.clientY);
    const stop = () => {
      insightsDeck.classList.remove("resizing");
      deckResizer.removeEventListener("pointermove", move);
      deckResizer.removeEventListener("pointerup", stop);
      deckResizer.removeEventListener("pointercancel", stop);
    };
    deckResizer.addEventListener("pointermove", move);
    deckResizer.addEventListener("pointerup", stop);
    deckResizer.addEventListener("pointercancel", stop);
  });
  deckResizer.addEventListener("dblclick", () => setDeckCollapsed(!insightsDeck.classList.contains("collapsed")));
  deckResizer.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    if (insightsDeck.classList.contains("collapsed")) setDeckCollapsed(false);
    const current = insightsDeck.getBoundingClientRect().height;
    setDeckSize(current + (event.key === "ArrowUp" ? 40 : -40));
  });
}

export function setupShell() {
  setupToolPanel();
  setupDeck();
  // Keep Leaflet's size in step with the layout, and keep the floating panel
  // clear of the insights drawer.
  if ("ResizeObserver" in window) {
    new ResizeObserver(() => map.invalidateSize({ pan: false })).observe(map.getContainer());
    new ResizeObserver(([entry]) => {
      workspace.style.setProperty("--deck-height", `${Math.round(entry.target.getBoundingClientRect().height)}px`);
      syncResizerValues();
    }).observe(insightsDeck);
  }
}
