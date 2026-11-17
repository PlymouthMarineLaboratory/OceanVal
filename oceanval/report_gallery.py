"""The validation report's two views of a page: Report view, the page as it is,
and Gallery view, a tile for each figure and table that opens it, with its
caption, in a pop-out.

Two buttons at the top of the sidebar, the report's top left, switch between
them; the choice is kept in the browser (localStorage), so it holds from page
to page, and Report view is the default.

The gallery is made by the page's own script from what is on the page, so
each figure is on the page once and both views show the same one (a page opened
from disk cannot fetch another file, so the figures could not be fragments of
their own). A figure or table is what comes before its caption, a paragraph
that starts "Figure N" or "Table N" in bold: every image, or table, since the
last caption or section heading. A tile is titled by its caption's short
gallery title, an empty <span class="ov-gallery-title" data-title="..."> that
md(..., gallery=...) (oceanval.tidiers) puts after the caption, or else by the
caption's first sentence. A section with two or more figures and tables is a
group: its heading, the section's question, comes once before its tiles, and
the group is coloured (a colour of GROUP_COLOURS each, in turn). A section's
only figure is a plain tile. The PDF and Word exports are made from the page
without this, so they are unchanged (the empty marker shows nothing there).

_write_offline_report_pages puts style() and script() in the pages of
validate()'s report and its interim report (gallery=True), not in those of
analyze() and compare_simulations().
"""

# the colours of the report's sidebar (see oceanval._offline_report_style)
TEAL = "#0f7c7c"
TEAL_DARK = "#0a5f5f"
INK = "#0e3a45"
# a group's colour and its tint, in turn
GROUP_COLOURS = [
    ("#0f7c7c", "#e8f4f4"),
    ("#2f66ad", "#ebf1f9"),
    ("#7a4fa6", "#f3eef9"),
    ("#a8620a", "#fbf2e6"),
    ("#ad3f5c", "#faedf0"),
    ("#3c7a38", "#edf5ec"),
]


def style():
    return (
        "<style>"
        ".ov-view-switch{display:flex;flex:0 0 auto;gap:0;margin:0 0 6px;border:1px solid rgba(255,255,255,0.7);"
        "border-radius:8px;overflow:hidden}"
        ".ov-view-switch button{flex:1;padding:9px 6px;border:0;background:transparent;color:#fff;cursor:pointer;"
        "font-family:Arial,sans-serif;font-size:13px;font-weight:700}"
        ".ov-view-switch button[aria-pressed=true]{background:#fff;color:" + TEAL + "}"
        ".ov-view-switch button:not([aria-pressed=true]):hover{background:rgba(255,255,255,0.16)}"
        ".ov-view-switch button:focus-visible{outline:2px solid #fff;outline-offset:-4px}"
        # rows that wrap, with the tiles growing to fill each row, so no row leaves a gap
        "#ov-gallery{display:flex;flex-wrap:wrap;gap:18px;margin:8px 0 40px}"
        "#ov-gallery[hidden]{display:none}"
        "body.ov-gallery-mode main>.jp-Cell:not(.ov-title-cell){display:none}"
        ".ov-tile{flex:1 1 250px;min-width:0;display:flex;flex-direction:column;gap:8px;padding:12px;border:1px solid #d5e3e6;border-radius:10px;"
        "background:#fff;color:" + INK + ";cursor:pointer;text-align:left;font-family:Arial,sans-serif;"
        "box-shadow:0 1px 3px rgba(14,58,69,0.08);transition:transform 0.15s,box-shadow 0.15s}"
        ".ov-tile:hover{transform:translateY(-2px);box-shadow:0 6px 16px rgba(14,58,69,0.14)}"
        ".ov-tile:focus-visible{outline:3px solid " + TEAL + ";outline-offset:2px}"
        ".ov-tile__thumb{display:grid;place-items:center;height:170px;border-radius:6px;background:#f3f8f9;overflow:hidden}"
        ".ov-tile__thumb img{max-width:100%;max-height:170px;object-fit:contain;margin:0!important}"
        ".ov-tile__thumb svg{width:56px;height:56px;color:" + TEAL + "}"
        ".ov-tile__label{font-size:12px;font-weight:700;letter-spacing:0.04em;text-transform:uppercase;color:" + TEAL + "}"
        ".ov-tile__title{font-size:14.5px;line-height:1.35;font-weight:600}"
        # a group of a section's figures: its question, then its tiles, in its colour
        ".ov-group{flex:1 1 100%;padding:14px 16px 18px;border-left:5px solid var(--ov-group);"
        "border-radius:10px;background:var(--ov-group-tint)}"
        ".ov-group__title{margin:0 0 12px;font-family:Arial,sans-serif;font-size:16.5px;line-height:1.35;"
        "font-weight:700;color:" + INK + "}"
        ".ov-group__tiles{display:flex;flex-wrap:wrap;gap:18px}"
        ".ov-group .ov-tile{border-color:var(--ov-group-tint);border-top:4px solid var(--ov-group)}"
        ".ov-group .ov-tile__thumb{background:var(--ov-group-tint)}"
        ".ov-group .ov-tile__label{color:var(--ov-group)}"
        + "".join(
            f".ov-group--{i}{{--ov-group:{colour};--ov-group-tint:{tint}}}"
            for i, (colour, tint) in enumerate(GROUP_COLOURS)
        )
        + ".ov-dialog__section{margin:4px 0 0;font-family:Arial,sans-serif;font-size:14px;line-height:1.4;"
        "color:#4a6670}"
        ".ov-dialog{position:fixed;inset:0;z-index:50;display:grid;place-items:center;padding:24px;"
        "background:rgba(14,58,69,0.55)}"
        ".ov-dialog[hidden]{display:none}"
        # the card is no taller than the window (less the overlay's padding) and its body scrolls, both ways
        "body.ov-dialog-open{overflow:hidden}"
        ".ov-dialog__card{display:flex;flex-direction:column;width:min(1100px,100%);max-height:calc(100vh - 48px);"
        "min-height:0;border-radius:12px;"
        "background:#fff;box-shadow:0 20px 50px rgba(0,0,0,0.3)}"
        ".ov-dialog__head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;"
        "padding:18px 22px 8px}"
        ".ov-dialog__head h2{margin:0;font-family:Arial,sans-serif;font-size:18px;color:" + INK + "}"
        ".ov-dialog__close{flex:none;width:36px;height:36px;border:0;border-radius:8px;background:transparent;"
        "color:" + INK + ";font-size:26px;line-height:1;cursor:pointer}"
        ".ov-dialog__close:hover{background:#eef5f6}"
        ".ov-dialog__body{flex:1 1 auto;min-height:0;overflow:auto;overscroll-behavior:contain;padding:4px 22px 22px}"
        ".ov-dialog__body:focus-visible{outline:3px solid " + TEAL + ";outline-offset:-3px}"
        ".ov-dialog__body img{display:block;max-width:100%;height:auto;margin:0 auto 10px}"
        ".ov-dialog__body table{margin:0 auto 12px}"
        ".ov-dialog__caption{margin-top:8px;font-size:15px;line-height:1.5;color:" + INK + "}"
        "@media(prefers-reduced-motion:reduce){.ov-tile{transition:none}.ov-tile:hover{transform:none}}"
        "</style>"
    )


