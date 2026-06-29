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

  // ---- lead capture: free-brief CTA + HubSpot modal (on plavena.com) ------
  function mountLeadCapture() {
    if (document.getElementById("plv-cta")) return;
    var path = (location.pathname || "").toLowerCase();
    if (path.indexOf("checkout") > -1 || path.indexOf("cart") > -1) return;

    var css =
      "#plv-cta{position:fixed;right:22px;bottom:22px;z-index:99998;display:flex;flex-direction:column;align-items:flex-start;"
      + "background:#0D1B2A;border:1px solid #00B3FF;color:#E8EDF4;border-radius:12px;padding:10px 18px;cursor:pointer;"
      + "font-family:Inter,system-ui,sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.45);transition:transform .15s}"
      + "#plv-cta:hover{transform:translateY(-2px)}"
      + "#plv-cta .eyl{font-size:10.5px;color:#00B3FF;letter-spacing:.1em;text-transform:uppercase;margin-bottom:1px}"
      + "#plv-cta b{font-size:14px;font-weight:600}"
      + "#plv-ov{display:none;position:fixed;inset:0;z-index:99999;background:rgba(4,9,18,.74);align-items:center;justify-content:center;padding:20px}"
      + "#plv-card{position:relative;width:100%;max-width:460px;background:#0D1B2A;border:1px solid #1B2A3D;border-radius:16px;padding:30px 28px;font-family:Inter,system-ui,sans-serif;color:#E8EDF4}"
      + "#plv-card h3{font-size:22px;font-weight:700;margin:0 0 8px}#plv-card p{font-size:14.5px;color:#94A6BC;margin:0 0 18px;line-height:1.5}"
      + "#plv-close{position:absolute;top:12px;right:15px;background:none;border:none;color:#5B6B7F;font-size:25px;cursor:pointer;line-height:1}"
      + "#plv-form .hs-form-field>label{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}"
      + "#plv-form .hs-input{width:100%;background:#0a1622;border:1px solid #1B2A3D;color:#E8EDF4;padding:13px 14px;border-radius:8px;font-size:15px;font-family:Inter,sans-serif;box-sizing:border-box}"
      + "#plv-form .hs-input:focus{outline:none;border-color:#00B3FF}#plv-form .hs-input::placeholder{color:#5B6B7F}"
      + "#plv-form .hs_submit{margin-top:10px}#plv-form .hs-button{width:100%;background:#00B3FF;color:#04111E;font-weight:600;font-size:15px;padding:13px;border:none;border-radius:8px;cursor:pointer;font-family:Inter,sans-serif}"
      + "#plv-form .hs-error-msgs{list-style:none;padding:0;margin:6px 0 0;color:#FF5A5F;font-size:12.5px}#plv-form .hs-form-required{color:#FF5A5F}"
      + "#plv-success{display:none;text-align:center}#plv-success .ok{color:#2BD17E;font-size:17px;margin-bottom:14px}"
      + "#plv-success a.go{display:inline-block;background:#00B3FF;color:#04111E;font-weight:600;padding:12px 24px;border-radius:8px;text-decoration:none}";
    var st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);

    var cta = document.createElement("button");
    cta.id = "plv-cta"; cta.type = "button";
    cta.innerHTML = "<span class='eyl'>Free this week</span><b>Get the full brief &rarr;</b>";
    document.body.appendChild(cta);

    var ov = document.createElement("div");
    ov.id = "plv-ov";
    ov.innerHTML = "<div id='plv-card'>"
      + "<button id='plv-close' aria-label='Close'>&times;</button>"
      + "<h3>Get the full brief &mdash; free.</h3>"
      + "<p>Drop your email and we&rsquo;ll send you this week&rsquo;s complete intelligence brief &mdash; then it lands every Monday, ahead of the desk.</p>"
      + "<div id='plv-form'></div>"
      + "<div id='plv-success'><div class='ok'>&#10003; You&rsquo;re in &mdash; your brief is unlocked.</div>"
      + "<a class='go' href='https://har50.github.io/plavena-reports/' target='_blank' rel='noopener'>Open this week&rsquo;s brief &rarr;</a></div>"
      + "</div>";
    document.body.appendChild(ov);

    var loaded = false;
    function openModal(){ ov.style.display = "flex"; if (!loaded){ loaded = true; loadForm(); } }
    function closeModal(){ ov.style.display = "none"; }
    cta.addEventListener("click", openModal);
    document.getElementById("plv-close").addEventListener("click", closeModal);
    ov.addEventListener("click", function (e){ if (e.target === ov) closeModal(); });
    document.addEventListener("keydown", function (e){ if (e.key === "Escape") closeModal(); });

    function build(){
      if (!(window.hbspt && window.hbspt.forms)) return;
      window.hbspt.forms.create({
        portalId: "246330048", formId: "356611e8-3bbe-4055-abe2-91f229bf8cd9", region: "na2",
        target: "#plv-form",
        onFormReady: function (){ var e = document.querySelector("#plv-form input.hs-input"); if (e) e.setAttribute("placeholder", "you@company.com"); },
        onFormSubmitted: function (){ var f = document.getElementById("plv-form"); if (f) f.style.display = "none"; var s = document.getElementById("plv-success"); if (s) s.style.display = "block"; }
      });
    }
    function loadForm(){
      if (window.hbspt && window.hbspt.forms){ build(); return; }
      var s = document.createElement("script");
      s.src = "https://js-na2.hsforms.net/forms/embed/v2.js"; s.charset = "utf-8";
      s.onload = build; document.head.appendChild(s);
    }
  }

  function start() {
    mountLeadCapture();
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
