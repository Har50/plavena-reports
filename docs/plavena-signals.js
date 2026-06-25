/* ============================================================================
 * Plavena — homepage LIVE SIGNALS sync
 * ----------------------------------------------------------------------------
 * Loaded on plavena.com (via WPCode footer) as:
 *   <script defer src="https://har50.github.io/plavena-reports/plavena-signals.js"></script>
 *
 * Fetches signals.json (published every Monday by generate.py) and rewrites the
 * hero LIVE SIGNALS panel + the ticker so the marketing site always matches the
 * current weekly report. If the feed is unreachable it does nothing — the
 * existing static markup stays in place (graceful degradation).
 *
 * Single source of truth for the data: the report pipeline. Editorial flavour
 * (exchange labels, demand tags, the Chrome SA desk benchmark) lives here.
 * ==========================================================================*/
(function () {
  "use strict";

  var FEED = "https://har50.github.io/plavena-reports/signals.json";

  // Featured hero cards — order matches the existing .price-card DOM order.
  var CARDS = [
    { key: "copper",    name: "COPPER",      exch: "LME",       note: "" },
    { key: "oil",       name: "BRENT CRUDE", exch: "ICE",       note: "" },
    { key: "iron_ore",  name: "IRON ORE",    exch: "CFR CHINA", note: "India demand ↑" },
    { key: "aluminium", name: "ALUMINIUM",   exch: "LME",       note: "EV demand driver" }
  ];

  // Ticker — curated desk watchlist. `static` rows are Plavena trading
  // benchmarks with no free price feed (shown as estimates).
  var TICKER = [
    { key: "copper" },
    { key: "oil" },
    { key: "iron_ore" },
    { key: "aluminium" },
    { key: "nickel" },
    { key: "met_coal" },
    { key: "lithium" },
    { static: { name: "CHROME SA", price: "~$680/t", est: true } }
  ];

  // ---- helpers -------------------------------------------------------------
  function fmtPct(v) {
    if (v === null || v === undefined) return "—";        // em dash
    return (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
  }
  function unitTail(unit) { return (unit || "").replace("$", ""); }   // "$/t" -> "/t"
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  }); }

  // Build an SVG polyline `points` string scaled into w×h with padding.
  function sparkPoints(hist, w, h, pad) {
    if (!hist || hist.length < 2) return null;
    var lo = Math.min.apply(null, hist), hi = Math.max.apply(null, hist);
    var rng = (hi - lo) || 1, n = hist.length - 1;
    return hist.map(function (v, i) {
      var x = pad + (w - 2 * pad) * (i / n);
      var y = pad + (h - 2 * pad) * (1 - (v - lo) / rng);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
  }

  function metaLine(c) {
    if (c.estimated) return "Plavena estimate";
    if (c.c1w !== null && c.c1w !== undefined)
      return "1W " + fmtPct(c.c1w) + " · YTD " + fmtPct(c.ytd);   // daily series
    if (c.c4w !== null && c.c4w !== undefined)
      return "MoM " + fmtPct(c.c4w) + " · YTD " + fmtPct(c.ytd);  // monthly series
    return "YTD " + fmtPct(c.ytd);
  }

  // ---- renderers -----------------------------------------------------------
  function renderHeader(feed) {
    var hdr = document.querySelector(".hero-right .data-header span");
    if (hdr) hdr.textContent = "LIVE SIGNALS — W" + feed.week + " " + feed.year;
  }

  function renderCards(feed) {
    var cards = document.querySelectorAll(".hero-right .price-card");
    CARDS.forEach(function (cfg, i) {
      var el = cards[i], c = feed.commodities[cfg.key];
      if (!el || !c) return;

      var name = el.querySelector(".pc-name");
      if (name) name.textContent = cfg.name + "  ·  " + cfg.exch;

      var price = el.querySelector(".pc-price");
      if (price) price.innerHTML = esc(c.spot_fmt) +
        '<span class="pc-unit">' + esc(unitTail(c.unit)) + "</span>";

      var chg = el.querySelector(".pc-change");
      if (chg) {
        var up = (c.ytd || 0) >= 0;
        chg.textContent = c.estimated ? "est." : fmtPct(c.ytd);
        chg.className = "pc-change " + (up ? "up" : "down");
      }

      var meta = el.querySelector(".pc-meta");
      if (meta) meta.textContent = cfg.note ? metaLine(c) + " · " + cfg.note : metaLine(c);

      var poly = el.querySelector(".pc-spark polyline");
      if (poly && c.hist) {
        var svg = el.querySelector(".pc-spark");
        var w = +(svg.getAttribute("width") || 60), h = +(svg.getAttribute("height") || 22);
        var pts = sparkPoints(c.hist, w, h, 3);
        if (pts) {
          poly.setAttribute("points", pts);
          poly.setAttribute("stroke", (c.ytd || 0) >= 0 ? "#2BD17E" : "#FF5A5F");
        }
      }
    });
  }

  function tickerItem(row, feed) {
    var name, price, chg, cls, est;
    if (row.static) {
      name = row.static.name; price = row.static.price; chg = "est."; cls = "pos"; est = true;
    } else {
      var c = feed.commodities[row.key];
      if (!c) return "";
      name = c.short; price = c.spot_fmt + unitTail(c.unit);
      est = c.estimated;
      chg = est ? "est." : fmtPct(c.ytd);
      cls = (c.ytd || 0) >= 0 ? "pos" : "neg";
    }
    return '<div class="ticker-item">' +
      '<span class="t-name">' + esc(name) + "</span>" +
      '<span class="t-price">' + esc(price) + "</span>" +
      '<span class="t-chg ' + cls + '"' + (est ? ' style="opacity:.55"' : "") + ">" +
      esc(chg) + "</span></div>";
  }

  function renderTicker(feed) {
    var track = document.querySelector(".ticker-track");
    if (!track) return;
    var items = TICKER.map(function (r) { return tickerItem(r, feed); }).join("");
    if (items) track.innerHTML = items + items;   // duplicate for seamless loop
  }

  // ---- boot ----------------------------------------------------------------
  function apply(feed) {
    try {
      if (!feed || !feed.commodities) return;
      renderHeader(feed);
      renderCards(feed);
      renderTicker(feed);
    } catch (e) { /* never break the page over a data refresh */ }
  }

  function start() {
    // Wait briefly for the hero markup (defer means it's normally already here).
    var tries = 0;
    (function waitFor() {
      if (document.querySelector(".hero-right") || document.querySelector(".ticker-track")) {
        fetch(FEED, { cache: "no-cache" })
          .then(function (r) { return r.json(); })
          .then(apply)
          .catch(function () { /* leave static markup in place */ });
      } else if (tries++ < 40) {
        setTimeout(waitFor, 150);
      }
    })();
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", start);
  else start();
})();
