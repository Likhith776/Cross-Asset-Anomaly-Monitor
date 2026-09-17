/* Header: mobile menu toggle, active-section underline, escape/focus handling. */
(function () {
  "use strict";

  function boot() {
    var header = document.querySelector(".header");
    var toggle = document.querySelector(".header__menu");
    var menu = document.querySelector(".mobile-menu");
    if (!header || !toggle || !menu) return;
    if (document.body.hasAttribute("data-nav-parity")) {
      dashboardNav(header, toggle, menu);
      return;
    }

    function close() {
      menu.classList.remove("is-open");
      header.classList.remove("is-menu-open");
      toggle.setAttribute("aria-expanded", "false");
      var first = menu.querySelector("a");
      if (first) first.removeAttribute("tabindex");
      Array.prototype.forEach.call(menu.querySelectorAll("a"), function (a) {
        a.setAttribute("tabindex", "-1");
      });
    }

    function open() {
      menu.classList.add("is-open");
      header.classList.add("is-menu-open");
      toggle.setAttribute("aria-expanded", "true");
      Array.prototype.forEach.call(menu.querySelectorAll("a"), function (a) {
        a.removeAttribute("tabindex");
      });
      var first = menu.querySelector("a");
      if (first) first.focus();
    }

    toggle.addEventListener("click", function () {
      if (menu.classList.contains("is-open")) close();
      else open();
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && menu.classList.contains("is-open")) {
        close();
        toggle.focus();
      }
    });

    // Trap focus inside the open overlay.
    document.addEventListener("focusin", function (e) {
      if (menu.classList.contains("is-open") && !menu.contains(e.target) && e.target !== toggle) {
        var first = menu.querySelector("a");
        if (first) first.focus();
      }
    });

    close();

    // Active-page underline follows in-page sections.
    var links = Array.prototype.slice.call(document.querySelectorAll('.nav-link[href^="#"]'));
    if (!links.length || !("IntersectionObserver" in window)) return;
    var map = {};
    links.forEach(function (a) { map[a.getAttribute("href").slice(1)] = a; });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        var link = map[entry.target.id];
        if (!link) return;
        if (entry.isIntersecting) {
          links.forEach(function (a) { a.removeAttribute("aria-current"); });
          link.setAttribute("aria-current", "page");
        }
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    Object.keys(map).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) io.observe(el);
    });
  }

  function dashboardNav(header, toggle, menu) {
    var root = document.documentElement;
    var reduced = matchMedia("(prefers-reduced-motion: reduce)");
    var desktop = matchMedia("(min-width: 769px)");
    var brand = header.querySelector(".header__brand");
    var wordmark = header.querySelector(".header__wordmark");
    var nav = header.querySelector(".header__nav");
    var menuLinks = Array.from(menu.querySelectorAll("a"));
    var background = Array.from(document.body.children).filter(function (el) {
      return el !== header && el !== menu && !el.matches("script, .cursor__ball, .cursor__label-wrap");
    });
    var inertState = [];
    var opened = false;
    var progress = 0;
    var destination = 0;
    var frame = 0;
    var lastTime = 0;
    var previousY = window.scrollY;
    var sizes;

    function motionEnabled() {
      return !reduced.matches && root.getAttribute("data-motion") !== "off" &&
        new URLSearchParams(location.search).get("motion") !== "off";
    }
    function geometryChanged() { window.dispatchEvent(new Event("cam:nav-geometry")); }
    function ease(t) { return t < .5 ? 8 * t * t * t * t : 1 - Math.pow(-2 * t + 2, 4) / 2; }
    function measure() {
      var probe = document.createElement("span");
      probe.style.cssText = "position:absolute;visibility:hidden;pointer-events:none;padding:var(--spacing-m);gap:var(--spacing-nav);margin:var(--spacing-s)";
      header.appendChild(probe);
      var style = getComputedStyle(probe);
      sizes = {
        expanded: root.clientWidth - 20,
        small: parseFloat(style.marginLeft),
        medium: parseFloat(style.paddingLeft),
        gap: parseFloat(style.gap),
        brandGap: parseFloat(getComputedStyle(brand).fontSize) * .6,
        word: wordmark.scrollWidth,
        mark: header.querySelector(".brandmark").getBoundingClientRect().width,
        links: Array.from(nav.children).reduce(function (sum, a) { return sum + a.getBoundingClientRect().width; }, 0)
      };
      probe.remove();
      sizes.compact = Math.min(sizes.expanded, sizes.mark + sizes.medium + sizes.links +
        sizes.gap / 2 * (nav.children.length - 1) + sizes.medium * 2);
      render();
    }
    function render() {
      if (!sizes) return;
      var active = desktop.matches && motionEnabled();
      var collapse = active ? ease(Math.max(0, (progress - .4) / .6)) : 0;
      var fade = active ? ease(Math.min(1, progress / .4)) : 0;
      header.style.width = active ? (sizes.expanded + (sizes.compact - sizes.expanded) * collapse) + "px" : "";
      header.style.paddingInline = active ? (sizes.small + (sizes.medium - sizes.small) * collapse) + "px" : "";
      header.style.borderRadius = (500 * collapse) + "px";
      header.style.backgroundColor = "rgba(255,255,255," + (.4 * collapse) + ")";
      header.style.backdropFilter = "blur(" + (12 * collapse) + "px)";
      var color = Math.round(255 - 234 * collapse);
      header.style.setProperty("--header-chrome", opened ? "#151515" : "rgb(" + color + "," + color + "," + color + ")");
      header.style.setProperty("--header-knockout", opened ? "#fff" : "rgb(" + Math.round(255 * collapse) + "," + Math.round(255 * collapse) + "," + Math.round(255 * collapse) + ")");
      header.style.setProperty("--wordmark-opacity", 1 - fade);
      header.style.setProperty("--wordmark-width", active ? sizes.word * (1 - collapse) + "px" : "auto");
      header.style.setProperty("--brand-gap", sizes.brandGap * (1 - collapse) + "px");
      nav.style.gap = sizes.gap * (1 - collapse / 2) + "px";
      header.dataset.navState = opened ? "menu" : progress === 1 && active ? "compact" : progress === 0 || !active ? "expanded" : "transitioning";
      geometryChanged();
    }
    function tick(time) {
      var delta = lastTime ? Math.min(time - lastTime, 64) / 1400 : 0;
      lastTime = time;
      progress = destination > progress ? Math.min(destination, progress + delta) : Math.max(destination, progress - delta);
      render();
      if (progress !== destination) frame = requestAnimationFrame(tick);
      else { frame = 0; lastTime = 0; }
    }
    function animate(value, immediate) {
      destination = value;
      if (immediate || !motionEnabled()) {
        cancelAnimationFrame(frame); frame = 0; lastTime = 0; progress = value; render();
      } else if (!frame && progress !== value) frame = requestAnimationFrame(tick);
    }
    function setMenu(value, restoreFocus) {
      if (value === opened) return;
      opened = value;
      if (value) {
        inertState = background.map(function (el) { return el.inert; });
        background.forEach(function (el) { el.inert = true; });
      } else background.forEach(function (el, i) { el.inert = inertState[i]; });
      menu.inert = !value;
      menu.setAttribute("aria-hidden", String(!value));
      menu.classList.toggle("is-open", value);
      header.classList.toggle("is-menu-open", value);
      document.body.classList.toggle("nav-menu-open", value);
      root.classList.toggle("nav-scroll-locked", value);
      toggle.setAttribute("aria-expanded", String(value));
      toggle.setAttribute("aria-label", value ? "Close menu" : "Open menu");
      window.dispatchEvent(new CustomEvent("cam:nav-menu", { detail: { open: value } }));
      render();
      if (value) menuLinks[0].focus({ preventScroll: true });
      else if (restoreFocus) toggle.focus({ preventScroll: true });
    }
    menu.inert = true;
    menu.setAttribute("aria-hidden", "true");
    menuLinks.forEach(function (a, i) {
      a.style.setProperty("--menu-index", i);
      a.addEventListener("click", function () {
        setMenu(false, false);
        var hash = a.getAttribute("href");
        var target = hash.charAt(0) === "#" && document.getElementById(hash.slice(1));
        if (target) {
          if (!target.hasAttribute("tabindex")) {
            target.setAttribute("tabindex", "-1");
            target.addEventListener("blur", function () { target.removeAttribute("tabindex"); }, { once: true });
          }
          target.focus({ preventScroll: true });
        }
      });
    });
    toggle.addEventListener("click", function () { setMenu(!opened, true); });
    document.addEventListener("keydown", function (e) {
      if (!opened) return;
      if (e.key === "Escape") { e.preventDefault(); setMenu(false, true); }
      if (e.key === "Tab") {
        var cycle = [toggle].concat(menuLinks);
        var index = cycle.indexOf(document.activeElement);
        e.preventDefault();
        cycle[(index + (e.shiftKey ? -1 : 1) + cycle.length) % cycle.length].focus();
      }
    });
    var sectionLinks = Array.from(document.querySelectorAll('.header__nav a[href^="#"], .mobile-menu a[href^="#"]'));
    var sections = Array.from(new Set(sectionLinks.map(function (a) { return document.getElementById(a.hash.slice(1)); }).filter(Boolean)));
    function updateSection() {
      var current = null;
      sections.forEach(function (section) {
        if (section.getBoundingClientRect().top <= header.getBoundingClientRect().bottom + 100) current = section.id;
      });
      sectionLinks.forEach(function (a) {
        if (a.hash === "#" + current) a.setAttribute("aria-current", "location");
        else a.removeAttribute("aria-current");
      });
    }
    window.addEventListener("scroll", function () {
      var y = window.scrollY;
      var scrollable = root.scrollHeight - innerHeight > Math.max(48, innerHeight * .05);
      if (desktop.matches && motionEnabled() && scrollable) {
        /* The capsule is one-way until top-of-page: it collapses on the first
           scroll past the threshold and only restores at the top, so scrolling
           back up mid-page never re-expands the header. */
        if (y <= 10) animate(0);
        else animate(1);
      }
      previousY = y;
      updateSection();
    }, { passive: true });
    function reset() {
      if (desktop.matches && opened) setMenu(false, false);
      measure();
      animate(desktop.matches && motionEnabled() && window.scrollY > 10 ? 1 : 0, true);
      previousY = window.scrollY;
      updateSection();
    }
    window.addEventListener("resize", reset, { passive: true });
    window.addEventListener("pageshow", reset);
    desktop.addEventListener && desktop.addEventListener("change", reset);
    window.addEventListener("cam:entrance-complete", reset);
    reduced.addEventListener("change", reset);
    if (document.fonts) document.fonts.ready.then(reset);
    reset();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
