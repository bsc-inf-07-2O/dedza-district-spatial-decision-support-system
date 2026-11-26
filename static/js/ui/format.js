// Text and number formatting shared by the map, panels and exports.

export function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

// "Near Mlinga (5)" -> "near Mlinga (5)"; other names get a "near " prefix.
export function nearPlace(name) {
  const text = String(name || "an unnamed settlement");
  return /^near /i.test(text) ? `near ${text.slice(5)}` : `near ${text}`;
}

export function ordinal(n) {
  const tens = n % 100;
  const suffix = tens >= 11 && tens <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th");
  return `${n}${suffix}`;
}

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

// Road travel time (minutes) from a cell's population centre to the nearest
// facility, calculated at build time on the mapped road network.
export function formatMinutes(minutes) {
  if (minutes == null || !Number.isFinite(Number(minutes))) return "no road route";
  const total = Math.round(Number(minutes));
  return total >= 60 ? `${Math.floor(total / 60)} h ${total % 60} min` : `${total} min`;
}

// Route-tool times, rounded to 5 minutes.
export function formatEstimatedRouteTime(minutes) {
  const roundedMinutes = Math.round(minutes / 5) * 5;
  if (roundedMinutes >= 60) {
    return `${Math.floor(roundedMinutes / 60)} hr ${roundedMinutes % 60} min`;
  }
  return roundedMinutes < 5 ? "under 5 min" : `${roundedMinutes} min`;
}

// Tag colour for a travel time: strong (≤ 30 min), moderate (≤ 60), weak.
export function travelTimeTone(minutes) {
  if (minutes == null) return "weak";
  if (minutes <= 30) return "strong";
  if (minutes <= 60) return "moderate";
  return "weak";
}

export function formatShare(share) {
  return `${Math.round(Number(share || 0) * 100)}%`;
}

// "12,345 (+8%)": a cell's projected 2030 residents and growth since 2025.
export function growthText(properties) {
  const base = Number(properties.population) || 0;
  const growth = Number(properties.growth) || 0;
  const rate = base > 0 ? ` (+${(growth / base * 100).toFixed(0)}%)` : "";
  return `${Math.round(Number(properties.population_2030) || 0).toLocaleString()}${rate}`;
}

// School level from Malawi naming conventions (F.P. full primary, J.P. junior
// primary, CDSS community day secondary). The source has no level field.
export function schoolLevel(name) {
  const text = String(name || "").toLowerCase().replace(/\s+/g, " ");
  if (/\b(c?dss|secondary|sec\.?|high school|acad[ae]my|open)\b/.test(text)) return "Secondary";
  if (/\bj\.? ?p\b/.test(text)) return "Junior primary";
  if (/\bf\.? ?p\b|\bprimary\b|\bl\. ?e\. ?a\b/.test(text)) return "Primary";
  return "School";
}

// Fast planar distance (km); accurate to well under 1% across Dedza.
export function quickKm([lon1, lat1], [lon2, lat2]) {
  const dx = (lon2 - lon1) * 111.32 * Math.cos(((lat1 + lat2) / 2) * Math.PI / 180);
  const dy = (lat2 - lat1) * 110.574;
  return Math.hypot(dx, dy);
}

export function csvField(value) {
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}
