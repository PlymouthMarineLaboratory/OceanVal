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

  // tells the app when this page's tab is not looked at, and when it is
  // again, as a browser may then slow the page's requests or freeze it
  // (Safari does), which the app would otherwise take for a closed window
  // (see App.page_visibility). keepalive, so that it is sent as the tab goes
  function tellVisibility(token, root) {
    const tell = () => {
      fetch(`${root}api/visibility`, {
        method: "POST",
        cache: "no-store",
        keepalive: true,
        headers: { "Content-Type": "application/json", "X-OceanVal-Token": token },
        body: JSON.stringify({ page: pageId, hidden: document.hidden }),
      }).catch(() => {});
    };
    document.addEventListener("visibilitychange", tell);
    if (document.hidden) tell();
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
  const RESTART = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>';
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
        <p class="prefs__size" id="settings-data-store-size" aria-live="polite" hidden></p>
        <div class="prefs__check-row">
          <button type="button" class="btn btn--ghost" id="settings-data-store-check" disabled>Run a check on the data store</button>
          <span class="prefs__hint-inline" id="settings-data-store-check-hint">It only looks: nothing is changed, removed or fixed.</span>
        </div>
        <div class="check" id="settings-data-store-result" aria-live="polite" hidden></div>
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
          <li><a href="https://PlymouthMarineLaboratory.github.io/OceanVal/" target="_blank" rel="noopener" id="info-website">The OceanVal website</a></li>
          <li><a href="https://github.com/PlymouthMarineLaboratory/oceanVal" target="_blank" rel="noopener" id="info-github">OceanVal on GitHub</a></li>
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

  // restart: { go, running }, for a page that can start again from the first
  // page. go() asks the app to forget everything (and moves the page on),
  // returning whether it did; running() is whether a run would be stopped.
  function mountChrome({ state, post, settingsPath, behind, onSaved = () => {}, restart = null }) {
    const brand = document.querySelector(".brand");
    brand.append(...[
      el("button", { type: "button", class: "settings-open", id: "settings-open", "aria-haspopup": "dialog",
        "aria-controls": "preferences", "aria-label": "Preferences", title: "Preferences" },
        el("span", { class: "settings-open__icon", "aria-hidden": "true", html: COG })),
      el("button", { type: "button", class: "info-open", id: "info-open", "aria-haspopup": "dialog",
        "aria-controls": "info", "aria-label": "About OceanVal", title: "About OceanVal" },
        el("span", { class: "info-open__icon", "aria-hidden": "true", html: INFO })),
      restart ? el("button", { type: "button", class: "info-open restart-open", id: "restart-open", "aria-haspopup": "dialog",
        "aria-controls": "restart-confirm", "aria-label": "Restart", title: "Restart" },
        el("span", { class: "restart-open__icon", "aria-hidden": "true", html: RESTART })) : null,
    ].filter(Boolean));
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

    let checking = false;

    // what a check of the data store found: a line saying how it went, and then
    // each kind of thing found with what it is
    function showCheck(check) {
      const out = $("settings-data-store-result");
      const { problems, notes } = check;
      const looked = `Looked at ${check.pieces} piece${check.pieces === 1 ? "" : "s"} (${bytes(check.bytes)}).`;
      const kind = problems ? "problem" : notes ? "note" : "ok";
      const said = problems
        ? `${problems} problem${problems === 1 ? "" : "s"} found${notes ? `, and ${notes} thing${notes === 1 ? "" : "s"} to know about` : ""}.`
        : notes ? `No problems found, but ${notes} thing${notes === 1 ? "" : "s"} to know about.` : "No problems found.";
      out.replaceChildren(
        el("p", { class: `check__summary check__summary--${kind}` },
          el("strong", { text: `${problems ? "Problems: " : notes ? "Notes: " : "All clear: "}` }), `${said} ${looked}`),
        ...check.groups.map((group) => el("section", { class: `check__group check__group--${group.level}` },
          el("h4", { class: "check__title" },
            el("span", { class: "check__badge", text: group.level === "problem" ? "Problem" : "Note" }),
            `${group.title} (${group.count})`),
          el("ul", { class: "check__list" }, ...group.items.map((item) => el("li", { text: item }))))),
        el("p", { class: "check__foot", text: `Checked at ${check.checked.replace("T", " ")}. Nothing was changed.` }));
      out.hidden = false;
    }

    async function runCheck() {
      const button = $("settings-data-store-check");
      const out = $("settings-data-store-result");
      checking = true;
      button.disabled = true;
      out.replaceChildren(el("p", { class: "check__foot", text: "Checking the data store..." }));
      out.hidden = false;
      const reply = await post(settingsPath.replace(/settings$/, "data_store_check"), {});
      checking = false;
      showDataStore();
      if (!reply.ok) {
        out.replaceChildren(el("p", { class: "check__summary check__summary--problem", text: reply.error || "The data store could not be checked." }));
        out.hidden = false;
        return;
      }
      showCheck(reply.check);
    }

    // the data store: where it is, or will be once applied, and how it is
    // used, which cannot be chosen without a folder
    function showDataStore() {
      const typed = $("settings-data-store").value.trim();
      const { values, data_store: info = {} } = state().user_settings;
      const status = $("settings-data-store-status");
      const size = $("settings-data-store-size");
      const applied = typed && typed === values.data_store && info.root;
      size.hidden = !applied;
      // a check is of the folder saved, so of one applied; what was found is of the folder as it was
      $("settings-data-store-check").disabled = !applied || checking;
      $("settings-data-store-check-hint").textContent = applied
        ? "It only looks: nothing is changed, removed or fixed."
        : "Apply a folder first: the check is of the data store that is saved.";
      if (!applied) $("settings-data-store-result").hidden = true;
      if (!typed) {
        status.replaceChildren("No data store: built-in observational data is downloaded each time it is needed.");
      } else if (applied) {
        const free = bytes(info.free);
        status.replaceChildren("Kept in ", el("code", { text: info.root }), ".",
          free ? ` ${free} free on that disk.` : "");
        size.replaceChildren("Size of the data store: ", el("strong", { text: bytes(info.size || 0) }));
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
      $("settings-data-store-result").hidden = true;
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
    $("settings-data-store-check").addEventListener("click", runCheck);
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

    if (restart) mountRestart({ restart, hideBehind });
  }

  // the button's pop-up: starting again from the first page throws away what
  // was entered, so it is asked about, with Cancel the default
  function mountRestart({ restart, hideBehind }) {
    const stopping = el("p", { class: "restart__stopping", id: "restart-stopping", hidden: "",
      text: "The run in progress will be stopped." });
    const cancel = el("button", { type: "button", class: "btn btn--ghost", id: "restart-cancel", text: "Cancel" });
    const confirm = el("button", { type: "button", class: "btn btn--danger", id: "restart-go", text: "Restart" });
    const dialog = el("div", { class: "picker", id: "restart-confirm", hidden: "" },
      el("div", { class: "picker__card restart__card", role: "dialog", "aria-modal": "true", "aria-labelledby": "restart-title" },
        el("div", { class: "restart__body" },
          el("h2", { id: "restart-title", text: "Start again?" }),
          el("p", { text: "Do you want to go back to the first page? Everything you have entered will be discarded, and you will start from a clean slate." }),
          stopping),
        el("div", { class: "picker__foot" }, cancel, confirm)));
    document.body.append(dialog);

    function open() {
      stopping.hidden = !restart.running();
      confirm.disabled = false;
      dialog.hidden = false;
      hideBehind(true);
      // keeping what was entered is the safe default
      cancel.focus();
    }

    function close() {
      dialog.hidden = true;
      hideBehind(false);
      $("restart-open").focus();
    }

    async function go() {
      confirm.disabled = true;
      cancel.disabled = true;
      let done = false;
      try {
        done = await restart.go();
      } catch (error) {
        done = false;
      }
      cancel.disabled = false;
      if (done) return;
      close();
      toast("OceanVal could not start again. Please try once more.");
    }

    $("restart-open").addEventListener("click", open);
    cancel.addEventListener("click", close);
    confirm.addEventListener("click", go);
    dialog.addEventListener("click", (event) => { if (event.target === dialog && !confirm.disabled) close(); });
    dialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !confirm.disabled) {
        event.preventDefault();
        close();
      }
    });
  }

  // the model's variables (available, their names) and their long_names
  // (longNames, by name), in a pop-out under a box that can be searched;
  // the table is only built when it is first opened, as a model can have
  // hundreds of variables. get() is what the box holds and set(text) puts a
  // new value in it. With multi, several can be ticked and are summed as
  // var1+var2; without, one at most. Returns the pop-out and sync(), which
  // ticks what the box holds, or null if there are no variables. Shared by
  // the create_recipes window and the oceanval window's Analyze steps.
  function variablesPicker({ available, longNames = {}, get, set, multi, className = "" }) {
    if (!available.length) return null;
    const search = el("input", {
      class: "vars__search",
      type: "search",
      placeholder: "Search variables",
      spellcheck: "false",
      autocomplete: "off",
      autocapitalize: "off",
      "aria-label": "Search the model's variables and their long names",
    });
    const count = el("span", { class: "vars__count vars__shown", "aria-live": "polite" });
    const ticked = el("span", { class: "vars__count vars__ticked" });
    const none = el("p", { class: "vars__none", text: "No variables match.", hidden: "" });
    const scroll = el("div", { class: "vars__scroll" });
    // what the box holds, with long_names, pinned above the table
    const pickedList = el("ul");
    const picked = el("div", { class: "vars__picked", hidden: "" },
      el("p", { class: "vars__picked-title", text: "Selected" }), pickedList);
    const body = el("div", { class: "vars__popover" },
      el("div", { class: "vars__bar" }, search, count, ticked), picked, scroll);
    const details = el("details", { class: `vars ${className}`.trim() },
      el("summary", { text: "Select from the model's variables" }), body);
    let rows = [];
    // the names in the box, which may include ones the model does not have;
    // a single box may also hold z_level or a file, which are not variables
    const named = () => multi
      ? get().split("+").map((name) => name.trim()).filter(Boolean)
      : [get().trim()].filter((name) => available.includes(name));
    // the ticks show what the box holds, wherever it was changed
    const syncTicks = () => {
      const names = new Set(named());
      for (const item of rows) item.box.checked = names.has(item.name);
      ticked.textContent = multi && names.size > 1 ? `${names.size} ticked, summed` : "";
      const used = named();
      picked.hidden = !used.length;
      pickedList.replaceChildren(...used.map((name) => {
        const longName = longNames[name] || "";
        const known = available.includes(name);
        const remove = el("button", {
          type: "button",
          class: "vars__picked-remove",
          title: `Remove ${name}`,
          "aria-label": `Remove ${name}`,
          text: "×",
        });
        remove.addEventListener("click", () => set(named().filter((other) => other !== name).join("+")));
        return el("li", {},
          el("span", { class: "vars__picked-text" },
            el("span", { class: "vars__picked-name", text: name }),
            known ? (longName ? ` ${longName}` : "") : el("span", { class: "vars__picked-note", text: " not in the simulation output" })),
          remove);
      }));
    };
    // every word typed must be in the variable's name or its long_name
    const filter = () => {
      const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
      let shown = 0;
      for (const { tr, text } of rows) {
        tr.hidden = !words.every((word) => text.includes(word));
        if (!tr.hidden) shown += 1;
      }
      none.hidden = shown > 0;
      count.textContent = words.length ? `${shown} of ${rows.length}` : `${rows.length}`;
    };
    search.addEventListener("input", filter);
    search.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (search.value) {
        search.value = "";
        filter();
      } else {
        details.open = false;
        details.querySelector("summary").focus();
      }
    });
    details.addEventListener("toggle", () => {
      if (!details.open) return;
      if (!rows.length) {
        rows = available.map((name) => {
          const longName = longNames[name] || "";
          const box = el("input", { type: "checkbox", class: "tick", "aria-label": `Use ${name}` });
          // ticking several gives their sum, as var1+var2+var3
          box.addEventListener("change", () => {
            const names = multi ? named().filter((used) => used !== name) : [];
            if (box.checked) names.push(name);
            set(names.join("+"));
          });
          return {
            name,
            box,
            text: `${name} ${longName}`.toLowerCase(),
            tr: el("tr", {}, el("td", {}, box), el("td", { text: name }), el("td", { text: longName })),
          };
        });
        scroll.append(
          el("table", {},
            el("thead", {}, el("tr", {}, el("th", { scope: "col", "aria-label": "Use" }),
              el("th", { scope: "col", text: "Variable" }), el("th", { scope: "col", text: "long_name" }))),
            el("tbody", {}, ...rows.map((row) => row.tr))),
          none);
      }
      search.value = "";
      filter();
      syncTicks();
      search.focus();
    });
    return { details, sync: syncTicks };
  }

  // ---- a box with a list of choices, opened by the arrow at its right-hand end ----
  // A box with yearChoices (years, or names: see withChoices) has a list of
  // them to pick from, and anything can still be typed in it. One list serves
  // every such box, placed under (or over) the box it was opened from, and
  // fixed, so that a pop-out's body cannot clip it. A box's choiceHints are
  // shown beside the choices they name, and its onPick(value) is called once
  // a choice is put in it.
  let choiceList = null;
  let choiceOwner = null;
  let choiceActive = -1;

  function withChoices(input, choices) {
    input.yearChoices = choices;
    input.classList.add("has-years");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-haspopup", "listbox");
    input.setAttribute("aria-expanded", "false");
    return input;
  }

  const choicesOpen = () => Boolean(choiceList) && !choiceList.hidden;

  function closeChoices() {
    if (choiceOwner) choiceOwner.setAttribute("aria-expanded", "false");
    choiceOwner = null;
    choiceActive = -1;
    if (choiceList) choiceList.hidden = true;
  }

  function markChoice(index) {
    const options = [...choiceList.children];
    choiceActive = (index + options.length) % options.length;
    options.forEach((option, number) => {
      option.classList.toggle("is-active", number === choiceActive);
      option.setAttribute("aria-selected", String(number === choiceActive));
    });
    options[choiceActive].scrollIntoView({ block: "nearest" });
  }

  function pickChoice(choice) {
    const input = choiceOwner;
    closeChoices();
    input.value = String(choice);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    if (input.onPick) input.onPick(input.value);
    input.focus();
  }

  function openChoices(input) {
    if (!input.yearChoices || input.disabled) return;
    if (!choiceList) {
      choiceList = el("ul", { class: "combo__list year-list", role: "listbox", hidden: true });
      document.body.append(choiceList);
    }
    choiceOwner = input;
    input.setAttribute("aria-expanded", "true");
    // the list is also the preset names of a variable's name (input.onPick), which are not years
    choiceList.classList.toggle("is-names", Boolean(input.onPick));
    // with hints beside the choices, they are in columns, from the left
    choiceList.classList.toggle("is-hinted", Boolean(input.choiceHints));
    choiceList.replaceChildren(...input.yearChoices.map((choice) => el("li", {
      class: "combo__option", role: "option", "aria-selected": "false", text: String(choice),
      // mousedown, so the box keeps its focus
      onmousedown: (event) => { event.preventDefault(); pickChoice(choice); },
    }, (input.choiceHints || {})[choice] ? el("span", { class: "combo__hint", text: input.choiceHints[choice] }) : null)));
    const box = input.getBoundingClientRect();
    const below = window.innerHeight - box.bottom;
    choiceList.style.minWidth = `${box.width}px`;
    choiceList.style.left = `${box.left}px`;
    choiceList.style.top = below >= 200 || below >= box.top ? `${box.bottom + 4}px` : "auto";
    choiceList.style.bottom = below >= 200 || below >= box.top ? "auto" : `${window.innerHeight - box.top + 4}px`;
    choiceList.hidden = false;
    // a list wider than its box, beside the window's edge, is moved in
    const wide = choiceList.getBoundingClientRect().width;
    choiceList.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - wide - 8))}px`;
    // a blank box has none chosen (Number("") is 0, a choice of a depth's)
    const typed = input.value.trim();
    const at = typed === "" ? -1 : input.yearChoices.indexOf(input.onPick ? typed : Number(typed));
    if (at >= 0) markChoice(at);
  }

  // the arrow is the right-hand end of the box: a click there opens the list, one anywhere else in the box only closes it
  document.addEventListener("click", (event) => {
    const input = event.target;
    if (!input.yearChoices) return;
    const open = choicesOpen() && choiceOwner === input;
    if (event.clientX > input.getBoundingClientRect().right - 24) {
      if (open) closeChoices();
      else { closeChoices(); openChoices(input); }
      input.focus();
    } else if (open) closeChoices();
  });
  document.addEventListener("mousedown", (event) => {
    if (choicesOpen() && event.target !== choiceOwner && !choiceList.contains(event.target)) closeChoices();
  });
  document.addEventListener("keydown", (event) => {
    const input = event.target;
    if (!input.yearChoices) return;
    if (!choicesOpen() || choiceOwner !== input) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        closeChoices();
        openChoices(input);
      }
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      markChoice(choiceActive + (event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "Enter" && choiceActive >= 0) {
      event.preventDefault();
      pickChoice(input.yearChoices[choiceActive]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeChoices();
    } else if (event.key === "Tab") {
      closeChoices();
    }
  });
  document.addEventListener("input", (event) => { if (choicesOpen() && event.target === choiceOwner) closeChoices(); });
  // a list left behind when the box moves
  document.addEventListener("scroll", (event) => { if (choicesOpen() && event.target !== choiceList) closeChoices(); }, true);
  window.addEventListener("resize", closeChoices);

  return {
    ICONS, $, el, icon, shorten, plural, toast, pageId, heartbeat, tellVisibility, mountChrome, variablesPicker,
    withChoices, choicesOpen, closeChoices,
  };
})();
