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

  // Chrome remembers what was typed under a box's name, and offers it again next time. Every typed box
  // is given a name that is new for each page load, so nothing remembered can match (see forget() below).
  const LOAD_NAME = `ov-${Math.random().toString(36).slice(2)}-`;
  let boxCount = 0;
  const unremembered = (box) => {
    if (!(box.getAttribute("name") || "").startsWith(LOAD_NAME)) box.setAttribute("name", `${LOAD_NAME}${boxCount++}`);
  };

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
    // typed boxes are never filled by the browser (see forget() below)
    if ((tag === "textarea" || tag === "input") && !node.hasAttribute("autocomplete")) node.setAttribute("autocomplete", "oceanval-none");
    if (tag === "textarea" || (tag === "input" && !/^(hidden|checkbox|radio|range|button|submit|reset|file|image|color)$/.test(node.type))) unremembered(node);
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

  // ---- the header's buttons ----
  // Back (the arrow) and Restart, as the oceanval app and the create_recipes window hosted by it have them, at the top
  // left beside the wordmark. Preferences and About are pages of the app's Tools & checks, not buttons here.
  // Every page calls mountChrome once:
  //   behind         the parts of the page that cannot be reached while the Restart panel is open
  const COG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>';
  const RESTART = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>';
  const BACK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><path d="m11 6-6 6 6 6"/></svg>';

  // the PML logo of the About page (recipes_gui.render_page puts the picture in)
  const PML_LOGO = "__OCEANVAL_PML_LOGO__";

  // restart: { go, running }, for a page that can start again from the first
  // page. go() asks the app to forget everything (and moves the page on),
  // returning whether it did; running() is whether a run would be stopped.
  //
  // back: true, for a page that goes back a step: the arrow to the left of Restart, greyed out until the page gives it
  // something to do with setBack({handler, title}) (and again with null where there is nothing to go back to)
  function mountChrome({ behind, restart = null, back = false }) {
    const brand = document.querySelector(".brand");
    brand.append(...[
      back ? el("button", { type: "button", class: "info-open back-open", id: "back-open", "aria-label": "Back", title: "Back", disabled: true,
        onclick: () => { if (backAction) backAction.handler(); } },
        el("span", { class: "back-open__icon", "aria-hidden": "true", html: BACK })) : null,
      restart ? el("button", { type: "button", class: "info-open restart-open", id: "restart-open", "aria-haspopup": "dialog",
        "aria-controls": "restart-confirm", "aria-label": "Restart", title: "Restart" },
        el("span", { class: "restart-open__icon", "aria-hidden": "true", html: RESTART })) : null,
    ].filter(Boolean));
    keepMastheadHeight();

    const hideBehind = (hidden) => { behind().forEach((node) => { node.inert = hidden; }); };

    if (restart) mountRestart({ restart, hideBehind });
  }

  // the button's pop-up: starting again from the first page throws away what
  // was entered, so it is asked about (one sentence and two buttons), with Cancel the default
  // what the arrow beside Restart does now: {handler, title}, or null where it cannot be pressed
  let backAction = null;
  function setBack(action) {
    backAction = action || null;
    const button = $("back-open");
    if (!button) return;
    button.disabled = !backAction;
    button.title = (backAction && backAction.title) || "Back";
  }

  // the header stays at the top as the page scrolls, so what else sticks to the top (a table's head, the viewer's
  // side panel) sits below it by --masthead-h, which is its height now (the page's title can wrap)
  function keepMastheadHeight() {
    const masthead = document.querySelector(".masthead");
    if (!masthead) return;
    const set = () => document.documentElement.style.setProperty("--masthead-h", `${masthead.offsetHeight}px`);
    set();
    if (window.ResizeObserver) new ResizeObserver(set).observe(masthead);
    else window.addEventListener("resize", set);
  }

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

  // ---- a model variable: one variable, a sum (a+b) or a formula in CDO's expr (a+2*b) ----
  // The window's own reading of it, as oceanval.model_expr reads it (the server checks it again):
  // the same grammar and the same messages, so what is typed is told at once. The functions OceanVal
  // takes, the constants and the functions it refuses are model_expr's (recipes_gui.render_page puts them in).
  const EXPR_TABLES = /*__OCEANVAL_EXPR_TABLES__*/ { functions: {}, constants: [], refused: {} };
  const EXPR_OPERATORS = ["<=>", "==", "!=", "<=", ">=", "&&", "||", "<", ">", "+", "-", "*", "/", "^", "!", "?", ":", "(", ")", ","];
  const EXPR_LEVELS = [["&&", "||"], ["==", "!=", "<=>", "<", ">", "<=", ">="], ["+", "-"], ["*", "/"]];
  const EXPR_BINARY_ONLY = ["*", "/", "^", "==", "!=", "<=>", "<", ">", "<=", ">=", "&&", "||"];
  const EXPR_NUMBER = /^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/;
  const EXPR_NAME = /^[A-Za-z_][A-Za-z0-9_.]*/;
  const EXPR_WORD = /^[A-Za-z0-9_.]+/;
  const EXPR_FORMULA_CHARACTERS = /[\s+\-*/^()<>=!?:&|,;]/;
  const EXPR_NESTED = "Put brackets around the ?: inside another: CDO cannot tell which is which.";

  class ExpressionError extends Error {
    constructor(message, start, end) { super(message); this.start = start; this.end = end === undefined ? start : end; }
  }

  function exprIsOneName(text) {
    const stripped = String(text).trim();
    return Boolean(stripped) && !EXPR_FORMULA_CHARACTERS.test(stripped)
      && !new RegExp(`^${EXPR_NUMBER.source.slice(1)}$`).test(stripped) && !EXPR_TABLES.constants.includes(stripped);
  }

  function exprTokens(text) {
    const found = [];
    let i = 0;
    while (i < text.length) {
      const char = text[i];
      const rest = text.slice(i);
      if (/\s/.test(char)) { i += 1; continue; }
      if (/\d/.test(char) || (char === "." && /\d/.test(text[i + 1] || ""))) {
        const number = EXPR_NUMBER.exec(rest)[0];
        // a number ends where something that is not a letter, a digit, _ or . comes (1e-3 is one)
        const following = EXPR_WORD.exec(rest.slice(number.length));
        if (!following) {
          found.push({ kind: "number", text: number, start: i, end: i + number.length });
          i += number.length;
          continue;
        }
        const first = EXPR_WORD.exec(rest)[0];
        const word = rest.slice(0, Math.max(first.length, number.length + following[0].length));
        const digits = /^\d+(?:\.\d*)?/.exec(word);
        const after = digits ? word.slice(digits[0].length) : "";
        if (digits && new RegExp(`^${EXPR_NAME.source.slice(1)}$`).test(after) && !/^[eE]\d*$/.test(after)) {
          throw new ExpressionError(`Write ${digits[0]}*${after}, not ${word}: CDO needs a * between a number and a variable.`, i, i + word.length);
        }
        throw new ExpressionError(`“${word}” is not a number.`, i, i + word.length);
      }
      if (/[A-Za-z_]/.test(char)) {
        const name = EXPR_NAME.exec(rest)[0];
        found.push({ kind: "name", text: name, start: i, end: i + name.length });
        i += name.length;
        continue;
      }
      if (rest.startsWith("**")) throw new ExpressionError("Use ^ for powers, not **.", i, i + 2);
      const operator = EXPR_OPERATORS.find((op) => rest.startsWith(op));
      if (operator) {
        found.push({ kind: "op", text: operator, start: i, end: i + operator.length });
        i += operator.length;
        continue;
      }
      if (char === "=") throw new ExpressionError("Give only the formula, without “=”: OceanVal names the result itself (use == to compare).", i, i + 1);
      if (char === ";") throw new ExpressionError("Give one formula, without “;”.", i, i + 1);
      if (char === "&" || char === "|") throw new ExpressionError(`Use ${char}${char}, not ${char}.`, i, i + 1);
      throw new ExpressionError(`“${char}” cannot be used in a formula.`, i, i + 1);
    }
    found.push({ kind: "end", text: "", start: text.length, end: text.length });
    return found;
  }

  function exprHasVariable(node) {
    if (node[0] === "var") return true;
    const children = node[0] === "call" ? node[2] : node.slice(1).filter(Array.isArray);
    return children.some(exprHasVariable);
  }

  function exprParse(text) {
    const tokens = exprTokens(text);
    const names = [];
    let at = 0;
    const peek = () => tokens[at];
    const take = () => tokens[at++];
    const isOp = (...ops) => peek().kind === "op" && ops.includes(peek().text);
    const fail = (message, token = peek(), start, end) => {
      throw new ExpressionError(message, start === undefined ? token.start : start, end === undefined ? token.end : end);
    };
    function unexpected(token) {
      const before = at ? tokens[at - 1] : null;
      if (token.kind === "op" && token.text === ")") fail("There is a ) with no ( before it.", token);
      if (token.kind === "op" && token.text === "?") fail(EXPR_NESTED, token);
      if (token.kind === "op" && token.text === ":") fail("There is a : with no ? before it.", token);
      if (token.kind === "op" && token.text === ",") fail("A comma can only part the values a function is given.", token);
      if (token.kind === "op" && token.text === "(" && before && before.kind === "name") fail(`“${before.text}” is not a CDO function.`, before, undefined, token.end);
      if (token.kind === "name" && (token.text === "and" || token.text === "or")) fail(`Use ${token.text === "and" ? "&&" : "||"}, not “${token.text}”.`, token);
      if (token.kind === "name" && token.text === "exp") fail("Use ^ for powers: exp(x) is e to the power of x.", token);
      fail(`Put an operator (such as + or *) between “${before ? before.text : ""}” and “${token.text || "the end"}”.`, token);
    }
    function operandAfter(operator) {
      const token = peek();
      if (token.kind === "end" || (token.kind === "op" && [")", ",", ":", "?", ...EXPR_BINARY_ONLY].includes(token.text))) {
        fail(`A ${operator.text} needs something on each side.`, operator);
      }
    }
    function ternary() {
      const start = peek();
      const condition = binary(0);
      if (!isOp("?")) return condition;
      take();
      const yes = binary(0);
      if (isOp("?")) fail(EXPR_NESTED);
      if (!isOp(":")) fail(`The ? needs a : after “${text.slice(start.start, peek().start).trim()}”.`);
      take();
      const no = binary(0);
      if (isOp("?")) fail(EXPR_NESTED);
      const found = ["if", condition, yes, no];
      if (!exprHasVariable(found)) {
        fail("A ?: needs a variable of the simulation in it: CDO cannot work one out of numbers alone.", start, start.start, tokens[at - 1].end);
      }
      return found;
    }
    function binary(level) {
      if (level === EXPR_LEVELS.length) return unary();
      let left = binary(level + 1);
      while (isOp(...EXPR_LEVELS[level])) {
        const operator = take();
        operandAfter(operator);
        left = ["op", operator.text, left, binary(level + 1)];
      }
      return left;
    }
    function unary() {
      const token = peek();
      if (token.kind === "op" && (token.text === "-" || token.text === "!")) {
        take();
        operandAfter(token);
        return [token.text === "-" ? "neg" : "not", unary()];
      }
      if (token.kind === "op" && token.text === "+") fail("A + needs something on each side.", token);
      if (token.kind === "op" && EXPR_BINARY_ONLY.includes(token.text)) fail(`A ${token.text} needs something on each side.`, token);
      return power();
    }
    function power() {
      const base = primary();
      if (isOp("^")) {
        const operator = take();
        operandAfter(operator);
        return ["op", "^", base, unary()];
      }
      return base;
    }
    function primary() {
      const token = take();
      if (token.kind === "number") return ["num", Number(token.text)];
      if (token.kind === "op" && token.text === "(") {
        if (isOp(")")) fail("There is nothing between ( and ).", token, token.start, peek().end);
        const inner = ternary();
        if (!isOp(")")) {
          if (peek().kind === "end") fail("There is a ( with no ) after it.", token);
          unexpected(peek());
        }
        take();
        return inner;
      }
      if (token.kind === "name") {
        if (isOp("(")) return call(token);
        if (EXPR_TABLES.constants.includes(token.text)) return ["const", token.text];
        if (token.text === "_ALL_") fail("_ALL_ cannot be used in a formula: name the variables.", token);
        if (!names.includes(token.text)) names.push(token.text);
        return ["var", token.text];
      }
      if (token.kind === "end") fail("The formula ends too soon.", token);
      at -= 1;
      unexpected(token);
    }
    function call(name) {
      const fn = name.text;
      if (fn in EXPR_TABLES.refused) fail(`“${fn}” ${EXPR_TABLES.refused[fn]}.`, name);
      if (!(fn in EXPR_TABLES.functions)) {
        if (EXPR_TABLES.constants.includes(fn)) fail(`${fn} is a number, not a function.`, name);
        // the server's message names the function it is closest to, if any
        const same = Object.keys(EXPR_TABLES.functions).find((known) => known.toLowerCase() === fn.toLowerCase());
        fail(`“${fn}” is not a function OceanVal can use in a formula.${same ? ` Did you mean ${same}?` : ""}`, name);
      }
      const opening = take();
      const args = [];
      if (!isOp(")")) {
        for (;;) {
          if (peek().kind === "end") fail(`There is a ( with no ) after it, in ${fn}(.`, opening);
          args.push(binary(0));
          if (isOp("?")) fail(`Put brackets around a ?: given to ${fn}: CDO cannot read it without them.`);
          if (isOp(",")) {
            const comma = take();
            if (isOp(")")) fail("Give a value after the comma.", comma);
            continue;
          }
          break;
        }
      }
      if (!isOp(")")) {
        if (peek().kind === "end") fail(`There is a ( with no ) after it, in ${fn}(.`, opening);
        unexpected(peek());
      }
      const closing = take();
      const wanted = EXPR_TABLES.functions[fn];
      if (args.length !== wanted) fail(`${fn} takes ${wanted} value${wanted > 1 ? "s" : ""}, not ${args.length}.`, name, undefined, closing.end);
      return ["call", fn, args];
    }
    if (peek().kind === "end") fail("Give a formula.");
    const tree = ternary();
    if (peek().kind !== "end") unexpected(peek());
    return { tree, names, tokens };
  }

  function exprIsSum(node) {
    return node[0] === "var" || (node[0] === "op" && node[1] === "+" && exprIsSum(node[2]) && exprIsSum(node[3]));
  }
  function exprSumNames(node) {
    return node[0] === "var" ? [node[1]] : [...exprSumNames(node[2]), ...exprSumNames(node[3])];
  }
  function exprDividesByZero(node) {
    if (node[0] === "op" && node[1] === "/" && node[3][0] === "num" && node[3][1] === 0) return true;
    if (node[0] === "call" && node[1] === "mod" && node[2][1][0] === "num" && node[2][1][1] === 0) return true;
    const children = node[0] === "call" ? node[2] : node.slice(1).filter(Array.isArray);
    return children.some(exprDividesByZero);
  }

  // A model variable, read as oceanval.model_expr.parse reads it: {ok, text, names, kind ("name", "sum" or
  // "formula"), description ("Sum of a, b" or "Expression based on a, b", "" for one name)}, or, if CDO's expr
  // could not work it out, {ok: false, error: {message, start, end}, names}: the names a half-typed formula
  // has so far. An empty box is {ok: false, empty: true}.
  function expression(value) {
    const stripped = String(value ?? "").trim();
    if (!stripped) return { ok: false, empty: true, names: [], kind: "name", text: "", description: "" };
    if (exprIsOneName(stripped)) return { ok: true, text: stripped, names: [stripped], kind: "name", description: "" };
    // what is typed, for the names it has so far
    const typed = () => {
      const found = [];
      for (const match of stripped.matchAll(/[A-Za-z_][A-Za-z0-9_.]*/g)) {
        const next = stripped.slice(match.index + match[0].length).trimStart();
        const name = match[0];
        if (next.startsWith("(") || EXPR_TABLES.constants.includes(name)) continue;
        if (match.index > 0 && /[0-9.]/.test(stripped[match.index - 1])) continue;
        if (!found.includes(name)) found.push(name);
      }
      return found;
    };
    let parsed;
    try {
      parsed = exprParse(stripped);
    } catch (error) {
      if (!(error instanceof ExpressionError)) throw error;
      return { ok: false, error: { message: error.message, start: error.start, end: error.end }, names: typed(), kind: "formula", text: stripped };
    }
    const fail = (message) => ({ ok: false, error: { message, start: 0, end: stripped.length }, names: parsed.names, kind: "formula", text: stripped });
    if (!parsed.names.length) return fail("The formula has no variable of the simulation in it.");
    if (exprDividesByZero(parsed.tree)) return fail("The formula divides by 0, which would make every value missing.");
    const names = parsed.names;
    if (parsed.tree[0] === "var") return { ok: true, text: names[0], names, kind: "name", description: "" };
    if (exprIsSum(parsed.tree) && exprSumNames(parsed.tree).length === names.length) {
      return { ok: true, text: exprSumNames(parsed.tree).join("+"), names, kind: "sum", description: `Sum of ${names.join(", ")}` };
    }
    return {
      ok: true, text: parsed.tokens.map((token) => token.text).join(""), names, kind: "formula",
      description: `Expression based on ${names.join(", ")}`,
    };
  }

  // The name under (or just before) the caret in a model variable box, the part a variable box searches with:
  // {start, end, text}, text "" where there is none (between operators)
  function nameAt(text, caret) {
    const at = caret === null || caret === undefined ? text.length : caret;
    let start = at;
    while (start > 0 && /[A-Za-z0-9_.]/.test(text[start - 1])) start -= 1;
    let end = at;
    while (end < text.length && /[A-Za-z0-9_.]/.test(text[end])) end += 1;
    return { start, end, text: text.slice(start, end) };
  }

  // how long typing pauses before a mistake in a formula is shown under its box (ms)
  const EXPR_PAUSE = 700;
  // the notes under the formula boxes, looked at again now and then, as a page can set a box's value without an event
  const exprNotes = [];
  setInterval(() => {
    if (document.hidden) return;
    for (let i = exprNotes.length - 1; i >= 0; i -= 1) {
      const box = exprNotes[i];
      if (box.input.isConnected) {
        box.seen = true;
        box.refresh();
      } else if (box.seen) {
        // a box taken out of the page (a row removed) is forgotten
        exprNotes.splice(i, 1);
      }
    }
  }, 400);

  // ---- a box for variables' names, with the model's variables in a pop-out under it ----
  // available are the variables' names and longNames their long_names, by
  // name. Anything can be typed in input. Once it has the focus, a pop-out
  // under it lists the variables, a +/- button, the name and the long_name, with
  // those the box holds chosen and listed at the top (Selected). The box is
  // the pop-out's search: the rows shown are those matching the part being
  // typed (searchMatcher, so P*_Chl finds P1_Chl and P2_Chl): with multi, the
  // part between the +s the caret is in, so "P1_Chl+" chooses P1_Chl and shows
  // every row again; without, the whole box. A click on a row adds it
  // to the box (multi: summed with +, replacing a half-typed search;
  // single: it is the box's value), a click on a chosen row (its - button) or its x in
  // Selected takes it out; every row is shown again after a click. extra
  // (such as z_level) are rows at the top. Every change is an input event,
  // as if it had been typed. Returns update(), for variables that are known
  // later, and close(). The table is built when it is first shown, as a model
  // can have hundreds of variables. Shared by the create_recipes window and
  // the oceanval window's Analyze, Compare simulations and own data steps.
  let openVariables = null;

  function variableBox(input, { available = [], longNames = {}, multi = false, extra = [], what = "simulation output", keepSearch = false, explain = false } = {}) {
    let names = available.slice();
    let longs = { ...longNames };
    let rows = [];
    let built = false;
    let active = -1;
    // keepSearch (Analyze, Compare simulations and the own gridded data's model variable): a click adds the name and the search typed before it stays, so
    // more of its rows can be added. kept is that search while the box holds only names; typing clears it.
    // Without keepSearch a click replaces the half-typed search and every row is shown again.
    let kept = null;
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
    // with keepSearch the chosen names are listed under the table, so the rows stay where they are as more are added
    const popover = el("div", { class: keepSearch ? "vars__popover is-picked-below" : "vars__popover", hidden: "" },
      el("div", { class: "vars__bar" }, count, ticked), ...(keepSearch ? [scroll, picked] : [picked, scroll]));

    // with multi, a line under the box saying what it is made of: "Sum of a, b" or "Expression based on a, b"
    // (the names not in the output marked), or, for a formula CDO could not work out, it with the part that is
    // wrong marked (explain: and why; elsewhere the page says why). A mistake is shown once typing pauses.
    let note = null;
    let noteTimer = null;
    let noteShown = "";
    function showNote(now) {
      if (!note) return;
      // a box made before it is in the page gets its note once it is
      if (!note.parentNode && input.parentNode) input.after(note);
      const found = read();
      clearTimeout(noteTimer);
      if (!found.ok && !found.empty && !now && document.activeElement === input) {
        // a formula half typed: told once typing pauses
        noteTimer = setTimeout(() => showNote(true), EXPR_PAUSE);
        return;
      }
      const key = JSON.stringify([input.value, found.ok, found.ok ? found.kind : found.error && found.error.start, names.length]);
      if (key === noteShown) return;
      noteShown = key;
      note.className = "vars__expr";
      if (found.empty || (found.ok && found.kind === "name")) {
        note.hidden = true;
        note.replaceChildren();
        return;
      }
      note.hidden = false;
      if (!found.ok) {
        const text = found.text;
        const { start, end, message } = found.error;
        note.classList.add("is-error");
        note.replaceChildren(
          ...(explain ? [el("span", { class: "vars__expr-message", text: message }), " "] : []),
          el("code", { class: "vars__expr-formula" },
            text.slice(0, start),
            el("mark", { text: end > start ? text.slice(start, end) : "\u2038" }),
            text.slice(Math.max(end, start))));
        return;
      }
      note.classList.add(found.kind === "formula" ? "is-formula" : "is-sum");
      const listed = [];
      found.names.forEach((name, index) => {
        if (index) listed.push(", ");
        const known = !names.length || isName(name);
        listed.push(el("span", { class: known ? "vars__expr-name" : "vars__expr-name is-unknown", text: name,
          title: known ? "" : `not in the ${what}` }));
      });
      note.replaceChildren(el("span", { text: found.kind === "formula" ? "Expression based on " : "Sum of " }), ...listed);
    }
    if (multi) {
      note = el("div", { class: "vars__expr", hidden: "", "aria-live": "polite" });
      if (input.parentNode) input.after(note);
      input.addEventListener("input", () => showNote(false));
      input.addEventListener("change", () => showNote(true));
      input.addEventListener("blur", () => showNote(true));
      // a value a page sets without an input event is told too
      exprNotes.push({ input, refresh: () => { if (document.activeElement !== input) showNote(true); }, seen: false });
      // once the rest of the box is set up
      queueMicrotask(() => showNote(true));
    }
    // a click in the pop-out (a row, an x, its scroll bar) leaves the focus in the box
    popover.addEventListener("mousedown", (event) => event.preventDefault());

    const everyName = () => [...extra.filter((name) => !names.includes(name)), ...names];
    const isName = (name) => names.includes(name) || extra.includes(name);
    // with multi, the box is a model variable: one name, a sum (a+b) or a formula (a+2*b), as expression reads it
    const read = () => expression(input.value);
    // a box holding names joined by + only (a half-typed sum too), whose names can be taken out
    const isSumLike = () => /^[^\s+\-*/^()<>=!?:&|,;]*(?:\s*\+\s*[^\s+\-*/^()<>=!?:&|,;]*)*$/.test(input.value.trim());
    // the names in the box: with multi, those of the formula (any, the model's or not); without, the box if it is a variable's
    const inBox = () => multi ? read().names : [input.value.trim()].filter(isName);
    // where the part searched starts and ends in the box, and what it is: with multi, the name the caret
    // is in (so * and ? are a formula's, never a search's wildcards); without, the whole box
    const part = () => {
      const text = input.value;
      if (!multi) return { start: 0, end: text.length, text: text.trim() };
      if (isSumLike()) {
        // a sum: the part between the +s, as it always was
        const caret = input.selectionStart === null ? text.length : input.selectionStart;
        const start = caret > 0 ? text.lastIndexOf("+", caret - 1) + 1 : 0;
        const next = text.indexOf("+", caret);
        return { start, end: next < 0 ? text.length : next, text: text.slice(start, next < 0 ? text.length : next).trim() };
      }
      return nameAt(text, input.selectionStart);
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
        // a name is taken out of a sum; a formula is the user's to edit
        if (multi && !isSumLike()) return;
        setBox(multi ? used.filter((other) => other !== name).join("+") : "");
        if (!keepSearch) kept = "";
      } else if (!multi) {
        setBox(name);
        close();
        return;
      } else {
        const { start, end, text } = part();
        const typed = text && !isName(text) ? text : "";
        const value = input.value;
        const caret = input.selectionStart === null ? value.length : input.selectionStart;
        // a formula waiting for a value where the caret is (after an operator, a bracket or a comma)
        const waiting = !isSumLike() && /[-+*/^(,<>=!?:&|]\s*$/.test(value.slice(0, caret));
        // a half-typed search leaves the box, and the name takes its place; otherwise the name is added at the end
        if (typed && isSumLike()) {
          setBox((value.slice(0, start) + name + value.slice(end)).split("+").map((one) => one.trim()).filter(Boolean).join("+"));
        } else if (typed || waiting) {
          const at = typed ? start : caret;
          const rest = value.slice(typed ? end : caret);
          input.value = value.slice(0, at) + name + rest;
          input.setSelectionRange(at + name.length, at + name.length);
          input.dispatchEvent(new Event("input", { bubbles: true }));
        } else if (isSumLike()) {
          setBox([...used, name].join("+"));
        } else {
          setBox(`${value.trim()}+${name}`);
        }
        // with keepSearch the search stays, so the rows it finds can be added too (a typed name searches as well); else every row is shown
        kept = keepSearch ? (kept ?? text) : "";
      }
      refresh();
    }

    function build() {
      rows = everyName().map((name) => {
        const longName = longs[name] || "";
        // the + / - button has no handler of its own: a click anywhere on the row, the button too, adds the
        // variable (+) or takes it out (-); refresh() sets the button's sign
        const box = el("button", { type: "button", class: "vars__pm", "data-name": name, tabindex: "-1", text: "+", "aria-label": `Add ${name}` });
        const tr = el("tr", {}, el("td", {}, box), el("td", { text: name }), el("td", { text: longName }));
        tr.addEventListener("click", () => choose(name));
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
      const used = inBox().filter((name) => isName(name) || name !== typing);
      const usedSet = new Set(used);
      // the search is what is typed, or the one kept after a click ("" shows every row)
      const searched = kept !== null ? kept : typing;
      // a single box holding a variable's name shows every row, with it chosen
      const wanted = searchMatcher(!multi && isName(searched) ? "" : searched);
      let shown = 0;
      // the names of a formula are the user's to take out, by editing it
      const fixed = multi && !isSumLike();
      for (const row of rows) {
        const on = usedSet.has(row.name);
        row.tr.classList.toggle("is-picked", on);
        row.box.textContent = on ? "-" : "+";
        row.box.setAttribute("aria-label", on ? `Take out ${row.name}` : `Add ${row.name}`);
        row.box.setAttribute("aria-pressed", String(on));
        row.box.disabled = on && fixed;
        row.box.title = on && fixed ? `Edit the formula to take ${row.name} out` : "";
        row.tr.hidden = !wanted.test(row.fields);
        if (!row.tr.hidden) shown += 1;
      }
      none.hidden = shown > 0;
      count.textContent = wanted.active ? `${shown} of ${rows.length}` : `${rows.length}`;
      ticked.textContent = !multi ? ""
        : fixed && usedSet.size ? `${usedSet.size} in the formula`
          : usedSet.size > 1 ? `${usedSet.size} chosen, summed` : "";
      picked.hidden = !used.length;
      pickedList.replaceChildren(...used.map((name) => {
        const remove = el("button", { type: "button", class: "vars__picked-remove", title: fixed ? `Edit the formula to take ${name} out` : `Remove ${name}`, "aria-label": `Remove ${name}`, text: "×" });
        remove.disabled = fixed;
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
      kept = null;
      if (openVariables === close) openVariables = null;
      input.setAttribute("aria-expanded", "false");
    }

    input.addEventListener("focus", open);
    input.addEventListener("click", () => { if (isOpen()) refresh(); else open(); });
    input.addEventListener("input", (event) => {
      // typed: the search is what is typed again
      if (event.isTrusted) kept = null;
      if (document.activeElement !== input) return;
      if (isOpen()) refresh(); else open();
    });
    input.addEventListener("blur", close);
    // the caret moved to another part
    input.addEventListener("keyup", (event) => {
      if (isOpen() && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { kept = null; refresh(); }
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
        noteShown = "";
        showNote(true);
        if (isOpen()) { build(); refresh(); }
        if (!everyName().length) close();
      },
      close,
    };
  }

  // ---- a box for an environmental variable's name, with the names that match what is typed under it ----
  // items are {name, found, shown}: found the model variables the name
  // resolves to (searched, with the name), shown the text of the second
  // column. The pop-out is the variableBox's (no ticks, no Selected): it opens
  // on focus and as the box is typed in, and its rows are those matching
  // searchMatcher. A click on a row, or Enter on the active one, puts the name
  // in the box as typed (an input event, so the caller's handler runs first),
  // then calls onPick(name). Used by Analyze and Compare simulations.
  function nameBox(input, { items = [], onPick = () => {} } = {}) {
    let rows = [];
    let built = false;
    let active = -1;
    input.classList.add("has-vars");
    input.setAttribute("aria-haspopup", "listbox");
    input.setAttribute("aria-expanded", "false");

    const count = el("span", { class: "vars__count vars__shown", "aria-live": "polite" });
    const none = el("p", { class: "vars__none", text: "No names match.", hidden: "" });
    const scroll = el("div", { class: "vars__scroll" });
    const popover = el("div", { class: "vars__popover", hidden: "" }, el("div", { class: "vars__bar" }, count), scroll);
    popover.addEventListener("mousedown", (event) => event.preventDefault());

    const isOpen = () => !popover.hidden;

    function build() {
      rows = items.map((item) => {
        const tr = el("tr", {},
          el("td", { text: item.name }),
          el("td", item.found.length ? { text: item.shown } : { class: "name-box__missing", text: item.shown }));
        tr.addEventListener("click", () => choose(item));
        return { item, tr, fields: [item.name, ...item.found] };
      });
      scroll.replaceChildren(
        el("table", {},
          el("thead", {}, el("tr", {}, el("th", { scope: "col", text: "Environmental variable" }), el("th", { scope: "col", text: "In the simulation" }))),
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
      if (tr.offsetTop < scroll.scrollTop + 30) scroll.scrollTop = Math.max(0, tr.offsetTop - 30);
      else if (tr.offsetTop + tr.offsetHeight > scroll.scrollTop + scroll.clientHeight) scroll.scrollTop = tr.offsetTop + tr.offsetHeight - scroll.clientHeight;
    }

    function refresh() {
      if (!isOpen()) return;
      const wanted = searchMatcher(input.value);
      let shown = 0;
      for (const row of rows) {
        row.tr.hidden = !wanted.test(row.fields);
        if (!row.tr.hidden) shown += 1;
      }
      none.hidden = shown > 0;
      count.textContent = wanted.active ? `${shown} of ${rows.length}` : `${rows.length}`;
      active = -1;
      rows.forEach((row) => row.tr.classList.remove("is-active"));
      placeList(popover, input);
    }

    function open() {
      if (input.disabled || !items.length) return;
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
      if (openVariables === close) openVariables = null;
      input.setAttribute("aria-expanded", "false");
    }

    function choose(item) {
      // the input event first: the caller's handler resets what it filled, then onPick fills it in
      input.value = item.name;
      input.setSelectionRange(item.name.length, item.name.length);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      close();
      onPick(item.name);
    }

    input.addEventListener("focus", open);
    input.addEventListener("click", () => { if (isOpen()) refresh(); else open(); });
    input.addEventListener("input", () => {
      if (document.activeElement !== input) return;
      if (isOpen()) refresh(); else open();
    });
    input.addEventListener("blur", close);
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
        choose(rows.filter((row) => !row.tr.hidden)[active].item);
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        close();
      } else if (event.key === "Tab") {
        close();
      }
    });

    return { close };
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
  // A box with yearChoices (years, or depths: see withChoices) has a list of
  // them to pick from, and anything can still be typed in it. One list serves
  // every such box, placed under (or over) the box it was opened from, and
  // fixed, so that a pop-out's body cannot clip it. A box's choiceHints are
  // shown beside the choices they name.
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
    const at = typed === "" ? -1 : input.yearChoices.indexOf(Number(typed));
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
  // Every box, in the markup or made later, has autocomplete="oceanval-none": Chrome
  // ignores "off" (and "chrome-off") on boxes it takes for address or name fields,
  // such as "first" and "last", but fills nothing for a token it does not know. The simulation directories
  // are remembered by OceanVal itself (.oceanvalcache), not by the browser. Chrome's list of earlier entries is
  // keyed on the box's name, so each typed box is also renamed at random on every page load (unremembered()).
  const NOT_TYPED = new Set(["hidden", "checkbox", "radio", "range", "button", "submit", "reset", "file", "image", "color"]);
  const typedIn = (node) => node.tagName === "TEXTAREA" || (node.tagName === "INPUT" && !NOT_TYPED.has(node.type));
  function forget(node) {
    if (!node || node.nodeType !== 1) return;
    const boxes = [...node.querySelectorAll("input, textarea, form")];
    if (node.matches("input, textarea, form")) boxes.push(node);
    for (const box of boxes) {
      if ((box.tagName === "FORM" || typedIn(box)) && box.getAttribute("autocomplete") !== "oceanval-none") {
        box.setAttribute("autocomplete", "oceanval-none");
      }
      if (typedIn(box)) unremembered(box);
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
  // the empty boxes a table-head tag asks for: those in its columns, in the ticked rows
  function emptyTableBoxes(tag, cell) {
    if (tag.hasAttribute("data-required-any") || tag.hasAttribute("data-required-tick")) return [];
    const [from, to] = columnsOf(cell);
    const rows = [...cell.closest("table").querySelectorAll("tbody:not(.analyze-all) > tr")]
      .filter((row) => row.children.length > 1 && row.classList.contains("is-on"));
    return rows.flatMap((row) => [...row.children].slice(from, to).flatMap((td) => [...td.querySelectorAll(BOXES)])
      .filter((box) => (box.type === "text" || box.tagName === "SELECT") && !isFilled(box)));
  }
  // a box that must be given is outlined in red while it is empty, and the Required tags are never shown
  function syncRequired(root = document) {
    // the boxes to outline now; a class is only written where it changes, since every write re-runs this (the observer below)
    const needed = new Set();
    for (const tag of root.querySelectorAll(TAGS)) {
      const cell = tag.closest("thead th");
      const inTable = cell && !tag.hasAttribute("data-required-for");
      let satisfied;
      let owners = [];
      if (inTable) {
        satisfied = tableSatisfied(tag, cell);
      } else {
        owners = ownersOf(tag);
        satisfied = owners.length > 0 && owners.every(ownerFilled);
      }
      tag.classList.toggle("is-satisfied", satisfied);
      const field = tag.closest(".own-field");
      if (field) field.classList.toggle("is-satisfied", satisfied);
      if (!satisfied && !tag.hidden && !tag.closest("[hidden]")) {
        const found = inTable ? emptyTableBoxes(tag, cell) : owners.filter((owner) => !ownerFilled(owner));
        for (const owner of found) needed.add(owner);
      }
    }
    // a box a page marks data-check-needed (e.g. the units step's boxes for units OceanVal cannot check) is
    // outlined the same way until it holds something, with no Required tag
    for (const box of root.querySelectorAll("[data-check-needed]")) {
      if (!box.closest("[hidden]") && !isFilled(box)) needed.add(box);
    }
    for (const old of root.querySelectorAll(".is-needed")) {
      if (!needed.has(old)) old.classList.remove("is-needed");
    }
    for (const box of needed) {
      if (!box.classList.contains("is-needed")) box.classList.add("is-needed");
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

  const wordsOf = (text) => String(text || "").split(/\s+/).filter(Boolean);

  // ---- a list of words: typed into a box and added with Enter, and shown below with an × to remove each ----
  // The words are kept, separated by spaces, in a hidden input (the box the page reads), so nothing else changes.
  // A value a script writes fires no event, so the list is looked at again now and then.
  const wordLists = [];
  function wordList({ store, add, items, empty = "" }) {
    const storeEl = $(store), addEl = $(add), listEl = $(items);
    if (!storeEl || storeEl.dataset.wordList) return null;
    storeEl.dataset.wordList = "1";
    let edited = false;
    let shown = null;
    const current = () => [...new Set(wordsOf(storeEl.value))];
    const write = (words) => {
      storeEl.value = words.join(" ");
      storeEl.dispatchEvent(new Event("input", { bubbles: true }));
      render(true);
    };
    const addWords = () => {
      const typed = wordsOf(addEl.value);
      addEl.value = "";
      if (!typed.length) return;
      edited = true;
      const have = current();
      write([...have, ...typed.filter((word) => !have.includes(word))]);
      addEl.focus();
    };
    function render(force = false) {
      addEl.disabled = storeEl.disabled;
      const words = current();
      const key = `${words.join(" ")}|${edited}|${addEl.disabled}`;
      if (!force && key === shown) return;
      shown = key;
      const rows = words.map((word) => {
        const remove = el("button", { type: "button", class: "word-list__remove", text: "×", "aria-label": `Remove ${word}`, title: `Remove ${word}` });
        remove.disabled = addEl.disabled;
        remove.addEventListener("click", () => {
          edited = true;
          write(current().filter((other) => other !== word));
          addEl.focus();
        });
        return el("li", { class: "word-list__item" },
          el("span", { class: "word-list__word", text: word }), remove);
      });
      if (!words.length && empty) rows.push(el("li", { class: "word-list__none", text: empty }));
      listEl.replaceChildren(...rows);
    }
    addEl.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      addWords();
    });
    wordLists.push(render);
    render(true);
    return { render };
  }
  setInterval(() => { if (!document.hidden) wordLists.forEach((render) => render()); }, 300);


  // ---- the two file filters of a window, built into a host: <div class="word-filters" data-skip="ID" data-only="ID">
  // The store inputs keep their ids (the page reads and writes them); the words, the boxes and the labels are made here.
  function wordFilters(host) {
    const pairs = [
      { store: host.dataset.skip, label: "Skip file names containing", empty: "Nothing is skipped yet", placeholder: "Type term and press ↵" },
      { store: host.dataset.only, label: "Only include file names containing", empty: "", placeholder: "Type term and press ↵" },
    ];
    for (const { store, label, empty, placeholder } of pairs) {
      if (!store || $(store)) continue;
      const block = el("div", { class: "g-row word-list" },
        el("span", { class: "g-label", id: `${store}-label`, text: label }),
        el("input", { type: "hidden", id: store }),
        el("div", { class: "word-list__add" },
          el("input", { class: "s-input is-mono", id: `${store}-add`, placeholder, "aria-labelledby": `${store}-label`,
            spellcheck: "false", autocapitalize: "off", autocomplete: "oceanval-none" })),
        el("ul", { class: "word-list__items", id: `${store}-items`, "aria-labelledby": `${store}-label` }));
      host.append(block);
      wordList({ store, add: `${store}-add`, items: `${store}-items`, empty });
    }
    host.classList.add("word-filters");
    return host;
  }
  // every filter box in the page, built once
  const buildWordFilters = () => document.querySelectorAll(".word-filters[data-skip]").forEach((host) => { if (!host.dataset.built) { host.dataset.built = "1"; wordFilters(host); } });

  return {
    ICONS, COG, PML_LOGO, $, el, icon, shorten, plural, toast, pageId, heartbeat, tellVisibility, mountChrome, setBack, variableBox, nameBox, searchMatcher,
    expression, nameAt,
    withChoices, choicesOpen, closeChoices, syncRequired, wordList, wordFilters, buildWordFilters,
  };
})();
