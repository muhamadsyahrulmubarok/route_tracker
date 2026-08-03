(() => {
  "use strict";

  const CACHE_KEY = "geomaps-companion-v1";
  const ACCESS_KEY_STORE = "geomaps-app-access-key";
  const OFF_ROUTE_M = 300;
  const CAT_COLORS = {
    food: "#b45309",
    market: "#047857",
    beach: "#0369a1",
    fuel: "#ca8a04",
    interest: "#7c3aed",
  };

  const state = {
    route: null,
    places: [],
    activeCats: new Set(["food", "market", "beach", "fuel", "interest"]),
    radiusM: 2000,
    user: null,
    watchId: null,
    simulateTimer: null,
    simulateIdx: 0,
    routeLine: null,
    placeLayer: null,
    userMarker: null,
    accuracyCircle: null,
    progress: null,
    followUser: true,
    sheetMode: "peek", // collapsed | peek | expanded
    expandedOnce: false,
  };

  const el = {
    app: document.getElementById("app"),
    statusText: document.getElementById("statusText"),
    settingsPanel: document.getElementById("settingsPanel"),
    btnSettings: document.getElementById("btnSettings"),
    btnCloseSettings: document.getElementById("btnCloseSettings"),
    accessKeyInput: document.getElementById("accessKeyInput"),
    originInput: document.getElementById("originInput"),
    destinationInput: document.getElementById("destinationInput"),
    modeSelect: document.getElementById("modeSelect"),
    btnLoadRoute: document.getElementById("btnLoadRoute"),
    progressChip: document.getElementById("progressChip"),
    progressMain: document.getElementById("progressMain"),
    progressSub: document.getElementById("progressSub"),
    categoryChips: document.getElementById("categoryChips"),
    radiusSelect: document.getElementById("radiusSelect"),
    btnTrack: document.getElementById("btnTrack"),
    btnSimulate: document.getElementById("btnSimulate"),
    btnRecenter: document.getElementById("btnRecenter"),
    btnSheetToggle: document.getElementById("btnSheetToggle"),
    sheet: document.getElementById("sheet"),
    sheetCollapsedLabel: document.getElementById("sheetCollapsedLabel"),
    nearbyCount: document.getElementById("nearbyCount"),
    nearbyList: document.getElementById("nearbyList"),
  };

  const map = L.map("map", {
    zoomControl: false,
    attributionControl: true,
  }).setView([-7.4, 109.8], 9);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap",
  }).addTo(map);
  state.placeLayer = L.layerGroup().addTo(map);

  // Stop auto-follow when user pans the map.
  map.on("dragstart", () => {
    state.followUser = false;
  });

  function setStatus(text) {
    el.statusText.textContent = text;
  }

  function haversineM(a, b) {
    const R = 6371000;
    const toRad = (d) => (d * Math.PI) / 180;
    const dLat = toRad(b[0] - a[0]);
    const dLng = toRad(b[1] - a[1]);
    const lat1 = toRad(a[0]);
    const lat2 = toRad(b[0]);
    const h =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  function buildRouteProgress(points) {
    const progress = [0];
    for (let i = 1; i < points.length; i += 1) {
      progress.push(progress[i - 1] + haversineM(points[i - 1], points[i]));
    }
    return progress;
  }

  function nearestOnRoute(lat, lng, points, progress) {
    const step = Math.max(1, Math.floor(points.length / 2500));
    let bestI = 0;
    let bestD = Infinity;
    for (let i = 0; i < points.length; i += step) {
      const d = haversineM([lat, lng], points[i]);
      if (d < bestD) {
        bestD = d;
        bestI = i;
      }
    }
    const start = Math.max(0, bestI - step);
    const end = Math.min(points.length, bestI + step + 1);
    for (let i = start; i < end; i += 1) {
      const d = haversineM([lat, lng], points[i]);
      if (d < bestD) {
        bestD = d;
        bestI = i;
      }
    }
    return { alongM: progress[bestI], offsetM: bestD, index: bestI };
  }

  function formatDist(m) {
    if (m >= 1000) return `${(m / 1000).toFixed(1)} km`;
    return `${Math.round(m)} m`;
  }

  function escapeHtml(s) {
    return String(s || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function sheetBottomPadding() {
    if (!el.sheet) return 180;
    return el.sheet.getBoundingClientRect().height + 16;
  }

  function refreshMapLayout() {
    requestAnimationFrame(() => {
      map.invalidateSize({ animate: false });
    });
  }

  function updateCollapsedLabel(count) {
    if (!el.sheetCollapsedLabel) return;
    const n = count == null ? el.nearbyCount?.textContent || "0" : String(count);
    el.sheetCollapsedLabel.textContent = `Nearby ${n} · tap to open`;
  }

  function setSheetMode(mode) {
    const next = ["collapsed", "peek", "expanded"].includes(mode) ? mode : "peek";
    state.sheetMode = next;
    el.sheet.classList.toggle("sheet-collapsed", next === "collapsed");
    el.sheet.classList.toggle("sheet-peek", next === "peek");
    el.sheet.classList.toggle("sheet-expanded", next === "expanded");
    el.app.classList.toggle("sheet-collapsed", next === "collapsed");
    el.app.classList.toggle("sheet-open", next === "expanded");
    const labels = {
      collapsed: "Show nearby panel",
      peek: "Expand nearby list",
      expanded: "Hide nearby panel",
    };
    el.btnSheetToggle.setAttribute("aria-label", labels[next]);
    updateCollapsedLabel();
    refreshMapLayout();
  }

  function setSettingsOpen(open) {
    el.settingsPanel.classList.toggle("hidden", !open);
    el.btnSettings.setAttribute("aria-expanded", String(open));
    el.btnSettings.setAttribute(
      "aria-label",
      open ? "Hide trip settings" : "Show trip settings"
    );
    el.btnSettings.textContent = open ? "✕" : "⚙";
  }

  function saveCache() {
    try {
      localStorage.setItem(
        CACHE_KEY,
        JSON.stringify({
          route: state.route,
          places: state.places,
          savedAt: Date.now(),
        })
      );
    } catch (_) {
      /* ignore */
    }
  }

  function loadCache() {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (_) {
      return null;
    }
  }

  function getAccessKey() {
    const fromInput = (el.accessKeyInput?.value || "").trim();
    if (fromInput) return fromInput;
    try {
      return (localStorage.getItem(ACCESS_KEY_STORE) || "").trim();
    } catch (_) {
      return "";
    }
  }

  function persistAccessKey() {
    const key = (el.accessKeyInput?.value || "").trim();
    try {
      if (key) localStorage.setItem(ACCESS_KEY_STORE, key);
    } catch (_) {
      /* ignore */
    }
  }

  async function postJson(url, body) {
    const headers = { "Content-Type": "application/json" };
    const accessKey = getAccessKey();
    if (accessKey) headers["X-App-Key"] = accessKey;
    const resp = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.error || `Request failed (${resp.status})`);
    }
    return data;
  }

  function drawRoute(route) {
    if (state.routeLine) map.removeLayer(state.routeLine);
    state.routeLine = L.polyline(route.points, {
      color: "#0b57d0",
      weight: 6,
      opacity: 0.92,
    }).addTo(map);
    L.circleMarker(route.points[0], {
      radius: 8,
      color: "#0f3d2e",
      fillColor: "#34d399",
      fillOpacity: 1,
      weight: 2,
    })
      .addTo(map)
      .bindPopup("Origin");
    L.circleMarker(route.points[route.points.length - 1], {
      radius: 8,
      color: "#7c2d12",
      fillColor: "#fb923c",
      fillOpacity: 1,
      weight: 2,
    })
      .addTo(map)
      .bindPopup("Destination");

    const pad = sheetBottomPadding();
    map.fitBounds(state.routeLine.getBounds(), {
      paddingTopLeft: [24, 120],
      paddingBottomRight: [24, pad],
    });
    refreshMapLayout();
  }

  function updateUserMarker(lat, lng, accuracy) {
    const latlng = [lat, lng];
    if (!state.userMarker) {
      const icon = L.divIcon({
        className: "",
        html: '<div class="user-dot"></div>',
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      });
      state.userMarker = L.marker(latlng, { icon, zIndexOffset: 1000 }).addTo(map);
    } else {
      state.userMarker.setLatLng(latlng);
    }
    if (accuracy != null) {
      if (!state.accuracyCircle) {
        state.accuracyCircle = L.circle(latlng, {
          radius: accuracy,
          color: "#dc2626",
          weight: 1,
          fillOpacity: 0.08,
        }).addTo(map);
      } else {
        state.accuracyCircle.setLatLng(latlng);
        state.accuracyCircle.setRadius(accuracy);
      }
    }
    if (state.followUser) {
      const z = Math.max(map.getZoom(), 14);
      map.setView(latlng, z, { animate: true });
    }
  }

  function updateProgress(lat, lng) {
    if (!state.route || !state.progress) return;
    const snap = nearestOnRoute(lat, lng, state.route.points, state.progress);
    const total =
      state.route.distanceMeters || state.progress[state.progress.length - 1];
    const off = snap.offsetM > OFF_ROUTE_M;
    el.progressChip.classList.toggle("off-route", off);
    el.progressMain.textContent = off
      ? `Off route · ${formatDist(snap.offsetM)}`
      : `${formatDist(snap.alongM)} of ${formatDist(total)}`;
    el.progressSub.textContent = off
      ? "Move closer to the planned corridor"
      : `${state.route.distanceText} · ${state.route.durationText}`;
  }

  function renderNearby() {
    state.placeLayer.clearLayers();
    if (!state.user) {
      el.nearbyCount.textContent = "0";
      updateCollapsedLabel(0);
      el.nearbyList.innerHTML =
        '<li><div class="meta">Tap <strong>GPS</strong> to track your location, then swipe up for nearby places.</div></li>';
      return;
    }

    const nearby = state.places
      .filter((p) => state.activeCats.has(p.category))
      .map((p) => ({
        ...p,
        distanceFromUser: haversineM(state.user, [p.lat, p.lng]),
      }))
      .filter((p) => p.distanceFromUser <= state.radiusM)
      .sort((a, b) => a.distanceFromUser - b.distanceFromUser)
      .slice(0, 80);

    el.nearbyCount.textContent = String(nearby.length);
    updateCollapsedLabel(nearby.length);
    el.nearbyList.innerHTML = "";

    if (!nearby.length) {
      el.nearbyList.innerHTML =
        '<li><div class="meta">No places in this radius. Try another category or larger radius.</div></li>';
      return;
    }

    for (const p of nearby) {
      const color = CAT_COLORS[p.category] || "#444";
      L.circleMarker([p.lat, p.lng], {
        radius: 8,
        color: "#1c1917",
        weight: 1,
        fillColor: color,
        fillOpacity: 0.95,
      })
        .addTo(state.placeLayer)
        .bindPopup(
          `<strong>${escapeHtml(p.name)}</strong><br>${escapeHtml(p.category)} · ${formatDist(p.distanceFromUser)}<br><a href="${p.maps_url}" target="_blank" rel="noopener">Open in Maps</a>`
        );

      const rating =
        p.rating != null && p.rating !== ""
          ? ` · ★ ${Number(p.rating).toFixed(1)}`
          : "";
      const li = document.createElement("li");
      li.innerHTML = `
        <div class="name">${escapeHtml(p.name)}</div>
        <span class="badge ${p.category}">${escapeHtml(p.category)}</span>
        <div class="meta">${formatDist(p.distanceFromUser)} away${rating}
          ${p.address ? ` · ${escapeHtml(p.address)}` : ""}
          · <a class="open-link" href="${p.maps_url}" target="_blank" rel="noopener">Open in Maps</a>
        </div>`;
      // Whole row opens Maps (easier while riding).
      li.addEventListener("click", (ev) => {
        if (ev.target.closest("a")) return;
        window.open(p.maps_url, "_blank", "noopener");
      });
      el.nearbyList.appendChild(li);
    }
  }

  function onLocation(lat, lng, accuracy) {
    state.user = [lat, lng];
    updateUserMarker(lat, lng, accuracy);
    updateProgress(lat, lng);
    renderNearby();
    if (!state.expandedOnce && state.places.length) {
      state.expandedOnce = true;
      setSheetMode("peek");
    }
  }

  function setGpsButton(on) {
    el.btnTrack.textContent = on ? "STOP" : "GPS";
    el.btnTrack.classList.toggle("is-on", on);
    el.btnTrack.setAttribute(
      "aria-label",
      on ? "Stop GPS tracking" : "Start GPS tracking"
    );
  }

  function stopSimulate() {
    if (state.simulateTimer) {
      clearInterval(state.simulateTimer);
      state.simulateTimer = null;
    }
    el.btnSimulate.textContent = "Simulate along route";
  }

  function stopGps() {
    if (state.watchId != null) {
      navigator.geolocation.clearWatch(state.watchId);
      state.watchId = null;
    }
    setGpsButton(false);
  }

  function startGps() {
    if (!navigator.geolocation) {
      setStatus("Geolocation not supported on this device");
      return;
    }
    stopSimulate();
    if (state.watchId != null) {
      stopGps();
      setStatus("GPS stopped");
      return;
    }
    state.followUser = true;
    setStatus("Requesting GPS…");
    setGpsButton(true);
    state.watchId = navigator.geolocation.watchPosition(
      (pos) => {
        setStatus("GPS tracking");
        onLocation(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy);
      },
      (err) => {
        setStatus(`GPS error: ${err.message}`);
        stopGps();
      },
      { enableHighAccuracy: true, maximumAge: 2000, timeout: 20000 }
    );
  }

  function recenter() {
    if (!state.user) {
      if (state.watchId == null) startGps();
      else setStatus("Waiting for GPS fix…");
      return;
    }
    state.followUser = true;
    map.setView(state.user, Math.max(map.getZoom(), 15), { animate: true });
  }

  function startSimulate() {
    if (!state.route) {
      setStatus("Load a route first");
      return;
    }
    if (state.simulateTimer) {
      stopSimulate();
      setStatus("Simulation stopped");
      return;
    }
    stopGps();
    state.followUser = true;
    state.simulateIdx = 0;
    const points = state.route.points;
    const step = Math.max(1, Math.floor(points.length / 200));
    el.btnSimulate.textContent = "Stop simulation";
    setStatus("Simulating along route");
    setSettingsOpen(false);
    setSheetMode("peek");
    state.simulateTimer = setInterval(() => {
      const pt = points[state.simulateIdx];
      onLocation(pt[0], pt[1], 25);
      state.simulateIdx = Math.min(points.length - 1, state.simulateIdx + step);
      if (state.simulateIdx >= points.length - 1) {
        stopSimulate();
        setStatus("Simulation finished");
      }
    }, 800);
  }

  async function loadTrip() {
    const origin = el.originInput.value.trim();
    const destination = el.destinationInput.value.trim();
    const mode = el.modeSelect.value;
    persistAccessKey();
    el.btnLoadRoute.disabled = true;
    setStatus("Fetching route…");
    try {
      const route = await postJson("/api/route", { origin, destination, mode });
      state.route = route;
      state.progress = buildRouteProgress(route.points);
      drawRoute(route);
      // Hide form as soon as the route is on the map (places can take a while).
      setSettingsOpen(false);
      el.progressMain.textContent = route.distanceText;
      el.progressSub.textContent = `${route.durationText} · loading places…`;
      setStatus("Loading Google Places…");
      const placesResp = await postJson("/api/places", {
        points: route.points,
        categories: [...state.activeCats],
        maxOffsetMeters: 2000,
      });
      state.places = placesResp.places || [];
      saveCache();
      setStatus(
        `${route.distanceText} · ${state.places.length} places · tap GPS`
      );
      el.progressSub.textContent = `${route.durationText} · ready for GPS`;
      renderNearby();
    } catch (err) {
      setStatus(err.message || "Failed to load trip");
      // Keep settings open on error so the user can fix key/inputs.
      setSettingsOpen(true);
    } finally {
      el.btnLoadRoute.disabled = false;
    }
  }

  function restoreFromCache() {
    const cached = loadCache();
    if (!cached?.route?.points?.length) return false;
    state.route = cached.route;
    state.places = cached.places || [];
    state.progress = buildRouteProgress(state.route.points);
    if (cached.route.origin) el.originInput.value = cached.route.origin;
    if (cached.route.destination) {
      el.destinationInput.value = cached.route.destination;
    }
    if (cached.route.mode) el.modeSelect.value = cached.route.mode;
    drawRoute(state.route);
    setStatus(`Cached trip · ${state.places.length} places · tap GPS`);
    el.progressMain.textContent = state.route.distanceText || "Cached route";
    el.progressSub.textContent = "Using saved data — reload if needed";
    renderNearby();
    return true;
  }

  el.btnSettings.addEventListener("click", () => {
    const open = el.settingsPanel.classList.contains("hidden");
    setSettingsOpen(open);
  });
  el.btnCloseSettings.addEventListener("click", () => setSettingsOpen(false));
  el.btnLoadRoute.addEventListener("click", loadTrip);
  el.btnTrack.addEventListener("click", startGps);
  el.btnSimulate.addEventListener("click", startSimulate);
  el.btnRecenter.addEventListener("click", recenter);

  // Keep list scrolling on mobile — don't let the map steal vertical swipes.
  el.nearbyList.addEventListener(
    "touchmove",
    (ev) => {
      ev.stopPropagation();
    },
    { passive: true }
  );
  el.sheet.addEventListener(
    "touchmove",
    (ev) => {
      if (ev.target.closest(".nearby-list")) ev.stopPropagation();
    },
    { passive: true }
  );
  el.btnSheetToggle.addEventListener("click", () => {
    // Tap: expanded -> collapsed (hide), otherwise open one step.
    if (state.sheetMode === "expanded") setSheetMode("collapsed");
    else if (state.sheetMode === "collapsed") setSheetMode("peek");
    else setSheetMode("expanded");
  });

  // Swipe on handle: down = hide, up = open more.
  {
    let startY = null;
    el.btnSheetToggle.addEventListener(
      "touchstart",
      (ev) => {
        startY = ev.changedTouches[0].clientY;
      },
      { passive: true }
    );
    el.btnSheetToggle.addEventListener(
      "touchend",
      (ev) => {
        if (startY == null) return;
        const dy = ev.changedTouches[0].clientY - startY;
        startY = null;
        if (Math.abs(dy) < 28) return;
        if (dy > 0) {
          // swipe down
          if (state.sheetMode === "expanded") setSheetMode("peek");
          else setSheetMode("collapsed");
        } else {
          // swipe up
          if (state.sheetMode === "collapsed") setSheetMode("peek");
          else setSheetMode("expanded");
        }
      },
      { passive: true }
    );
  }
  el.radiusSelect.addEventListener("change", () => {
    state.radiusM = Number(el.radiusSelect.value) || 2000;
    renderNearby();
  });
  el.categoryChips.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".chip");
    if (!btn) return;
    const cat = btn.dataset.cat;
    if (state.activeCats.has(cat)) {
      if (state.activeCats.size === 1) return;
      state.activeCats.delete(cat);
      btn.classList.remove("active");
    } else {
      state.activeCats.add(cat);
      btn.classList.add("active");
    }
    renderNearby();
  });

  window.addEventListener("resize", refreshMapLayout);
  window.addEventListener("orientationchange", () => {
    setTimeout(refreshMapLayout, 250);
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  try {
    const savedKey = localStorage.getItem(ACCESS_KEY_STORE) || "";
    if (el.accessKeyInput && savedKey) el.accessKeyInput.value = savedKey;
  } catch (_) {
    /* ignore */
  }

  setSheetMode("peek");
  if (!restoreFromCache()) {
    setSettingsOpen(true);
    setStatus("Set trip, then Load route & places");
  } else {
    setSettingsOpen(false);
  }
  refreshMapLayout();
})();
