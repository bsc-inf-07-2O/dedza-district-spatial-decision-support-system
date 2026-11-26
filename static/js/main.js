// Entry point (loaded by static/index.html). Checks that Leaflet loaded, then
// starts the app in static/js/app.js.
//
// Layout of static/js/:
//   app.js        start-up order: map, shell, data, then each tool
//   data.js       data loaded from the API, plus lookups built from it
//   state.js      what the user has chosen (area, thresholds, candidates...)
//   api.js        fetch helpers
//   map/          the Leaflet map, overlay layers, legend and symbols
//   ui/           reusable widgets: dropdowns, number inputs, popups,
//                 notifications, sliders, formatting, the app shell
//   features/     one file per tool or panel (routes, sites, gap, ...)

const mapStatus = document.getElementById("map-status");
if (typeof L === "undefined") {
  mapStatus.textContent = "The map library did not load. Check that static/vendor/leaflet-1.9.4 is present, then reload.";
} else {
  import("./app.js").catch((error) => {
    console.error(error);
    mapStatus.textContent = `The app could not start: ${error.message}`;
  });
}
