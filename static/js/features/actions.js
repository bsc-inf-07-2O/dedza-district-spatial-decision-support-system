// Delegated click handlers: buttons inside map popups and in the briefing's
// recommended actions, plus the insights-drawer tabs.
import { map } from "../map/map.js";
import { state } from "../state.js";
import { showNotice } from "../ui/notice.js";
import { openTool } from "../ui/shell.js";
import { selectTA } from "./area.js";
import { findRoute, routeIsLoading } from "./routes.js";
import { addCandidateAt } from "./sites.js";
import { addSuggestedSites } from "./suggestions.js";

const routeSettlementSelect = document.getElementById("route-settlement");
const routeFacilitySelect = document.getElementById("route-facility");
const findRouteButton = document.getElementById("find-route");
const scenarioResults = document.getElementById("scenario-results");

// Open the tool panel holding `element` and bring it into view.
function revealControl(element) {
  if (!element) return;
  const view = element.closest("[data-tool-view]");
  if (view) openTool(view.dataset.toolView);
  const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  element.scrollIntoView({ block: "center", behavior: smooth ? "smooth" : "auto" });
}

// "Open TA", "Compare top 3 sites" and settlement-name buttons in the briefing.
export function setupBriefingActions() {
  document.getElementById("briefing-actions").addEventListener("click", (event) => {
    const button = event.target instanceof Element && event.target.closest("[data-action]");
    if (!button) return;
    const { action, value } = button.dataset;
    if (action === "select-ta") selectTA(value);
    if (action === "suggest") {
      addSuggestedSites({ type: value, radius: Number(button.dataset.radius) });
      map.getContainer().scrollIntoView({ block: "start", behavior: "smooth" });
    }
    if (action === "zoom") {
      const [lon, lat] = value.split(",").map(Number);
      map.setView([lat, lon], 13);
      map.getContainer().scrollIntoView({ block: "start", behavior: "smooth" });
    }
  });
}

export function setupInsightTabs() {
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
}

export function setupPopupActions() {
  // Keep an opened popup clear of the candidate-results panel on the right.
  // Runs after Leaflet's own auto-pan (about 0.25 s) so the two pans don't fight.
  map.on("popupopen", (event) => {
    if (scenarioResults.hidden) return;
    setTimeout(() => {
      if (!event.popup.isOpen()) return;
      const popupBox = event.popup.getElement()?.getBoundingClientRect();
      const panelBox = scenarioResults.getBoundingClientRect();
      const mapBox = map.getContainer().getBoundingClientRect();
      if (!popupBox) return;
      const overlap = popupBox.right - (panelBox.left - 12);
      const dx = overlap > 0 && popupBox.top < panelBox.bottom ? overlap : 0;
      const dy = Math.min(0, popupBox.top - (mapBox.top + 12));
      if (dx || dy) map.panBy([dx, dy]);
    }, 320);
  });

  // Popup action buttons (built by popupCard). Capture phase so Leaflet cannot swallow the click.
  map.getContainer().addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest(".popup-action") : null;
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    const action = button.dataset;
    if (action.action === "zoom") {
      map.closePopup();
      map.flyTo([Number(action.lat), Number(action.lng)], Math.max(map.getZoom(), Number(action.zoom) || 12));
    } else if (action.action === "route-from" || action.action === "route-to") {
      if (routeIsLoading() || action.index === undefined || action.index === "") return;
      const isFrom = action.action === "route-from";
      const select = isFrom ? routeSettlementSelect : routeFacilitySelect;
      const other = isFrom ? routeFacilitySelect : routeSettlementSelect;
      select.value = action.index;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      map.closePopup();
      const name = select.options[select.selectedIndex]?.textContent.trim() || "";
      if (other.value) {
        revealControl(findRouteButton);
        showNotice("Calculating route", `${isFrom ? "From" : "To"} ${name}. Comparing route options...`, "success", true);
        findRoute();
      } else {
        revealControl(document.getElementById(`${other.id}-button`));
        showNotice(
          isFrom ? "Start point set" : "Destination set",
          `${name}. Now choose the ${isFrom ? "health facility" : "starting settlement"} in the sidebar, or click one on the map and use Route ${isFrom ? "to" : "from"} here.`,
          "success",
          true
        );
      }
    } else if (action.action === "add-site") {
      map.closePopup();
      if (state.siteScreeningPending) return;
      state.placingScenario = true;
      addCandidateAt(L.latLng(Number(action.lat), Number(action.lng)));
    }
  }, true);
}
