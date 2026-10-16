// The helpers shared by the create_recipes window and the oceanval app,
// inlined into each page ahead of its own script.
const OceanValGUI = (() => {
  "use strict";

  const ICONS = {
    warn: '<svg viewBox="0 0 20 20"><path d="M10 2.6 1.9 16.8h16.2L10 2.6Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M10 8v3.8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="10" cy="14.2" r="1" fill="currentColor"/></svg>',
    error: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="m7.4 7.4 5.2 5.2m0-5.2-5.2 5.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.3 4.3L19 7.2" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    cross: '<svg viewBox="0 0 24 24"><path d="m7 7 10 10M17 7 7 17" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>',
  };

  const $ = (id) => document.getElementById(id);

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value; // only ever one of the icons here
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children) {
      if (child !== null && child !== undefined && child !== false) node.append(child);
    }
    return node;
  }

  const icon = (name) => el("span", { class: "icon", "aria-hidden": "true", html: ICONS[name] });

  // long paths keep their start and their file name
  function shorten(text, max = 56) {
    if (text.length <= max) return text;
    const head = Math.floor((max - 1) * 0.35);
    return text.slice(0, head) + "…" + text.slice(text.length - (max - 1 - head));
  }

  function plural(count, word) {
    return `${count} ${word}${count === 1 ? "" : "s"}`;
  }

  let toastTimer = null;
  function toast(text) {
    const node = $("toast");
    node.textContent = text;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.hidden = true; }, 7000);
  }

  // names this page load to the oceanval app, which quits when the browser
  // has closed every page it knows of (see App.touch)
  const pageId = Math.random().toString(36).slice(2) + Date.now().toString(36);

  // for the pages with no request of their own waiting on the app: each
  // request is held by the app for a few seconds, and the next is made as
  // that one is answered, as no timer is relied on (the browser slows those
  // in tabs in the background). root is where the app's api is, from the
  // page's address
  async function heartbeat(token, root) {
    for (;;) {
      try {
        const response = await fetch(`${root}api/heartbeat?${new URLSearchParams({ token, page: pageId })}`, { cache: "no-store" });
        if (!response.ok) return;
        if ((await response.json()).closed) return;
      } catch (error) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
    }
  }

  // ---- the header's buttons, and the panels they open ----
  // the cog (preferences, kept in the home .oceanvalrc) and the i (about), as
  // the oceanval app and the create_recipes window both have them, at the top
  // left beside the wordmark. Every page calls this once, after its own
  // script has what the panels need:
  //   state()        the page's state, with oceanval_version, datasets and
  //                  user_settings ({ values, themes, path })
  //   post(path, b)  the page's own POST, which answers with the reply's JSON
  //   settingsPath   where post sends the preferences, from the page's address
  //   behind         the parts of the page that cannot be reached while a panel is open
  //   onSaved()      called once the preferences are saved (the page may show them)
  const COG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';
  const INFO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 17v-6M12 7.5h.01" stroke-width="2.6"/></svg>';

  const CHROME_HTML = `
<div class="picker prefs" id="preferences" hidden>
  <div class="picker__card prefs__card" role="dialog" aria-modal="true" aria-labelledby="preferences-title" aria-describedby="settings-where">
    <div class="prefs__head">
      <h2 id="preferences-title">Preferences</h2>
      <p class="prefs__where" id="settings-where"></p>
    </div>
    <div class="prefs__body">
      <fieldset class="prefs__group">
        <legend class="prefs__legend">Colour</legend>
        <p class="prefs__hint">How the OceanVal window looks.</p>
        <div class="prefs__themes" id="settings-themes"></div>
      </fieldset>
      <fieldset class="prefs__group">
        <legend class="prefs__legend">Demo</legend>
        <p class="prefs__hint">Should the first page offer the demo?</p>
        <label class="prefs__check"><input type="checkbox" class="tick" id="settings-show-demo">Yes, show &ldquo;Try a demo&rdquo; on the first page</label>
      </fieldset>
      <fieldset class="prefs__group" id="settings-data-store-group">
        <legend class="prefs__legend">Data store</legend>
        <p class="prefs__hint">A folder to keep the built-in observational data OceanVal downloads (WOA23, OC-CCI, NSBC, COBE2, GLODAP and ICES), so that later matchups need not download it again. Your own data is never kept in it. Everything is kept in an <code>oceanval_datastore</code> folder inside the one you give, which must already exist. Leave it empty to keep nothing.</p>
        <label class="g-label" for="settings-data-store">Folder</label>
        <input type="text" class="s-input is-mono prefs__path" id="settings-data-store" placeholder="The folder's full path, e.g. /data/me" spellcheck="false" autocomplete="off" aria-describedby="settings-data-store-status">
        <p class="prefs__status" id="settings-data-store-status" aria-live="polite"></p>
        <div class="prefs__choices" id="settings-data-store-uses" role="radiogroup" aria-label="How matchups use the data store"></div>
        <p class="prefs__note" id="settings-data-store-note">Choose a disk with a reasonable amount of free space: the data store grows as more data is kept in it, and can come to several GB.</p>
      </fieldset>
      <p class="prefs__error" id="settings-error" role="alert" hidden></p>
    </div>
    <div class="picker__foot prefs__foot">
      <button type="button" class="btn btn--ghost" id="settings-cancel">Cancel</button>
      <button type="button" class="btn btn--primary" id="settings-apply">Apply</button>
    </div>
  </div>
</div>
<div class="picker prefs" id="info" hidden>
  <div class="picker__card prefs__card" role="dialog" aria-modal="true" aria-labelledby="info-title">
    <div class="prefs__head">
      <h2 id="info-title">About OceanVal</h2>
      <p class="prefs__where" id="info-version"></p>
    </div>
    <div class="prefs__body">
      <fieldset class="prefs__group">
        <legend class="prefs__legend">Links</legend>
        <ul class="info__links">
          <li><a href="https://pmlmodelling.github.io/OceanVal/" target="_blank" rel="noopener" id="info-website">The OceanVal website</a></li>
          <li><a href="https://github.com/pmlmodelling/oceanVal" target="_blank" rel="noopener" id="info-github">OceanVal on GitHub</a></li>
        </ul>
      </fieldset>
      <fieldset class="prefs__group">
        <legend class="prefs__legend">Built-in datasets</legend>
        <p class="prefs__hint">The observational datasets OceanVal can compare a simulation with. Water-column means the whole water column can be validated, not just the surface.</p>
        <h3 class="info__subtitle">Gridded data</h3>
        <div class="info__table-wrap" id="info-gridded"></div>
        <h3 class="info__subtitle">Point data</h3>
        <div class="info__table-wrap" id="info-point"></div>
      </fieldset>
    </div>
    <div class="picker__foot prefs__foot">
      <button type="button" class="btn btn--primary" id="info-close">Close</button>
    </div>
  </div>
</div>`;

  function mountChrome({ state, post, settingsPath, behind, onSaved = () => {} }) {
    const brand = document.querySelector(".brand");
    brand.append(
      el("button", { type: "button", class: "settings-open", id: "settings-open", "aria-haspopup": "dialog",
        "aria-controls": "preferences", "aria-label": "Preferences", title: "Preferences" },
        el("span", { class: "settings-open__icon", "aria-hidden": "true", html: COG })),
      el("button", { type: "button", class: "info-open", id: "info-open", "aria-haspopup": "dialog",
        "aria-controls": "info", "aria-label": "About OceanVal", title: "About OceanVal" },
        el("span", { class: "info-open__icon", "aria-hidden": "true", html: INFO })));
    document.body.insertAdjacentHTML("beforeend", CHROME_HTML);

    function applyTheme(theme) {
      if (theme === "default") delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = theme;
    }

    function chooseTheme(theme) {
      $("settings-themes").querySelectorAll(".theme-option").forEach((option) => {
        option.classList.toggle("is-chosen", option.querySelector("input").value === theme);
      });
    }

    const hideBehind = (hidden) => { behind().forEach((node) => { node.inert = hidden; }); };

    function bytes(n) {
      if (n === null || n === undefined) return null;
      const units = ["bytes", "KB", "MB", "GB", "TB"];
      let i = 0;
      while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
      return `${i === 0 ? n : n.toFixed(n < 10 ? 1 : 0)} ${units[i]}`;
    }

    // the data store: where it is, or will be once applied, and how it is
    // used, which cannot be chosen without a folder
    function showDataStore() {
      const typed = $("settings-data-store").value.trim();
      const { values, data_store: info = {} } = state().user_settings;
      const status = $("settings-data-store-status");
      if (!typed) {
        status.replaceChildren("No data store: built-in observational data is downloaded each time it is needed.");
      } else if (typed === values.data_store && info.root) {
        const free = bytes(info.free);
        status.replaceChildren("Kept in ", el("code", { text: info.root }), ". ",
          free ? `${free} free on that disk; ` : "",
          `it holds ${bytes(info.size || 0)}.`);
      } else {
        status.replaceChildren("Once applied, it is kept in ",
          el("code", { text: `${typed.replace(/\/+$/, "")}/oceanval_datastore` }), ".");
      }
      $("settings-data-store-uses").querySelectorAll("input").forEach((input) => { input.disabled = !typed; });
      $("settings-data-store-uses").classList.toggle("is-disabled", !typed);
    }

    // what is saved, each time it is opened: Cancel leaves it as it was
    function openSettings() {
      const { values, themes, path } = state().user_settings;
      $("settings-where").replaceChildren("Saved in ", el("code", { text: path }),
        ", and used each time OceanVal's window opens.");
      $("settings-themes").replaceChildren(...themes.map(([key, label]) => el("label", { class: "theme-option" },
        el("span", { class: "swatch", "data-theme": key, "aria-hidden": "true" },
          el("span", { class: "swatch__mast" }, el("i"), el("i")),
          el("span", { class: "swatch__page" }, el("span", { class: "swatch__card" }, el("i"), el("i"), el("b")))),
        el("span", { class: "theme-option__name" },
          el("input", { type: "radio", name: "settings-theme", value: key, checked: key === values.theme,
            onchange: () => chooseTheme(key) }),
          label,
          key === "default" ? el("small", { text: "OceanVal's own" }) : null))));
      chooseTheme(values.theme);
      $("settings-show-demo").checked = values.show_demo !== false;
      $("settings-data-store").value = values.data_store || "";
      const uses = (state().user_settings.data_store || {}).uses || [];
      $("settings-data-store-uses").replaceChildren(...uses.map(([key, label, note]) => el("label", { class: "prefs__check prefs__use" },
        el("input", { type: "radio", name: "settings-data-store-use", value: key,
          checked: key === (values.data_store_use || "always") }),
        el("span", {}, label, note ? el("small", { text: note }) : null))));
      showDataStore();
      $("settings-error").hidden = true;
      $("settings-apply").disabled = false;
      $("preferences").hidden = false;
      hideBehind(true);
      $("settings-themes").querySelector("input:checked").focus();
    }

    function closeSettings() {
      $("preferences").hidden = true;
      hideBehind(false);
      $("settings-open").focus();
    }

    async function applySettings() {
      const settings = {
        theme: $("settings-themes").querySelector("input:checked").value,
        show_demo: $("settings-show-demo").checked,
        data_store: $("settings-data-store").value.trim(),
      };
      const use = $("settings-data-store-uses").querySelector("input:checked");
      if (use) settings.data_store_use = use.value;
      $("settings-apply").disabled = true;
      const reply = await post(settingsPath, { settings });
      $("settings-apply").disabled = false;
      if (!reply.ok) {
        $("settings-error").textContent = reply.error || "Your preferences could not be saved.";
        $("settings-error").hidden = false;
        return;
      }
      state().user_settings.values = reply.settings;
      if (reply.data_store) state().user_settings.data_store = reply.data_store;
      applyTheme(reply.settings.theme);
      closeSettings();
      onSaved();
      toast("Preferences saved.");
    }

    $("settings-open").addEventListener("click", openSettings);
    $("settings-cancel").addEventListener("click", closeSettings);
    $("settings-apply").addEventListener("click", applySettings);
    $("settings-data-store").addEventListener("input", showDataStore);
    $("preferences").addEventListener("click", (event) => { if (event.target === $("preferences")) closeSettings(); });
    $("preferences").addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeSettings();
      }
    });

    // the website, GitHub and the built-in datasets
    function datasetTable(rows) {
      const head = ["Region", "Variable", "Dataset", "Water-column", "Units"];
      return el("table", { class: "info__table" },
        el("thead", {}, el("tr", {}, ...head.map((name) => el("th", { scope: "col", text: name })))),
        el("tbody", {}, ...rows.map((row) => el("tr", {},
          el("td", { text: row.region }),
          el("td", { text: row.variable }),
          el("td", {}, row.url
            ? el("a", { href: row.url, target: "_blank", rel: "noopener", text: row.dataset })
            : row.dataset),
          el("td", { class: row.water_column ? null : "is-no", text: row.water_column ? "Yes" : "No" }),
          el("td", {}, el("code", { text: row.units }))))));
    }

    function openInfo() {
      const datasets = state().datasets || [];
      $("info-gridded").replaceChildren(datasetTable(datasets.filter((row) => !row.point)));
      $("info-point").replaceChildren(datasetTable(datasets.filter((row) => row.point)));
      $("info-version").textContent = `Version ${state().oceanval_version}`;
      $("info").hidden = false;
      hideBehind(true);
      $("info-website").focus();
    }

    function closeInfo() {
      $("info").hidden = true;
      hideBehind(false);
      $("info-open").focus();
    }

    $("info-open").addEventListener("click", openInfo);
    $("info-close").addEventListener("click", closeInfo);
    $("info").addEventListener("click", (event) => { if (event.target === $("info")) closeInfo(); });
    $("info").addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeInfo();
      }
    });
  }

  return { ICONS, $, el, icon, shorten, plural, toast, pageId, heartbeat, mountChrome };
})();
