/* UI for the local recorder server.
   Meters come over SSE; everything else is plain fetch. */
(() => {
  "use strict";

  const TOKEN = new URLSearchParams(location.search).get("t") || "";
  const MIN_DB = -60; // bottom of the meter scale

  const $ = (id) => document.getElementById(id);
  const el = {
    dot: $("statusDot"), deviceLabel: $("deviceLabel"), alerts: $("alerts"),
    meters: $("meters"), clipPill: $("clipPill"),
    recBtn: $("recBtn"), recLabel: $("recLabel"), elapsed: $("elapsed"), recInfo: $("recInfo"),
    deviceSel: $("deviceSel"), rateSel: $("rateSel"), deviceMeta: $("deviceMeta"),
    chanSel: $("chanSel"), monoSel: $("monoSel"), monoField: $("monoField"), subSel: $("subSel"),
    outdir: $("outdir"), revealBtn: $("revealBtn"),
    fileList: $("fileList"), emptyFiles: $("emptyFiles"), quitBtn: $("quitBtn"),
  };

  let devices = [];
  let lastStatus = null;
  let rowCount = 0;
  let filesKey = "";
  let busy = false;

  // ------------------------------------------------------------------ http
  const api = async (path, options = {}) => {
    const sep = path.includes("?") ? "&" : "?";
    const res = await fetch(`${path}${sep}t=${encodeURIComponent(TOKEN)}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let data = {};
    try { data = await res.json(); } catch { /* non-JSON error page */ }
    if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  };
  const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

  // --------------------------------------------------------------- helpers
  const fmtTime = (secs) => {
    const s = Math.max(0, secs || 0);
    const m = Math.floor(s / 60);
    const rest = s - m * 60;
    return `${String(m).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
  };
  const fmtSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  };
  const pct = (db) => {
    if (db <= MIN_DB) return 0;
    return Math.max(0, Math.min(100, ((db - MIN_DB) / -MIN_DB) * 100));
  };
  const fmtDb = (db) => (db <= MIN_DB ? "−∞" : `${db.toFixed(1)}`);

  const showAlerts = (items) => {
    const key = items.map((i) => i.text + (i.action || "")).join("|");
    if (key === el.alerts.dataset.key) return;
    el.alerts.dataset.key = key;
    el.alerts.innerHTML = "";
    for (const item of items) {
      const div = document.createElement("div");
      div.className = `alert ${item.kind}`;
      const b = document.createElement("b");
      b.textContent = `${item.title} `;
      div.append(b, document.createTextNode(item.text));
      if (item.action) {
        const btn = document.createElement("button");
        btn.className = "ghost sm";
        btn.textContent = item.action;
        btn.onclick = item.onAction;
        div.append(btn);
      }
      el.alerts.append(div);
    }
  };

  // ---------------------------------------------------------------- meters
  const buildMeters = (n) => {
    el.meters.innerHTML = "";
    for (let i = 0; i < n; i++) {
      const row = document.createElement("div");
      row.className = "meterRow";
      row.innerHTML =
        `<span class="chLabel">${n === 1 ? "in" : i === 0 ? "L" : "R"}</span>` +
        `<div class="track"><div class="grad"></div><div class="mask"></div>` +
        `<div class="ticks"><i style="left:20%"></i><i style="left:40%"></i>` +
        `<i style="left:60%"></i><i style="left:80%"></i><i style="left:90%"></i></div>` +
        `<div class="hold"></div></div>` +
        `<span class="val">−∞ dB</span>`;
      el.meters.append(row);
    }
    rowCount = n;
  };

  const paintMeters = (levels) => {
    if (levels.length !== rowCount) buildMeters(levels.length);
    const rows = el.meters.children;
    for (let i = 0; i < levels.length; i++) {
      const lvl = levels[i];
      const row = rows[i];
      if (!row) continue;
      row.querySelector(".mask").style.width = `${100 - pct(lvl.rms)}%`;
      row.querySelector(".hold").style.left = `${pct(lvl.hold)}%`;
      row.querySelector(".val").textContent = `${fmtDb(lvl.peak)} dB`;
    }
  };

  // ---------------------------------------------------------------- status
  const applyStatus = (st) => {
    lastStatus = st;
    paintMeters(st.levels || []);
    el.clipPill.hidden = !st.clipped;

    el.dot.className = `dot ${st.error ? "err" : st.recording ? "rec" : st.open ? "live" : ""}`;
    el.dot.title = st.error ? "error" : st.recording ? "recording" : st.open ? "listening" : "idle";

    el.elapsed.textContent = fmtTime(st.elapsed);
    el.recBtn.disabled = !st.open || busy;
    el.recBtn.classList.toggle("armed", st.recording);
    el.recLabel.textContent = st.recording ? "Stop" : "Record";
    el.recInfo.textContent = st.recording
      ? `${st.record_name || ""} · ${st.channels === 1 ? "mono" : "stereo"} · ${st.subtype.replace("PCM_", "")}-bit`
      : st.open ? "ready" : "no input device";

    el.outdir.textContent = st.outdir || "";
    el.monoField.style.display = Number(el.chanSel.value) === 1 ? "" : "none";

    const alerts = [];
    if (st.error) {
      alerts.push({
        kind: "bad", title: "Problem:", text: st.error,
        action: "Reconnect", onAction: reopen,
      });
    }
    if (st.open && st.silent_seconds > 2.5) {
      alerts.push({
        kind: "warn",
        title: "No signal.",
        text: window.__permHint ||
          "Every sample is silent. Check the microphone is selected and unmuted.",
      });
    }
    if (st.overflows > 0) {
      alerts.push({
        kind: "warn", title: "Dropouts:",
        text: `${st.overflows} audio block(s) were dropped — the disk or CPU could not keep up.`,
      });
    }
    showAlerts(alerts);
  };

  // --------------------------------------------------------------- devices
  const loadDevices = async () => {
    const data = await api("/api/devices");
    devices = data.devices || [];
    window.__permHint = data.permission_hint || "";
    const current = data.current ?? data.suggested;

    el.deviceSel.innerHTML = "";
    if (!devices.length) {
      el.deviceSel.innerHTML = `<option>No input devices found</option>`;
      el.deviceLabel.textContent = "no input devices";
      return;
    }
    for (const dev of devices) {
      const opt = document.createElement("option");
      opt.value = String(dev.index);
      const tags = [];
      if (dev.manufacturer) tags.push(dev.manufacturer);
      if (dev.likely_tbone) tags.push("t.bone");
      opt.textContent = `${dev.name}${tags.length ? ` — ${tags.join(", ")}` : ""}`;
      if (dev.index === current) opt.selected = true;
      el.deviceSel.append(opt);
    }
    syncRates();
  };

  const currentDevice = () => devices.find((d) => d.index === Number(el.deviceSel.value));

  const syncRates = () => {
    const dev = currentDevice();
    if (!dev) return;
    const rates = dev.samplerates && dev.samplerates.length ? dev.samplerates : [48000];
    const want = lastStatus ? lastStatus.samplerate : dev.default_samplerate;
    el.rateSel.innerHTML = "";
    for (const rate of rates) {
      const opt = document.createElement("option");
      opt.value = String(rate);
      opt.textContent = `${(rate / 1000).toFixed(rate % 1000 ? 1 : 0)} kHz`;
      if (rate === want) opt.selected = true;
      el.rateSel.append(opt);
    }
    el.deviceLabel.textContent = `${dev.name}${dev.manufacturer ? ` · ${dev.manufacturer}` : ""}`;
    const extra = [`${dev.channels} channel${dev.channels === 1 ? "" : "s"}`, dev.host_api];
    if (dev.also_on && dev.also_on.length) extra.push(`also on ${[...new Set(dev.also_on)].join(", ")}`);
    el.deviceMeta.textContent = extra.join(" · ");
    el.chanSel.querySelector('option[value="2"]').disabled = dev.channels < 2;
    if (dev.channels < 2) el.chanSel.value = "1";
  };

  const reopen = async () => {
    try {
      await loadDevices();   // indices move when a device is re-plugged
      await openDevice();
    } catch (err) {
      showAlerts([{ kind: "bad", title: "Reconnect failed:", text: err.message }]);
    }
  };

  const openDevice = async () => {
    const dev = currentDevice();
    if (!dev) return;
    busy = true;
    try {
      await post("/api/open", { device: dev.index, samplerate: Number(el.rateSel.value) });
    } catch (err) {
      showAlerts([{ kind: "bad", title: "Could not open device:", text: err.message }]);
    } finally {
      busy = false;
    }
  };

  // ----------------------------------------------------------------- files
  const renderFiles = (files) => {
    const key = files.map((f) => `${f.name}:${f.size}`).join("|");
    if (key === filesKey) return;
    filesKey = key;
    el.fileList.innerHTML = "";
    el.emptyFiles.hidden = files.length > 0;
    for (const f of files) {
      const li = document.createElement("li");
      if (f.recording) li.classList.add("live");
      const name = document.createElement("span");
      name.className = "fname";
      name.textContent = f.name;
      const size = document.createElement("span");
      size.className = "fsize";
      size.textContent = fmtSize(f.size);
      li.append(name, size);

      if (f.recording) {
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = "● RECORDING";
        li.append(badge);
      } else {
        const audio = document.createElement("audio");
        audio.controls = true;
        audio.preload = "none";
        audio.src = `/api/audio/${encodeURIComponent(f.name)}?t=${encodeURIComponent(TOKEN)}`;
        const reveal = document.createElement("button");
        reveal.className = "ghost sm";
        reveal.textContent = "Show";
        reveal.onclick = () => post("/api/reveal", { name: f.name }).catch(() => {});
        const del = document.createElement("button");
        del.className = "ghost sm";
        del.textContent = "Delete";
        del.onclick = async () => {
          if (!confirm(`Delete ${f.name}?`)) return;
          try { await post("/api/delete", { name: f.name }); filesKey = ""; refreshFiles(); }
          catch (err) { alert(err.message); }
        };
        li.append(audio, reveal, del);
      }
      el.fileList.append(li);
    }
  };

  const refreshFiles = async () => {
    try { renderFiles((await api("/api/recordings")).recordings || []); }
    catch { /* transient */ }
  };

  // ------------------------------------------------------------------ wire
  el.recBtn.onclick = async () => {
    if (!lastStatus) return;
    busy = true;
    el.recBtn.disabled = true;
    try {
      if (lastStatus.recording) {
        await post("/api/record/stop");
      } else {
        await post("/api/record/start", {
          channels: Number(el.chanSel.value),
          subtype: el.subSel.value,
          mono_source: el.monoSel.value,
        });
      }
      filesKey = "";
      refreshFiles();
    } catch (err) {
      showAlerts([{ kind: "bad", title: "Recording:", text: err.message }]);
    } finally {
      busy = false;
    }
  };

  el.deviceSel.onchange = async () => { syncRates(); await openDevice(); };
  el.rateSel.onchange = openDevice;
  el.chanSel.onchange = () => {
    el.monoField.style.display = Number(el.chanSel.value) === 1 ? "" : "none";
    post("/api/settings", { channels: Number(el.chanSel.value) }).catch(() => {});
  };
  el.monoSel.onchange = () => post("/api/settings", { mono_source: el.monoSel.value }).catch(() => {});
  el.subSel.onchange = () => post("/api/settings", { subtype: el.subSel.value }).catch(() => {});
  el.revealBtn.onclick = () => post("/api/reveal", {}).catch(() => {});
  el.quitBtn.onclick = async () => {
    if (!confirm("Stop the recorder server?")) return;
    try { await post("/api/quit"); } catch { /* server goes away mid-request */ }
    document.body.innerHTML =
      '<main><section class="card"><h2>Stopped</h2>' +
      '<p class="hint">The recorder has shut down. You can close this tab.</p></section></main>';
  };

  // Space toggles recording, unless focus is in a control.
  document.addEventListener("keydown", (e) => {
    if (e.code !== "Space" || e.target.closest("select, button, input")) return;
    e.preventDefault();
    el.recBtn.click();
  });

  // ------------------------------------------------------------------ boot
  const connect = () => {
    const src = new EventSource(`/api/stream?t=${encodeURIComponent(TOKEN)}`);
    src.onmessage = (ev) => {
      try { applyStatus(JSON.parse(ev.data)); } catch { /* ignore a bad frame */ }
    };
    src.onerror = () => {
      el.dot.className = "dot err";
      src.close();
      setTimeout(connect, 1500); // server restarted or tab was suspended
    };
  };

  (async () => {
    const st = await api("/api/status");
    // Populate the bit-depth list from what the server actually supports.
    el.subSel.innerHTML = "";
    for (const [value, label] of Object.entries(st.subtypes || {})) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      if (value === st.subtype) opt.selected = true;
      el.subSel.append(opt);
    }
    el.chanSel.value = String(st.channels || 1);
    el.monoSel.value = st.mono_source || "left";
    await loadDevices();
    if (!st.open) await openDevice();
    applyStatus(st);
    connect();
    refreshFiles();
    setInterval(refreshFiles, 2000);
  })().catch((err) => {
    showAlerts([{ kind: "bad", title: "Startup failed:", text: err.message }]);
  });
})();
