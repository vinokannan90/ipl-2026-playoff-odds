// Direct JSONP fallback for when the backend is unreachable.
// Kept minimal; backend is the preferred path.

const COMP_ID = 284; // IPL 2026
const STANDINGS_URL = `https://scores.iplt20.com/ipl/feeds/stats/${COMP_ID}-groupstandings.js`;
const SCHEDULE_URL  = `https://scores.iplt20.com/ipl/feeds/${COMP_ID}-matchschedule.js`;

function jsonp(url, callbackName) {
  return new Promise((resolve, reject) => {
    const original = window[callbackName];
    let done = false;
    const cleanup = () => {
      window[callbackName] = original;
      if (s.parentNode) s.parentNode.removeChild(s);
    };
    window[callbackName] = (data) => {
      done = true;
      cleanup();
      resolve(data);
    };
    const s = document.createElement("script");
    s.src = url + (url.includes("?") ? "&" : "?") + "_t=" + Date.now();
    s.onerror = () => { if (!done) { cleanup(); reject(new Error("Failed to load " + url)); } };
    document.head.appendChild(s);
    setTimeout(() => { if (!done) { cleanup(); reject(new Error("Timeout loading " + url)); } }, 20000);
  });
}

export async function loadDirect() {
  const [stand, sched] = await Promise.all([
    jsonp(STANDINGS_URL, "ongroupstandings"),
    jsonp(SCHEDULE_URL, "MatchSchedule"),
  ]);
  return {
    standings: stand.points || [],
    schedule: sched.Matchsummary || [],
  };
}
