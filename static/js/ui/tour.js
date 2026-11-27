// First-visit guided tour: five steps that follow the planning workflow
// (area → health gap → new sites → briefing → export). It opens on the first
// visit only and can be reopened from the "?" button in the top bar.
import { openTool } from "./shell.js";

const SEEN_KEY = "dd-sdss-tour-seen";

const STEPS = [
  {
    target: ".area-control",
    title: "Choose an area",
    text: "Search for a Traditional Authority to zoom to it. Every count, table and recommendation then follows that area. District returns to the whole district.",
  },
  {
    target: "#tool-panel",
    before: () => openTool("gap"),
    title: "Test the access standard",
    text: "Move the Served and Underserved sliders to see how many cells, people and km² fall short under a different standard. The map recolours instantly.",
  },
  {
    target: "#suggest-sites",
    before: () => openTool("sites"),
    title: "Suggest the best sites",
    text: "Choose Health centre or School and a radius, then press Suggest best sites. The system proposes and ranks three locations by people newly reached. Add site on map tests your own.",
  },
  {
    target: "#insights-deck",
    before: () => document.querySelector('.analysis-tab[data-panel="briefing-panel"]')?.click(),
    title: "Read the briefing",
    text: "Recommended actions for the selected area: the priority TA, the best health-centre location, outreach targets and school advice. Print / Save PDF makes a paper brief.",
  },
  {
    target: "#export-brief",
    title: "Take the results with you",
    text: "Download the area's figures, recommendations, candidate ranking and settlement distances as a CSV for Excel or reports. Reopen this tour any time with the ? button.",
  },
];

const helpButton = document.getElementById("open-tour");
let tour = null; // { root, spotlight, card, index, returnFocus }

function storageAvailable() {
  try {
    localStorage.setItem("dd-sdss-storage-check", "1");
    localStorage.removeItem("dd-sdss-storage-check");
    return true;
  } catch {
    return false;
  }
}

function markSeen() {
  try { localStorage.setItem(SEEN_KEY, "1"); } catch { /* storage unavailable */ }
}

function hasSeen() {
  try { return localStorage.getItem(SEEN_KEY) === "1"; } catch { return false; }
}

function buildTour() {
  const root = document.createElement("div");
  root.className = "tour";
  const spotlight = document.createElement("div");
  spotlight.className = "tour-spotlight";
  spotlight.setAttribute("aria-hidden", "true");
  const card = document.createElement("section");
  card.className = "tour-card";
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "true");
  card.setAttribute("aria-labelledby", "tour-title");
  card.setAttribute("aria-describedby", "tour-text");
  card.innerHTML = `
    <header class="tour-card-header">
      <p class="tour-progress" id="tour-progress"></p>
      <button type="button" class="tour-close" data-tour="close" aria-label="Close the tour" title="Close (Esc)">×</button>
    </header>
    <h2 id="tour-title"></h2>
    <p id="tour-text"></p>
    <div class="tour-dots" aria-hidden="true">${STEPS.map(() => "<span></span>").join("")}</div>
    <footer class="tour-actions">
      <button type="button" class="tour-skip" data-tour="close">Skip tour</button>
      <span class="tour-nav">
        <button type="button" data-tour="back">Back</button>
        <button type="button" class="tour-next" data-tour="next">Next</button>
      </span>
    </footer>`;
  root.append(spotlight, card);
  card.addEventListener("click", (event) => {
    const action = event.target.closest("[data-tour]")?.dataset.tour;
    if (action === "close") closeTour();
    else if (action === "back") showStep(tour.index - 1);
    else if (action === "next") {
      if (tour.index === STEPS.length - 1) closeTour();
      else showStep(tour.index + 1);
    }
  });
  return { root, spotlight, card };
}

