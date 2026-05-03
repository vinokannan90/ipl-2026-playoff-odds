// Azure Application Insights initializer.
// The connection string is injected at deploy time into window.__APPINSIGHTS_CS__
// by frontend-deploy.yml (via dev-config.js). In local dev it is undefined and
// this file exits silently — no telemetry is sent from localhost.
(function () {
  var cs = window.__APPINSIGHTS_CS__;
  if (!cs) return;

  var AI = window.Microsoft && window.Microsoft.ApplicationInsights;
  if (!AI) return;

  var appInsights = new AI.ApplicationInsights({
    config: {
      connectionString: cs,
      // Track all fetch/XHR calls (standings, schedule, agent, leverage)
      disableFetchTracking: false,
      disableAjaxTracking: false,
      // Geo-location is derived server-side by App Insights from the client IP.
      // No extra config needed — it happens automatically.
      enableAutoRouteTracking: false, // single-page, no router
      // Avoid adding correlation headers to cross-origin requests (scores.iplt20.com)
      // which would trigger unnecessary CORS preflight failures.
      enableCorsCorrelation: false,
      correlationHeaderExcludedDomains: ["scores.iplt20.com", "www.iplt20.com"],
    },
  });

  appInsights.loadAppInsights();

  // Track the initial page view. Properties here appear in App Insights
  // "Page Views" table and on the geographic map.
  appInsights.trackPageView({ name: "PlayoffOdds" });

  // Expose globally so app.js can track custom events (optional).
  window.__appInsights = appInsights;
})();
