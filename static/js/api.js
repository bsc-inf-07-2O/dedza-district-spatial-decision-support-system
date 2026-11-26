// Requests to the Flask API (see sdss/routes/).

export async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load ${url}`);
  return res.json();
}

// POST a JSON body; throws the server's error message (or `<what> failed (status).`).
export async function postJson(url, body, what) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(result.error || `${what} failed (${response.status}).`);
  }
  return result;
}
