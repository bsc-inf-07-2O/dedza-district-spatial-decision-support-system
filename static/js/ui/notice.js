// Notification card at the top of the map (candidate sites, routes, suggestions).

const notice = document.getElementById("scenario-notice");
const noticeTitle = document.getElementById("scenario-notice-title");
const noticeMessage = document.getElementById("scenario-notice-message");
const noticeIcon = document.getElementById("scenario-notice-icon");
let noticeTimer = null;

// kind: "warning" (default), "error" or "success". Temporary notices hide after 4 s.
export function showNotice(title, message, kind = "warning", temporary = false) {
  clearTimeout(noticeTimer);
  notice.className = `scenario-notice ${kind}`;
  notice.hidden = false;
  noticeTitle.textContent = title;
  noticeMessage.textContent = message;
  noticeIcon.textContent = kind === "error" ? "!" : kind === "success" ? "✓" : "i";
  if (temporary) {
    noticeTimer = setTimeout(() => {
      notice.hidden = true;
    }, 4000);
  }
}

export function hideNotice() {
  clearTimeout(noticeTimer);
  notice.hidden = true;
}

export function setupNotice() {
  document.getElementById("close-scenario-notice").addEventListener("click", hideNotice);
}