// Place the highlight over the target and the card beside it, kept on screen.
function position() {
  if (!tour) return;
  const { spotlight, card } = tour;
  const target = document.querySelector(STEPS[tour.index].target);
  const box = target && !target.hidden ? target.getBoundingClientRect() : null;
  const margin = 12;
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  const cardBox = card.getBoundingClientRect();

  if (!box || box.width === 0 || box.height === 0) {
    spotlight.hidden = true;
    card.style.left = `${Math.max(margin, (viewportWidth - cardBox.width) / 2)}px`;
    card.style.top = `${Math.max(margin, (viewportHeight - cardBox.height) / 2)}px`;
    return;
  }
  const pad = 6;
  spotlight.hidden = false;
  spotlight.style.left = `${box.left - pad}px`;
  spotlight.style.top = `${box.top - pad}px`;
  spotlight.style.width = `${box.width + pad * 2}px`;
  spotlight.style.height = `${box.height + pad * 2}px`;

  // Prefer the side with the most room: below, above, right, then left.
  const spaces = [
    { side: "below", room: viewportHeight - box.bottom },
    { side: "above", room: box.top },
    { side: "right", room: viewportWidth - box.right },
    { side: "left", room: box.left },
  ];
  const fits = spaces.find(({ side, room }) =>
    room >= (side === "below" || side === "above" ? cardBox.height : cardBox.width) + margin * 2);
  let left;
  let top;
  const side = fits?.side;
  if (side === "below" || side === "above") {
    left = box.left + box.width / 2 - cardBox.width / 2;
    top = side === "below" ? box.bottom + margin + pad : box.top - cardBox.height - margin - pad;
  } else if (side === "right" || side === "left") {
    left = side === "right" ? box.right + margin + pad : box.left - cardBox.width - margin - pad;
    top = box.top + box.height / 2 - cardBox.height / 2;
  } else {
    // No side has room (a large target on a small screen): overlap near the bottom.
    left = (viewportWidth - cardBox.width) / 2;
    top = viewportHeight - cardBox.height - margin;
  }
  card.style.left = `${Math.min(Math.max(margin, left), viewportWidth - cardBox.width - margin)}px`;
  card.style.top = `${Math.min(Math.max(margin, top), viewportHeight - cardBox.height - margin)}px`;
}

function showStep(index) {
  if (!tour || index < 0 || index >= STEPS.length) return;
  tour.index = index;
  const step = STEPS[index];
  step.before?.();
  const { card } = tour;
  card.querySelector("#tour-progress").textContent = `Step ${index + 1} of ${STEPS.length}`;
  card.querySelector("#tour-title").textContent = step.title;
  card.querySelector("#tour-text").textContent = step.text;
  card.querySelectorAll(".tour-dots span").forEach((dot, dotIndex) => {
    dot.classList.toggle("active", dotIndex === index);
  });
  card.querySelector('[data-tour="back"]').disabled = index === 0;
  card.querySelector('[data-tour="next"]').textContent = index === STEPS.length - 1 ? "Finish" : "Next";
  // Panels and the drawer animate open, so measure again once they settle.
  position();
  requestAnimationFrame(position);
  setTimeout(position, 260);
  card.querySelector('[data-tour="next"]').focus();
}

// Keyboard: Esc closes, arrows step, and Tab stays inside the card.
function onKeydown(event) {
  if (!tour) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeTour();
  } else if (event.key === "ArrowRight" && !event.target.closest("input, select, textarea")) {
    event.preventDefault();
    if (tour.index < STEPS.length - 1) showStep(tour.index + 1);
  } else if (event.key === "ArrowLeft" && !event.target.closest("input, select, textarea")) {
    event.preventDefault();
    showStep(tour.index - 1);
  } else if (event.key === "Tab") {
    const focusable = [...tour.card.querySelectorAll("button:not(:disabled)")];
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!tour.card.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
}

export function startTour() {
  if (tour) return;
  const { root, spotlight, card } = buildTour();
  tour = { root, spotlight, card, index: 0, returnFocus: document.activeElement };
  document.body.append(root);
  document.body.classList.add("tour-open");
  helpButton?.setAttribute("aria-expanded", "true");
  // Capture phase on window runs before the shell's Esc handler on document.
  window.addEventListener("keydown", onKeydown, true);
  window.addEventListener("resize", position);
  showStep(0);
}

export function closeTour() {
  if (!tour) return;
  const { root, returnFocus } = tour;
  tour = null;
  root.remove();
  document.body.classList.remove("tour-open");
  helpButton?.setAttribute("aria-expanded", "false");
  window.removeEventListener("keydown", onKeydown, true);
  window.removeEventListener("resize", position);
  markSeen();
  (returnFocus instanceof HTMLElement && document.contains(returnFocus) ? returnFocus : helpButton)?.focus();
}

export function setupTour() {
  helpButton?.addEventListener("click", startTour);
}

// Called once the map and data are ready. Without storage the tour would
// reappear on every visit, so it only opens itself when it can remember.
export function startTourOnFirstVisit() {
  if (storageAvailable() && !hasSeen()) startTour();
}