# the script, as one function run once the page is read
_SCRIPT = r"""
(function () {
  var KEY = "oceanval-report-view";
  function saved() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function save(view) { try { localStorage.setItem(KEY, view); } catch (e) {} }
  var CAPTION = /^(Figure|Table)\s+(\d+)/;
  var TABLE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
    + 'stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14.5h18M9 9v11M15 9v11"/></svg>';

  // a tile's title: the caption's short gallery title, or else its first sentence
  function shortTitle(caption) {
    var marker = caption.querySelector(".ov-gallery-title");
    var given = marker && marker.getAttribute("data-title");
    if (given) return given.trim();
    var text = caption.textContent.replace(/\s+/g, " ").trim()
      .replace(/^(Figure|Table)\s+\d+\s*:?\s*/, "").replace(/^:\s*/, "");
    var sentence = text.split(/\.\s/)[0].replace(/\.$/, "");
    if (sentence.length <= 80) return sentence;
    var cut = sentence.slice(0, 80);
    return cut.slice(0, cut.lastIndexOf(" ") > 40 ? cut.lastIndexOf(" ") : 80) + "…";
  }

  // the page's figures and tables: what comes before each caption, since the last caption or heading
  function items(main) {
    var found = [], pending = [], heading = "", section = 0;
    var children = main.querySelectorAll(".jp-OutputArea-child");
    for (var i = 0; i < children.length; i++) {
      var child = children[i];
      var h2 = child.querySelector("h2");
      if (h2) { heading = h2.textContent.replace(/¶/g, "").trim(); section++; pending = []; continue; }
      var strong = child.querySelector(".jp-RenderedMarkdown p > strong:first-child");
      var match = strong && CAPTION.exec(strong.textContent.trim());
      if (match) {
        var kind = match[1];
        var shown = pending.filter(function (node) {
          return kind === "Figure" ? node.querySelector("img") : node.querySelector("table");
        });
        if (shown.length) {
          found.push({ kind: kind, label: kind + " " + match[2], title: shortTitle(child), heading: heading,
            section: section, nodes: shown, caption: child });
        }
        pending = [];
        continue;
      }
      if (child.querySelector("img, table")) pending.push(child);
    }
    return found;
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      if (key === "text") node.textContent = attrs[key];
      else if (key === "html") node.innerHTML = attrs[key];
      else node.setAttribute(key, attrs[key]);
    });
    (children || []).forEach(function (child) { node.appendChild(child); });
    return node;
  }

  function start() {
    var main = document.querySelector("main");
    var sidebar = document.querySelector(".oceanval-sidebar");
    if (!main || !sidebar) return;
    var found = items(main);
    var title = main.querySelector("h1");
    var titleCell = title ? title.closest(".jp-Cell") : null;
    if (titleCell) titleCell.classList.add("ov-title-cell");

    // the pop-out of one figure or table, with its caption
    var dialog = el("div", { class: "ov-dialog", hidden: "", role: "dialog", "aria-modal": "true", "aria-labelledby": "ov-dialog-title" });
    var heading = el("h2", { id: "ov-dialog-title" });
    var sectionLine = el("p", { class: "ov-dialog__section", hidden: "" });
    var close = el("button", { type: "button", class: "ov-dialog__close", "aria-label": "Close", text: "×" });
    // the body scrolls (a tall figure, a wide table): by the wheel, and by the keys once it is tabbed to
    var body = el("div", { class: "ov-dialog__body", tabindex: "0" });
    dialog.appendChild(el("div", { class: "ov-dialog__card" }, [el("div", { class: "ov-dialog__head" }, [el("div", {}, [heading, sectionLine]), close]), body]));
    document.body.appendChild(dialog);
    var opener = null;
    function open(item, tile) {
      opener = tile;
      heading.textContent = item.label + (item.title ? ": " + item.title : "");
      // the section's question, as context
      sectionLine.textContent = item.heading;
      sectionLine.hidden = !item.heading;
      body.replaceChildren();
      item.nodes.forEach(function (node) { body.appendChild(node.cloneNode(true)); });
      var caption = item.caption.cloneNode(true);
      caption.classList.add("ov-dialog__caption");
      body.appendChild(caption);
      dialog.hidden = false;
      // the page behind stays still, so the wheel moves the figure
      document.body.classList.add("ov-dialog-open");
      body.scrollTop = 0;
      body.scrollLeft = 0;
      close.focus();
    }
    function shut() {
      if (dialog.hidden) return;
      dialog.hidden = true;
      document.body.classList.remove("ov-dialog-open");
      if (opener) opener.focus();
    }
    close.addEventListener("click", shut);
    dialog.addEventListener("click", function (event) { if (event.target === dialog) shut(); });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape") shut(); });

    // a tile for each: a thumbnail of the figure (a table's icon), its number and its short title
    var gallery = el("section", { id: "ov-gallery", hidden: "", "aria-label": "Gallery of the figures and tables" });
    function tileOf(item) {
      var image = item.kind === "Figure" ? item.nodes[0].querySelector("img") : null;
      var thumb = el("span", { class: "ov-tile__thumb" });
      if (image) thumb.appendChild(el("img", { src: image.getAttribute("src"), alt: "" }));
      else thumb.innerHTML = TABLE_ICON;
      var tile = el("button", { type: "button", class: "ov-tile", "aria-haspopup": "dialog" }, [
        thumb, el("span", { class: "ov-tile__label", text: item.label }), el("span", { class: "ov-tile__title", text: item.title || item.heading || item.label }),
      ]);
      tile.addEventListener("click", function () { open(item, tile); });
      return tile;
    }
    // a section with two or more is a group, its question once before its tiles, in a colour of its own
    var groups = 0;
    for (var start = 0; start < found.length;) {
      var end = start;
      while (end < found.length && found[end].section === found[start].section) end++;
      var run = found.slice(start, end);
      if (run.length > 1 && run[0].heading) {
        var group = el("div", { class: "ov-group ov-group--" + (groups++ % GROUP_COUNT), role: "group", "aria-label": run[0].heading }, [
          el("h3", { class: "ov-group__title", text: run[0].heading }),
          el("div", { class: "ov-group__tiles" }, run.map(tileOf)),
        ]);
        gallery.appendChild(group);
      } else {
        run.forEach(function (item) { gallery.appendChild(tileOf(item)); });
      }
      start = end;
    }
    if (!found.length) gallery.appendChild(el("p", { text: "This page has no figures or tables." }));
    if (titleCell) titleCell.after(gallery);
    else main.prepend(gallery);

    // the two buttons, at the top of the sidebar
    var report = el("button", { type: "button", "aria-pressed": "true", text: "Report view" });
    var tiles = el("button", { type: "button", "aria-pressed": "false", text: "Gallery view" });
    var group = el("div", { class: "ov-view-switch", role: "group", "aria-label": "How the page is shown" }, [report, tiles]);
    sidebar.prepend(group);
    function show(view) {
      var asGallery = view === "gallery";
      document.body.classList.toggle("ov-gallery-mode", asGallery);
      gallery.hidden = !asGallery;
      report.setAttribute("aria-pressed", String(!asGallery));
      tiles.setAttribute("aria-pressed", String(asGallery));
    }
    report.addEventListener("click", function () { save("report"); show("report"); });
    tiles.addEventListener("click", function () { save("gallery"); show("gallery"); });
    show(saved() === "gallery" ? "gallery" : "report");
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
"""


def script():
    return "<script>" + _SCRIPT.replace("GROUP_COUNT", str(len(GROUP_COLOURS))) + "</script>"
