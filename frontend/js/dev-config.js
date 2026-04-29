// Local-dev auto-wire: when serving from localhost without a deployed
// backend, point at the FastAPI server on :8000. In production this is
// overridden by Static Web Apps configuration / build-time injection.
(function () {
  var h = window.location.hostname;
  if (!window.__API_BASE__ && (h === "localhost" || h === "127.0.0.1" || h === "[::1]")) {
    window.__API_BASE__ = "http://localhost:8000";
  }
})();
