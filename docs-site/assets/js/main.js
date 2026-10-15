(function () {
  "use strict";

  /* ---------- header scroll + mobile nav ---------- */
  var header = document.querySelector(".site-header");
  var navToggle = document.querySelector(".nav-toggle");
  var navLinks = document.querySelector(".nav-links");

  function onScroll() {
    if (!header) return;
    header.classList.toggle("is-scrolled", window.scrollY > 8);
  }
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });

  if (navToggle && navLinks) {
    navToggle.addEventListener("click", function () {
      var open = navLinks.classList.toggle("is-open");
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    navLinks.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () {
        navLinks.classList.remove("is-open");
        navToggle.setAttribute("aria-expanded", "false");
      });
    });
  }

  /* ---------- nav "How to use" dropdown ---------- */
  var navDrops = document.querySelectorAll(".nav-drop");

  function closeNavDrops(except) {
    navDrops.forEach(function (drop) {
      if (drop === except) return;
      drop.classList.remove("is-open");
      var toggle = drop.querySelector(".nav-drop-toggle");
      if (toggle) toggle.setAttribute("aria-expanded", "false");
    });
  }

  navDrops.forEach(function (drop) {
    var toggle = drop.querySelector(".nav-drop-toggle");
    if (!toggle) return;
    toggle.addEventListener("click", function (event) {
      event.stopPropagation();
      var open = !drop.classList.contains("is-open");
      closeNavDrops(open ? drop : null);
      drop.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });
  document.addEventListener("click", function () { closeNavDrops(); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeNavDrops();
  });

  /* ---------- mark active nav link ---------- */
  var here = (location.pathname.split("/").pop() || "index.html");
  document.querySelectorAll(".nav-links a[data-page]").forEach(function (a) {
    if (a.getAttribute("data-page") === here) {
      a.classList.add("active");
      var parentDrop = a.closest(".nav-drop");
      if (parentDrop) {
        var parentToggle = parentDrop.querySelector(".nav-drop-toggle");
        if (parentToggle) parentToggle.classList.add("active");
      }
    }
  });

  /* ---------- copy-to-clipboard for code cards ---------- */
  document.querySelectorAll("[data-copy-target]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var target = document.querySelector(btn.getAttribute("data-copy-target"));
      if (!target) return;
      var text = target.innerText;
      navigator.clipboard.writeText(text).then(function () {
        var label = btn.querySelector(".copy-label");
        var original = label ? label.textContent : null;
        if (label) label.textContent = "Copied!";
        setTimeout(function () {
          if (label && original !== null) label.textContent = original;
        }, 1600);
      });
    });
  });

  /* ---------- FAQ accordion ---------- */
  document.querySelectorAll(".faq-item").forEach(function (item) {
    var q = item.querySelector(".faq-question");
    if (!q) return;
    q.addEventListener("click", function () {
      var isOpen = item.classList.contains("is-open");
      item.classList.toggle("is-open", !isOpen);
      var answer = item.querySelector(".faq-answer");
      if (answer) {
        answer.style.maxHeight = !isOpen ? answer.scrollHeight + 40 + "px" : "";
      }
    });
  });

  /* ---------- recipes table: search + filter + expand ---------- */
  var recipeSearch = document.querySelector("#recipe-search");
  var recipeRows = document.querySelectorAll("tr.recipe-row");
  var filterPills = document.querySelectorAll(".filter-pill");
  var activeRegion = "all";

  function applyRecipeFilters() {
    var term = (recipeSearch && recipeSearch.value || "").trim().toLowerCase();
    recipeRows.forEach(function (row) {
      var text = row.textContent.toLowerCase();
      var region = row.getAttribute("data-region");
      var matchesTerm = !term || text.indexOf(term) !== -1;
      var matchesRegion = activeRegion === "all" || region === activeRegion;
      var show = matchesTerm && matchesRegion;
      row.classList.toggle("hidden-row", !show);
      var detail = row.nextElementSibling;
      if (detail && detail.classList.contains("recipe-detail")) {
        detail.classList.toggle("hidden-row", !show && true);
        if (!show) detail.classList.remove("is-open");
      }
    });
  }

  if (recipeSearch) recipeSearch.addEventListener("input", applyRecipeFilters);
  filterPills.forEach(function (pill) {
    pill.addEventListener("click", function () {
      filterPills.forEach(function (p) { p.classList.remove("active"); });
      pill.classList.add("active");
      activeRegion = pill.getAttribute("data-region");
      applyRecipeFilters();
    });
  });

  recipeRows.forEach(function (row) {
    row.addEventListener("click", function () {
      var detail = row.nextElementSibling;
      if (!detail || !detail.classList.contains("recipe-detail")) return;
      var willOpen = !detail.classList.contains("is-open");
      document.querySelectorAll(".recipe-detail.is-open").forEach(function (d) {
        d.classList.remove("is-open");
      });
      if (willOpen) detail.classList.add("is-open");
    });
  });

  /* ---------- docs scrollspy ---------- */
  var tocLinks = document.querySelectorAll(".docs-toc a");
  if (tocLinks.length) {
    var targets = [];
    tocLinks.forEach(function (link) {
      var id = link.getAttribute("href").replace("#", "");
      var el = document.getElementById(id);
      if (el) targets.push({ link: link, el: el });
    });

    function updateToc() {
      var pos = window.scrollY + 120;
      var current = null;
      targets.forEach(function (t) {
        if (t.el.offsetTop <= pos) current = t;
      });
      tocLinks.forEach(function (l) { l.classList.remove("active"); });
      if (current) current.link.classList.add("active");
    }
    updateToc();
    window.addEventListener("scroll", updateToc, { passive: true });
  }

  /* ---------- docs version (this repo's setup.py, not the last PyPI
     release - they can differ once a feature is merged but not yet
     published) ---------- */
  var versionEls = document.querySelectorAll("#oceanval-docs-version");
  if (versionEls.length) {
    fetch("https://raw.githubusercontent.com/pmlmodelling/oceanVal/main/setup.py")
      .then(function (res) { return res.text(); })
      .then(function (text) {
        var match = text.match(/version\s*=\s*['"]([^'"]+)['"]/);
        var version = match && match[1];
        if (!version) return;
        versionEls.forEach(function (el) {
          el.textContent = "v" + version;
          el.title = "OceanVal v" + version + " (main branch)";
        });
      })
      .catch(function () {
        /* GitHub unreachable (offline, blocked, rate-limited): leave the
           static fallback text in the HTML in place rather than showing
           an error or a stale version number. */
      });
  }

  /* ---------- site search (Pagefind) ----------
     The index is built in CI (see .github/workflows/pages.yml) and is not
     committed, so the bar only appears once pagefind/ is actually being
     served. Archived snapshots under /archive/ have no index of their own.
     pages.yml links this script as main.js?v=<hash>, hence src*= not src$=. */
  var navActions = document.querySelector(".nav-actions");
  var mainScript = document.querySelector('script[src*="assets/js/main.js"]');
  if (navActions && mainScript && location.pathname.indexOf("/archive/") === -1) {
    var pagefindBase = new URL("../../pagefind/", mainScript.src).href;
    var searchDialog = null;
    var searchReady = false;

    var searchBar = document.createElement("button");
    searchBar.type = "button";
    searchBar.className = "search-bar";
    searchBar.hidden = true;
    searchBar.setAttribute("aria-label", "Search the docs");
    searchBar.title = "Search (press /)";
    searchBar.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>' +
      '<span class="search-bar__label">Search docs&hellip;</span>' +
      '<kbd class="search-bar__key" aria-hidden="true">/</kbd>';
    navActions.insertBefore(searchBar, navActions.firstChild);

    var loadPagefindUI = function () {
      return new Promise(function (resolve, reject) {
        var css = document.createElement("link");
        css.rel = "stylesheet";
        css.href = pagefindBase + "pagefind-ui.css";
        document.head.appendChild(css);
        var js = document.createElement("script");
        js.src = pagefindBase + "pagefind-ui.js";
        js.onload = resolve;
        js.onerror = reject;
        document.head.appendChild(js);
      });
    };

    var buildDialog = function () {
      if (!searchDialog) {
        searchDialog = document.createElement("dialog");
        searchDialog.className = "search-dialog";
        searchDialog.setAttribute("aria-label", "Search the docs");
        searchDialog.innerHTML = '<div id="search-ui"></div>';
        document.body.appendChild(searchDialog);
        // a click on the backdrop, outside the box, closes it
        searchDialog.addEventListener("click", function (e) {
          if (e.target === searchDialog) searchDialog.close();
        });
        new PagefindUI({
          element: "#search-ui",
          showImages: false,
          showSubResults: true,
          resetStyles: false,
          pageSize: 8
        });
      }
    };

    var openSearch = function () {
      var ready = window.PagefindUI ? Promise.resolve() : loadPagefindUI();
      ready.then(function () {
        buildDialog();
        if (!searchDialog.open) searchDialog.showModal();
        var input = searchDialog.querySelector("input");
        if (input) input.focus();
      });
    };

    searchBar.addEventListener("click", function () {
      if (searchReady) openSearch();
    });

    document.addEventListener("keydown", function (e) {
      var tag = (e.target.tagName || "").toLowerCase();
      var typing = tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable;
      var wantsSearch = (e.key === "/" && !typing) ||
        ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k");
      if (wantsSearch && searchReady) {
        e.preventDefault();
        openSearch();
      }
    });

    fetch(pagefindBase + "pagefind-entry.json", { method: "HEAD" })
      .then(function (res) {
        if (!res.ok) throw new Error("no search index");
        searchReady = true;
        searchBar.hidden = false;
      })
      .catch(function () { /* no index (e.g. a local preview): leave search off */ });
  }

  /* ---------- landing page animation: autoplay, with a pause button ---------- */
  var heroVideo = document.querySelector("#hero-video");
  var heroToggle = document.querySelector("#hero-video-toggle");
  if (heroVideo && heroToggle) {
    var syncToggle = function () {
      var paused = heroVideo.paused;
      heroToggle.classList.toggle("is-paused", paused);
      heroToggle.setAttribute("aria-label", paused ? "Play the animation" : "Pause the animation");
    };
    heroVideo.addEventListener("play", syncToggle);
    heroVideo.addEventListener("pause", syncToggle);
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      /* the poster stays up until the visitor asks for the motion */
      heroVideo.removeAttribute("autoplay");
      heroVideo.pause();
      var still = function () { heroVideo.currentTime = 7.9; }; /* the datasets step, as the poster */
      if (heroVideo.readyState >= 1) still(); else heroVideo.addEventListener("loadedmetadata", still, { once: true });
    } else {
      var started = heroVideo.play();
      if (started && started.catch) started.catch(function () { /* autoplay blocked: poster + Play button */ });
    }
    syncToggle();
    heroToggle.addEventListener("click", function () {
      if (heroVideo.paused) heroVideo.play(); else heroVideo.pause();
    });
  }

  /* ---------- back to top ---------- */
  var backToTop = document.querySelector(".back-to-top");
  if (backToTop) {
    window.addEventListener("scroll", function () {
      backToTop.classList.toggle("is-visible", window.scrollY > 600);
    }, { passive: true });
    backToTop.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }
})();
