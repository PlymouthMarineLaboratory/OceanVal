// The helpers shared by the create_recipes window and the oceanval app,
// inlined into each page ahead of its own script.
const OceanValGUI = (() => {
  "use strict";

  const ICONS = {
    warn: '<svg viewBox="0 0 20 20"><path d="M10 2.6 1.9 16.8h16.2L10 2.6Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M10 8v3.8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="10" cy="14.2" r="1" fill="currentColor"/></svg>',
    error: '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="m7.4 7.4 5.2 5.2m0-5.2-5.2 5.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.3 4.3L19 7.2" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    cross: '<svg viewBox="0 0 24 24"><path d="m7 7 10 10M17 7 7 17" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>',
    plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>',
    // the i in a circle: the About button, and the buttons that say what an option plots
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 17v-6M12 7.5h.01" stroke-width="2.6"/></svg>',
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
        const reply = await response.json();
        if (reply.closed) return;
        // the app has found the simulation cannot be worked with: its page says why
        if (reply.blocked) { location.assign(`${root || "./"}?token=${encodeURIComponent(token)}`); return; }
      } catch (error) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
    }
  }

  // tells the app when this page's tab is not looked at, and when it is
  // again, as a browser may then slow the page's requests or freeze it
  // (Safari does), which the app would otherwise take for a closed window
  // (see App.page_visibility). keepalive, so that it is sent as the tab goes.
  // A tab being closed says it is hidden first, which would look like one left
  // in the background, so a page being unloaded says so too (pagehide; one
  // kept to go Back to, persisted, is not)
  function tellVisibility(token, root) {
    const tell = (closing) => {
      fetch(`${root}api/visibility`, {
        method: "POST",
        cache: "no-store",
        keepalive: true,
        headers: { "Content-Type": "application/json", "X-OceanVal-Token": token },
        body: JSON.stringify({ page: pageId, hidden: document.hidden, closing: closing === true }),
      }).catch(() => {});
    };
    document.addEventListener("visibilitychange", () => tell());
    window.addEventListener("pagehide", (event) => { if (!event.persisted) tell(true); });
    if (document.hidden) tell();
  }

  // ---- the header's buttons, and the panels they open ----
  // the cog (preferences, kept in the home .oceanvalrc) and the i (about), as
  // the oceanval app and the create_recipes window both have them, at the top
  // left beside the wordmark. Every page calls this once, after its own
  // script has what the panels need:
  //   state()        the page's state, with oceanval_version, datasets and
  //                  user_settings ({ values, path })
  //   post(path, b)  the page's own POST, which answers with the reply's JSON
  //   settingsPath   where post sends the preferences, from the page's address
  //   behind         the parts of the page that cannot be reached while a panel is open
  //   onSaved()      called once the preferences are saved (the page may show them)
  const COG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';
  const RESTART = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>';
  const INFO = ICONS.info;

  const CHROME_HTML = `
<div class="picker prefs" id="preferences" hidden>
  <div class="picker__card prefs__card" role="dialog" aria-modal="true" aria-labelledby="preferences-title" aria-describedby="settings-where">
    <div class="prefs__head">
      <h2 id="preferences-title">Preferences</h2>
      <p class="prefs__where" id="settings-where"></p>
    </div>
    <div class="prefs__body">
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
      <footer class="info__credit">
        <div class="info__credit-row">
          <a href="https://pml.ac.uk/" target="_blank" rel="noopener" class="info__credit-logo"><img src="__OCEANVAL_PML_LOGO__" alt="Plymouth Marine Laboratory"></a>
          <span>Developed by the Environmental Intelligence group at Plymouth Marine Laboratory</span>
        </div>
        <div class="info__credit-row info__credit-legal">
          <span>Released under the <a href="https://www.gnu.org/licenses/gpl-3.0.html" target="_blank" rel="noopener">GNU GPLv3 license</a>.</span>
          <span class="info__badge">GPLv3</span>
        </div>
      </footer>
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
      const { values, path } = state().user_settings;
      $("settings-where").replaceChildren("Saved in ", el("code", { text: path }),
        ", and used each time OceanVal's window opens.");
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
      $("settings-show-demo").focus();
    }

    function closeSettings() {
      $("preferences").hidden = true;
      hideBehind(false);
      $("settings-open").focus();
    }

    async function applySettings() {
      const settings = {
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
  // was entered, so it is asked about (one sentence and two buttons), with Cancel the default
  function mountRestart({ restart, hideBehind }) {
    const cancel = el("button", { type: "button", class: "btn btn--ghost", id: "restart-cancel", text: "Cancel" });
    const confirm = el("button", { type: "button", class: "btn btn--primary", id: "restart-go", text: "Restart" });
    const dialog = el("div", { class: "picker", id: "restart-confirm", hidden: "" },
      el("div", { class: "picker__card restart__card", role: "dialog", "aria-modal": "true", "aria-labelledby": "restart-title" },
        el("div", { class: "restart__body" },
          el("h2", { id: "restart-title", text: "Begin again with a clean slate" })),
        el("div", { class: "picker__foot" }, cancel, confirm)));
    document.body.append(dialog);

    function open() {
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

  // ---- searching variables by name, long_name (and units) ----
  // The words typed are all required. A word is a plain substring of the
  // fields joined by a space, unless it has * (any characters) or ? (one
  // character): that is matched within one field, so it never runs from a
  // name into its long_name. Case is ignored and nothing is anchored, so
  // P*_Chl finds P1_Chl, P2_Chl and P10_Chl. Anything else typed is literal.
  // Returns {active, test(fields)}, active being whether anything was typed.
  function searchMatcher(query) {
    const terms = String(query || "").toLowerCase().split(/\s+/).filter(Boolean).map((term) => {
      if (!/[*?]/.test(term)) return { plain: term };
      const source = term.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*+/g, ".*").replace(/\?/g, ".");
      return { pattern: new RegExp(source) };
    });
    return {
      active: terms.length > 0,
      test(fields) {
        const lowered = fields.map((field) => String(field || "").toLowerCase());
        const joined = lowered.join(" ");
        return terms.every((term) => term.pattern ? lowered.some((field) => term.pattern.test(field)) : joined.includes(term.plain));
      },
    };
  }

  // ---- a box for variables' names, with the model's variables in a pop-out under it ----
  // available are the variables' names and longNames their long_names, by
  // name. Anything can be typed in input. Once it has the focus, a pop-out
  // under it lists the variables, a tick, the name and the long_name, with
  // those the box holds ticked and listed at the top (Selected). The box is
  // the pop-out's search: the rows shown are those matching the part being
  // typed (searchMatcher, so P*_Chl finds P1_Chl and P2_Chl): with multi, the
  // part between the +s the caret is in, so "P1_Chl+" ticks P1_Chl and shows
  // every row again; without, the whole box. A click on a row ticks it and
  // adds it to the box (multi: summed with +, replacing a half-typed search;
  // single: it is the box's value), a click on a ticked row or its x in
  // Selected takes it out; every row is shown again after a click. extra
  // (such as z_level) are rows at the top. Every change is an input event,
  // as if it had been typed. Returns update(), for variables that are known
  // later, and close(). The table is built when it is first shown, as a model
  // can have hundreds of variables. Shared by the create_recipes window and
  // the oceanval window's Analyze, Compare simulations and own data steps.
  let openVariables = null;

  function variableBox(input, { available = [], longNames = {}, multi = false, extra = [], what = "simulation output" } = {}) {
    let names = available.slice();
    let longs = { ...longNames };
    let rows = [];
    let built = false;
    let active = -1;
    // after a click every row is shown, until the box is typed in again
    let showAll = false;
    input.classList.add("has-vars");
    input.setAttribute("aria-haspopup", "dialog");
    input.setAttribute("aria-expanded", "false");

    const count = el("span", { class: "vars__count vars__shown", "aria-live": "polite" });
    const ticked = el("span", { class: "vars__count vars__ticked" });
    const none = el("p", { class: "vars__none", text: "No variables match.", hidden: "" });
    const scroll = el("div", { class: "vars__scroll" });
    const pickedList = el("ul");
    const picked = el("div", { class: "vars__picked", hidden: "" },
      el("p", { class: "vars__picked-title", text: "Selected" }), pickedList);
    const popover = el("div", { class: "vars__popover", hidden: "" },
      el("div", { class: "vars__bar" }, count, ticked), picked, scroll);
    // a click in the pop-out (a row, an x, its scroll bar) leaves the focus in the box
    popover.addEventListener("mousedown", (event) => event.preventDefault());

    const everyName = () => [...extra.filter((name) => !names.includes(name)), ...names];
    const isName = (name) => names.includes(name) || extra.includes(name);
    // the names in the box: with multi, those between the +s (any, the model's or not); without, the box if it is a variable's
    const inBox = () => multi
      ? input.value.split("+").map((name) => name.trim()).filter(Boolean)
      : [input.value.trim()].filter(isName);
    // where the part searched starts and ends in the box, and what it is
    const part = () => {
      const text = input.value;
      if (!multi) return { start: 0, end: text.length, text: text.trim() };
      const caret = input.selectionStart === null ? text.length : input.selectionStart;
      const start = caret > 0 ? text.lastIndexOf("+", caret - 1) + 1 : 0;
      const next = text.indexOf("+", caret);
      return { start, end: next < 0 ? text.length : next, text: text.slice(start, next < 0 ? text.length : next).trim() };
    };
    const isOpen = () => !popover.hidden;

    // puts text in the box, as if typed, with the caret at its end
    function setBox(text) {
      input.value = text;
      input.setSelectionRange(text.length, text.length);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function choose(name) {
      const used = inBox();
      if (used.includes(name)) {
        setBox(multi ? used.filter((other) => other !== name).join("+") : "");
      } else if (!multi) {
        setBox(name);
        close();
        return;
      } else {
        const { start, end, text } = part();
        // a half-typed search is replaced; otherwise the name is added at the end
        if (text && !isName(text)) {
          const value = input.value;
          setBox((value.slice(0, start) + name + value.slice(end)).split("+").map((one) => one.trim()).filter(Boolean).join("+"));
        } else {
          setBox([...used, name].join("+"));
        }
      }
      showAll = true;
      refresh();
    }

    function build() {
      rows = everyName().map((name) => {
        const longName = longs[name] || "";
        const box = el("input", { type: "checkbox", class: "tick", tabindex: "-1", "aria-label": `Use ${name}` });
        box.addEventListener("click", (event) => { event.preventDefault(); choose(name); });
        const tr = el("tr", {}, el("td", {}, box), el("td", { text: name }), el("td", { text: longName }));
        // a click anywhere on the row ticks it
        tr.addEventListener("click", (event) => { if (event.target !== box) choose(name); });
        return { name, box, fields: [name, longName], tr };
      });
      scroll.replaceChildren(
        el("table", {},
          el("thead", {}, el("tr", {}, el("th", { scope: "col", "aria-label": "Use" }),
            el("th", { scope: "col", text: "Variable" }), el("th", { scope: "col", text: "long_name" }))),
          el("tbody", {}, ...rows.map((row) => row.tr))),
        none);
      built = true;
    }

    function mark(index) {
      const shown = rows.filter((row) => !row.tr.hidden);
      rows.forEach((row) => row.tr.classList.remove("is-active"));
      if (!shown.length) { active = -1; return; }
      active = (index + shown.length) % shown.length;
      const tr = shown[active].tr;
      tr.classList.add("is-active");
      // scrolled within the pop-out only, never the page
      if (tr.offsetTop < scroll.scrollTop + 30) scroll.scrollTop = Math.max(0, tr.offsetTop - 30);
      else if (tr.offsetTop + tr.offsetHeight > scroll.scrollTop + scroll.clientHeight) scroll.scrollTop = tr.offsetTop + tr.offsetHeight - scroll.clientHeight;
    }

    // the rows shown, the ticks and Selected, as the box is now
    function refresh() {
      if (!isOpen()) return;
      // the part being typed is a search, not a choice, until it is a variable's name
      const typing = part().text;
      const used = inBox().filter((name) => isName(name) || showAll || name !== typing);
      const usedSet = new Set(used);
      const searched = showAll ? "" : typing;
      // a single box holding a variable's name shows every row, with it ticked
      const wanted = searchMatcher(!multi && isName(searched) ? "" : searched);
      let shown = 0;
      for (const row of rows) {
        row.box.checked = usedSet.has(row.name);
        row.tr.hidden = !wanted.test(row.fields);
        if (!row.tr.hidden) shown += 1;
      }
      none.hidden = shown > 0;
      count.textContent = wanted.active ? `${shown} of ${rows.length}` : `${rows.length}`;
      ticked.textContent = multi && usedSet.size > 1 ? `${usedSet.size} ticked, summed` : "";
      picked.hidden = !used.length;
      pickedList.replaceChildren(...used.map((name) => {
        const remove = el("button", { type: "button", class: "vars__picked-remove", title: `Remove ${name}`, "aria-label": `Remove ${name}`, text: "×" });
        remove.addEventListener("click", () => choose(name));
        return el("li", {},
          el("span", { class: "vars__picked-text" },
            el("span", { class: "vars__picked-name", text: name }),
            isName(name) ? (longs[name] ? ` ${longs[name]}` : "") : el("span", { class: "vars__picked-note", text: ` not in the ${what}` })),
          remove);
      }));
      active = -1;
      rows.forEach((row) => row.tr.classList.remove("is-active"));
      placeList(popover, input);
    }

    function open() {
      if (input.disabled || !everyName().length) return;
      if (openVariables && openVariables !== close) openVariables();
      if (!popover.isConnected) document.body.append(popover);
      if (!built) build();
      popover.hidden = false;
      openVariables = close;
      input.setAttribute("aria-expanded", "true");
      scroll.scrollTop = 0;
      refresh();
    }

    function close() {
      if (popover.hidden) return;
      popover.hidden = true;
      showAll = false;
      if (openVariables === close) openVariables = null;
      input.setAttribute("aria-expanded", "false");
    }

    input.addEventListener("focus", open);
    input.addEventListener("click", () => { if (isOpen()) refresh(); else open(); });
    input.addEventListener("input", (event) => {
      // typed: the search is what is typed again
      if (event.isTrusted) showAll = false;
      if (document.activeElement !== input) return;
      if (isOpen()) refresh(); else open();
    });
    input.addEventListener("blur", close);
    // the caret moved to another part
    input.addEventListener("keyup", (event) => {
      if (isOpen() && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { showAll = false; refresh(); }
    });
    input.addEventListener("keydown", (event) => {
      if (!isOpen()) {
        if (event.key === "ArrowDown") { event.preventDefault(); open(); }
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        mark(event.key === "ArrowDown" ? active + 1 : active < 0 ? -1 : active - 1);
      } else if (event.key === "Enter" && active >= 0) {
        event.preventDefault();
        const shown = rows.filter((row) => !row.tr.hidden);
        choose(shown[active].name);
      } else if (event.key === "Escape") {
        // the pop-out closes first, not the pop-out the box is in
        event.preventDefault();
        event.stopPropagation();
        close();
      } else if (event.key === "Tab") {
        close();
      }
    });

    return {
      update({ available: newNames = [], longNames: newLongs = {} } = {}) {
        names = newNames.slice();
        longs = { ...newLongs };
        built = false;
        if (isOpen()) { build(); refresh(); }
        if (!everyName().length) close();
      },
      close,
    };
  }

  // a pop-out left behind when its box moves follows it; a scroll anywhere
  // else on the page (such as a log the window writes to) leaves it alone
  document.addEventListener("scroll", (event) => {
    const popover = document.querySelector(".vars__popover:not([hidden])");
    if (!popover || popover.contains(event.target)) return;
    const box = document.querySelector('input.has-vars[aria-expanded="true"]');
    if (!box) return;
    const scrolled = event.target === document ? document.documentElement : event.target;
    if (scrolled !== box && scrolled.contains && scrolled.contains(box)) placeList(popover, box);
  }, true);
  window.addEventListener("resize", () => { if (openVariables) openVariables(); });

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

  // a fixed list put under its box (or over it, if there is more room there),
  // and moved in from the window's edge if it is wider than the box
  function placeList(list, input) {
    const box = input.getBoundingClientRect();
    const below = window.innerHeight - box.bottom;
    list.style.minWidth = `${box.width}px`;
    list.style.left = `${box.left}px`;
    list.style.top = below >= 200 || below >= box.top ? `${box.bottom + 4}px` : "auto";
    list.style.bottom = below >= 200 || below >= box.top ? "auto" : `${window.innerHeight - box.top + 4}px`;
    list.hidden = false;
    const wide = list.getBoundingClientRect().width;
    list.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - wide - 8))}px`;
  }

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
    placeList(choiceList, input);
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

  // ---- the browser never offers what was typed before ----
  // Every box, in the markup or made later, has autocomplete="off" (Chrome
  // ignores "chrome-off" and a form's own "off"). The simulation directories
  // are remembered by OceanVal itself (.oceanvalcache), not by the browser.
  const NOT_TYPED = new Set(["hidden", "checkbox", "radio", "range", "button", "submit", "reset", "file", "image", "color"]);
  const typedIn = (node) => node.tagName === "TEXTAREA" || (node.tagName === "INPUT" && !NOT_TYPED.has(node.type));
  function forget(node) {
    if (!node || node.nodeType !== 1) return;
    const boxes = [...node.querySelectorAll("input, textarea, form")];
    if (node.matches("input, textarea, form")) boxes.push(node);
    for (const box of boxes) {
      if ((box.tagName === "FORM" || typedIn(box)) && box.getAttribute("autocomplete") !== "off") {
        box.setAttribute("autocomplete", "off");
      }
    }
  }
  forget(document.documentElement);
  new MutationObserver((changes) => {
    for (const change of changes) for (const node of change.addedNodes) forget(node);
  }).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("focusin", (event) => forget(event.target), true);

  // ---- Required tags are shown only while what they ask for is empty ----
  // A tag (.own-req, .year-required) gets is-satisfied once its box is filled, and CSS hides it; the red bar and
  // borders of a filled box go the same way. Only is-satisfied/is-filled are set here, never `hidden` or
  // is-required, which the pages use themselves. The box is found from the tag: data-required-for (ids), the
  // label's `for`, the one box in its label/row/field, or a legend's fieldset. A tag in a table's head covers the
  // columns it spans: data-required-tick = every ticked row has one of them ticked, data-required-any = some row
  // is ticked, otherwise every ticked row has the boxes filled.
  const TAGS = ".own-req, .year-required";
  const BOXES = "input, select, textarea";
  const isFilled = (box) => {
    if (box.type === "checkbox" || box.type === "radio") return box.checked;
    return box.value.trim() !== "";
  };
  const groupFilled = (boxes) => {
    if (!boxes.length) return false;
    const radios = boxes.filter((box) => box.type === "radio");
    const others = boxes.filter((box) => box.type !== "radio");
    const named = new Map();
    for (const radio of radios) named.set(radio.name, (named.get(radio.name) || false) || radio.checked);
    return [...named.values()].every(Boolean) && others.every(isFilled);
  };
  const chosenIn = (node) => node.querySelector(".is-chosen, [aria-checked='true'], [aria-pressed='true']") !== null;
  function ownersOf(tag) {
    const given = tag.getAttribute("data-required-for");
    if (given) return given.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean);
    const label = tag.closest("label");
    if (label && label.htmlFor) return [document.getElementById(label.htmlFor)].filter(Boolean);
    const legend = tag.closest("legend");
    if (legend) return [legend.closest("fieldset")].filter(Boolean);
    const holder = tag.closest("label, .g-row, .own-field");
    if (!holder) return [];
    const boxes = [...holder.querySelectorAll(BOXES)];
    return boxes.length > 1 && boxes.every((box) => box.type === "checkbox" || box.type === "radio") ? boxes : boxes.slice(0, 1);
  }
  function columnsOf(cell) {
    let first = 0;
    for (const sibling of cell.parentElement.children) {
      if (sibling === cell) break;
      first += sibling.colSpan;
    }
    return [first, first + cell.colSpan];
  }
  function tableSatisfied(tag, cell) {
    const [from, to] = columnsOf(cell);
    const rows = [...cell.closest("table").querySelectorAll("tbody:not(.analyze-all) > tr")]
      .filter((row) => row.children.length > 1);
    const spanned = (row) => [...row.children].slice(from, to).flatMap((td) => [...td.querySelectorAll(BOXES)])
      .filter((box) => box.type !== "hidden");
    if (tag.hasAttribute("data-required-any")) return rows.some((row) => spanned(row).some((box) => box.checked));
    const ticked = rows.filter((row) => row.classList.contains("is-on"));
    if (!ticked.length) return false;
    if (tag.hasAttribute("data-required-tick")) {
      return ticked.every((row) => spanned(row).some((box) => box.type === "checkbox" && box.checked));
    }
    return ticked.every((row) => {
      const boxes = spanned(row).filter((box) => box.type === "text" || box.tagName === "SELECT");
      return boxes.length > 0 && boxes.every(isFilled);
    });
  }
  function ownerFilled(owner) {
    if (owner.matches("input[type=radio]") && owner.name) {
      return groupFilled([...document.querySelectorAll(`input[type=radio][name="${owner.name}"]`)]);
    }
    if (owner.matches(BOXES)) return isFilled(owner);
    // a fieldset or group of choices: its radios, else the boxes in it
    const radios = [...owner.querySelectorAll("input[type=radio]")];
    if (radios.length) return groupFilled(radios) || chosenIn(owner);
    const boxes = [...owner.querySelectorAll(BOXES)];
    return boxes.length ? boxes.every(isFilled) : chosenIn(owner);
  }
  function syncRequired(root = document) {
    for (const tag of root.querySelectorAll(TAGS)) {
      const cell = tag.closest("thead th");
      let satisfied;
      if (cell && !tag.hasAttribute("data-required-for")) {
        satisfied = tableSatisfied(tag, cell);
      } else {
        const owners = ownersOf(tag);
        satisfied = owners.length > 0 && owners.every(ownerFilled);
      }
      tag.classList.toggle("is-satisfied", satisfied);
      const field = tag.closest(".own-field");
      if (field) field.classList.toggle("is-satisfied", satisfied);
    }
    // the boxes that are red because they must be given are plain once they are
    for (const box of root.querySelectorAll(".analyze-dialog .s-input, .analyze-dialog .s-select, .own-field .s-input, .own-field .s-select")) {
      box.classList.toggle("is-filled", box.value.trim() !== "");
    }
  }
  let requiredQueued = false;
  function queueRequired() {
    if (requiredQueued) return;
    requiredQueued = true;
    queueMicrotask(() => { requiredQueued = false; syncRequired(); });
  }
  for (const type of ["input", "change", "click"]) document.addEventListener(type, queueRequired, true);
  new MutationObserver(queueRequired).observe(document.documentElement, {
    childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "class", "disabled"],
  });
  // a value written by a script fires no event
  setInterval(() => { if (!document.hidden) syncRequired(); }, 300);
  syncRequired();

  return {
    ICONS, $, el, icon, shorten, plural, toast, pageId, heartbeat, tellVisibility, mountChrome, variableBox, searchMatcher,
    withChoices, choicesOpen, closeChoices, syncRequired,
  };
})();
