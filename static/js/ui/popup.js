// Map popup cards (styles: .popup-card in static/css/map.css).
import { escapeHtml } from "./format.js";

export const POPUP_OPTIONS = {
  maxWidth: 300,
  minWidth: 230,
  className: "card-popup",
  autoPanPadding: [16, 16],
};

// Values are escaped here; `actions` render as buttons whose data-action /
// data-* attributes are handled by one delegated click listener
// (static/js/features/popup-actions.js).
export function popupCard({ kicker, title, badge, rows = [], meter, note, actions = [] }) {
  const badgeHtml = badge
    ? `<span class="popup-badge ${escapeHtml(badge.tone || "")}">${escapeHtml(badge.text)}</span>`
    : "";
  const rowsHtml = rows
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd>`)
    .join("");
  const meterHtml = meter
    ? `<div class="popup-meter" title="${escapeHtml(meter.label)}">
         <span class="popup-meter-label">${escapeHtml(meter.label)}</span>
         <span class="popup-meter-track"><span style="width:${Math.max(0, Math.min(100, meter.percent))}%"></span></span>
         <strong>${escapeHtml(meter.text)}</strong>
       </div>`
    : "";
  const actionsHtml = actions.length
    ? `<div class="popup-actions">${actions.map((action) => {
        const data = Object.entries(action.data || {})
          .map(([key, value]) => `data-${key}="${escapeHtml(value)}"`).join(" ");
        return `<button type="button" class="popup-action ${action.primary ? "primary" : ""}" data-action="${escapeHtml(action.action)}" data-icon="${escapeHtml(action.icon || "")}" ${data}>${escapeHtml(action.label)}</button>`;
      }).join("")}</div>`
    : "";
  return `<div class="popup-card">
    ${kicker ? `<p class="popup-kicker">${escapeHtml(kicker)}</p>` : ""}
    <div class="popup-head"><h3>${escapeHtml(title)}</h3>${badgeHtml}</div>
    ${rowsHtml ? `<dl class="popup-rows">${rowsHtml}</dl>` : ""}
    ${meterHtml}
    ${note ? `<p class="popup-note">${escapeHtml(note)}</p>` : ""}
    ${actionsHtml}
  </div>`;
}
