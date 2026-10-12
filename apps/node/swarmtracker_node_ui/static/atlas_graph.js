/* ============================================================
   AtlasGraph v1.2 — the ONE shared evidence-graph renderer (v0.2.0).
   v1.2 adds epistemic edge styling (deterministic solid / model_inferred
   dashed / analyst_confirmed heavy accent / disputed red) driven by
   relationship.epistemic_status. Backward-compatible: edges without the
   field fall back to v1.1 behavior.

   Canonical source: verity_public_v2/atlas_graph.js
   Deployed copies : swarmtracker_node_ui/static/atlas_graph.js (byte-identical)
                     inlined into verity_embed_v2.html (byte-identical body)
   Do not fork per surface. Contract = FusedReport Atlas fields:
     entities:      [{id, type, label}]
     relationships: [{source, target, relation, artifact_ids}]
   Optional render opts: {title, campaigns: [{campaign_id, confidence,
     artifact_ids}], inspector: <element>} — campaigns drive the cluster
   ring weight (confidence), inspector receives click-to-inspect detail.

   Deterministic layout: golden-angle seeding by entity order + fixed
   iteration count. Identical input → identical geometry on any surface.
   v1.1: label halos, click-to-inspect, campaign-confidence rings, light
   presentation chrome (graph canvas stays dark), 16:9 deck export.
   ============================================================ */
var AtlasGraph = (function () {
  "use strict";

  var COLORS = {
    artifact: "#2a8fd6", domain: "#37c0a3", ip: "#e05252",
    email: "#e0a83a", url: "#9b7ed8", sender: "#e0813a",
    organization: "#38c172", campaign: "#c94f7c"
  };
  var BG = "#0b0f14", EDGE = "#33465e", LABEL = "#c3cfdb", MUTED = "#8296ab";

  function mk(tag, attrs) {
    var el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }
  function escText(s) { return String(s == null ? "" : s); }

  /* ---------- deterministic layout ---------- */
  function layout(entities, relationships) {
    var nodes = {}, idx = 0;
    (entities || []).forEach(function (e) {
      nodes[e.id] = { id: e.id, type: e.type, label: e.label, deg: 0, i: idx++ };
    });
    var edges = [];
    (relationships || []).forEach(function (r) {
      if (nodes[r.source] && nodes[r.target]) {
        edges.push({ s: nodes[r.source], t: nodes[r.target],
                     rel: r.relation || "",
                     epistemic: r.epistemic_status || "" });
        nodes[r.source].deg++; nodes[r.target].deg++;
      }
    });
    var N = Object.keys(nodes).map(function (k) { return nodes[k]; });
    var W = 900, H = 560, cx = W / 2, cy = H / 2;
    N.forEach(function (n, i) {
      var a = i * 2.39996, r = 42 + 15 * Math.sqrt(i);
      n.x = cx + r * Math.cos(a);
      n.y = cy + r * Math.sin(a) * 0.75;
    });
    var K = 98;
    for (var iter = 0; iter < 240; iter++) {
      var temp = 14 * (1 - iter / 240) + 1;
      for (var i = 0; i < N.length; i++) {
        var fx = 0, fy = 0, a2 = N[i];
        for (var j = 0; j < N.length; j++) {
          if (i === j) continue;
          var dx = a2.x - N[j].x, dy = a2.y - N[j].y;
          var d2 = dx * dx + dy * dy + 0.01, d = Math.sqrt(d2);
          var rep = (K * K) / d2;
          fx += (dx / d) * rep * 6; fy += (dy / d) * rep * 6;
        }
        fx += (cx - a2.x) * 0.014; fy += (cy - a2.y) * 0.019;
        a2.fx = fx; a2.fy = fy;
      }
      edges.forEach(function (e) {
        var dx = e.t.x - e.s.x, dy = e.t.y - e.s.y;
        var d = Math.sqrt(dx * dx + dy * dy) + 0.01;
        var f = (d - K) * 0.06;
        e.s.fx += (dx / d) * f * 8; e.s.fy += (dy / d) * f * 8;
        e.t.fx -= (dx / d) * f * 8; e.t.fy -= (dy / d) * f * 8;
      });
      N.forEach(function (n) {
        var m = Math.sqrt(n.fx * n.fx + n.fy * n.fy) + 0.01;
        var step = Math.min(m, temp);
        n.x += (n.fx / m) * step; n.y += (n.fy / m) * step;
      });
    }
    var minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
    N.forEach(function (n) {
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
      minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
    });
    if (!N.length) { minX = 0; minY = 0; maxX = 400; maxY = 60; }
    return { nodes: N, edges: edges,
             bbox: { minX: minX, minY: minY, maxX: maxX, maxY: maxY } };
  }

  function _campConf(opts, id) {
    var cs = (opts && opts.campaigns) || [];
    for (var i = 0; i < cs.length; i++) {
      if (cs[i].campaign_id === id) return cs[i].confidence || 0;
    }
    return 0;
  }

  /* ---------- render into an <svg> ---------- */
  function render(svg, entities, relationships, opts) {
    opts = opts || {};
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    if (!entities || !entities.length) {
      svg.setAttribute("viewBox", "0 0 400 60");
      var t0 = mk("text", { x: 10, y: 35, fill: MUTED, "font-size": "13" });
      t0.textContent = "No entities extracted.";
      svg.appendChild(t0);
      return null;
    }
    var g = layout(entities, relationships);
    var pad = 72;
    svg.setAttribute("viewBox",
      (g.bbox.minX - pad) + " " + (g.bbox.minY - pad) + " " +
      (g.bbox.maxX - g.bbox.minX + 2 * pad) + " " +
      (g.bbox.maxY - g.bbox.minY + 2 * pad));
    if (opts.title) {
      var tt = mk("text", { x: g.bbox.minX - pad + 14,
        y: g.bbox.minY - pad + 26, fill: LABEL, "font-size": "15",
        "font-weight": "700", "font-family": "sans-serif" });
      tt.textContent = escText(opts.title);
      svg.appendChild(tt);
    }
    g.edges.forEach(function (e) {
      var campaign = e.s.type === "campaign" || e.t.type === "campaign";
      // v1.2: epistemic status drives edge styling when present.
      //   deterministic     -> solid
      //   model_inferred     -> dashed
      //   analyst_confirmed  -> heavier solid, accent
      var st = e.epistemic || (campaign ? "model_inferred" : "deterministic");
      var stroke = campaign ? COLORS.campaign : EDGE;
      var width = 1.1, dash = "none";
      if (st === "model_inferred") { dash = "5,4"; width = 1.4;
        stroke = campaign ? COLORS.campaign : "#7d8ba0"; }
      else if (st === "analyst_confirmed") { width = 2.2; stroke = "#37c0a3"; }
      else if (st === "disputed" || st === "rejected") {
        dash = "2,3"; stroke = "#c6342e"; }
      else if (campaign) { dash = "5,4"; width = 1.6; }
      var line = mk("line", {
        x1: e.s.x, y1: e.s.y, x2: e.t.x, y2: e.t.y,
        stroke: stroke, "stroke-width": width,
        "stroke-dasharray": dash, opacity: 0.85
      });
      var lt = mk("title", {});
      lt.textContent = e.s.label + " — " + e.rel + " → " + e.t.label +
        (e.epistemic ? " · " + e.epistemic : "");
      line.appendChild(lt);
      svg.appendChild(line);
    });
    g.nodes.forEach(function (n) {
      var conf = n.type === "campaign" ? _campConf(opts, n.label) : 0;
      var r = n.type === "artifact" ? 13 : (n.type === "campaign" ? 17 :
              7 + Math.min(6, n.deg));
      var c = mk("circle", {
        cx: n.x, cy: n.y, r: r,
        fill: COLORS[n.type] || MUTED,
        "fill-opacity": n.type === "campaign" ? 0.14 : 0.92,
        stroke: COLORS[n.type] || MUTED,
        /* campaign ring weight encodes cluster confidence */
        "stroke-width": n.type === "campaign" ?
          (1.5 + conf / 25) : 1,
        cursor: opts.inspector ? "pointer" : "default"
      });
      var ct = mk("title", {});
      ct.textContent = n.type + ": " + n.label +
        (conf ? " · confidence " + conf + "/100" : "") +
        " · " + n.deg + " connection(s)";
      c.appendChild(ct);
      if (opts.inspector && c.addEventListener) {
        c.addEventListener("click", function () {
          _inspect(opts.inspector, n, g, opts);
        });
      }
      svg.appendChild(c);
      var label = n.label.length > 26 ? n.label.slice(0, 24) + "…" : n.label;
      var t = mk("text", {
        x: n.x, y: n.y + r + 12, fill: LABEL, "font-size": "10",
        "text-anchor": "middle",
        "font-family": "ui-monospace, Menlo, Consolas, monospace",
        /* halo for legibility over edges */
        "paint-order": "stroke", stroke: BG, "stroke-width": "3",
        "stroke-linejoin": "round"
      });
      t.textContent = label;
      svg.appendChild(t);
    });
    return g;
  }

  /* ---------- click-to-inspect ---------- */
  function _inspect(el, node, g, opts) {
    var conns = [];
    g.edges.forEach(function (e) {
      if (e.s.id === node.id) conns.push(e.rel + " → " + e.t.label);
      if (e.t.id === node.id) conns.push(e.s.label + " " + e.rel + " →");
    });
    var conf = node.type === "campaign" ? _campConf(opts, node.label) : 0;
    while (el.firstChild) el.removeChild(el.firstChild);
    var head = document.createElement("div");
    head.style.cssText = "font-weight:700;margin-bottom:3px";
    head.textContent = node.type.toUpperCase() + " · " + node.label +
      (conf ? " · confidence " + conf + "/100" : "");
    el.appendChild(head);
    var body = document.createElement("div");
    body.textContent = node.deg + " connection(s)" +
      (conns.length ? ": " + conns.slice(0, 6).join(" · ") +
        (conns.length > 6 ? " · …" : "") : "");
    el.appendChild(body);
  }

  function legendHTML() {
    var html = "";
    for (var k in COLORS) {
      html += '<span style="display:inline-flex;align-items:center;gap:5px;' +
        'margin-right:13px;font-size:11px;color:' + MUTED + '">' +
        '<span style="display:inline-block;width:10px;height:10px;' +
        'border-radius:50%;background:' + COLORS[k] + '"></span>' + k +
        "</span>";
    }
    return html;
  }

  /* ---------- exports: standalone crisp SVG ---------- */
  function toSVGString(svg) {
    var clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    var vb = (svg.getAttribute("viewBox") || "0 0 900 560").split(/\s+/);
    clone.setAttribute("width", Math.round(parseFloat(vb[2])));
    clone.setAttribute("height", Math.round(parseFloat(vb[3])));
    var bg = mk("rect", { x: vb[0], y: vb[1], width: vb[2], height: vb[3],
                          fill: BG });
    clone.insertBefore(bg, clone.firstChild);
    return '<?xml version="1.0" encoding="UTF-8"?>\n' +
      new XMLSerializer().serializeToString(clone);
  }

  /* 16:9 deck export (1920x1080): dark stage, title, legend, fitted graph */
  function toSVGString169(entities, relationships, opts) {
    opts = opts || {};
    var inner = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    render(inner, entities, relationships, { campaigns: opts.campaigns });
    var vb = inner.getAttribute("viewBox");
    var outer = mk("svg", { xmlns: "http://www.w3.org/2000/svg",
                            width: 1920, height: 1080,
                            viewBox: "0 0 1920 1080" });
    outer.appendChild(mk("rect", { x: 0, y: 0, width: 1920, height: 1080,
                                   fill: BG }));
    var title = mk("text", { x: 60, y: 78, fill: "#e8eef4",
      "font-size": "34", "font-weight": "700", "font-family": "sans-serif" });
    title.textContent = escText(opts.title || "Evidence Relationship Graph");
    outer.appendChild(title);
    if (opts.subtitle) {
      var sub = mk("text", { x: 60, y: 112, fill: MUTED, "font-size": "17",
        "font-family": "sans-serif" });
      sub.textContent = escText(opts.subtitle);
      outer.appendChild(sub);
    }
    var stage = mk("svg", { x: 60, y: 140, width: 1800, height: 830,
      viewBox: vb, preserveAspectRatio: "xMidYMid meet" });
    // move children (works in browser and DOM shim)
    while (inner.firstChild) {
      var ch = inner.firstChild;
      inner.removeChild(ch);
      stage.appendChild(ch);
    }
    outer.appendChild(stage);
    // legend row
    var lx = 60, i = 0;
    for (var k in COLORS) {
      outer.appendChild(mk("circle", { cx: lx + 7, cy: 1024, r: 7,
                                       fill: COLORS[k] }));
      var lt = mk("text", { x: lx + 20, y: 1029, fill: MUTED,
        "font-size": "15", "font-family": "sans-serif" });
      lt.textContent = k;
      outer.appendChild(lt);
      lx += 24 + k.length * 8 + 26; i++;
    }
    var cred = mk("text", { x: 1860, y: 1029, fill: "#5c6c7e",
      "font-size": "13", "text-anchor": "end", "font-family": "sans-serif" });
    cred.textContent = escText(opts.credit ||
      "Verity Core · deterministic evidence graph");
    outer.appendChild(cred);
    return '<?xml version="1.0" encoding="UTF-8"?>\n' +
      new XMLSerializer().serializeToString(outer);
  }

  function _download(blob, filename) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }
  function downloadSVG(svg, filename) {
    _download(new Blob([toSVGString(svg)], { type: "image/svg+xml" }),
              filename || "evidence-graph.svg");
  }
  function _pngFromString(s, w, h, filename) {
    var img = new Image();
    img.onload = function () {
      var canvas = document.createElement("canvas");
      canvas.width = w; canvas.height = h;
      var ctx = canvas.getContext("2d");
      ctx.fillStyle = BG; ctx.fillRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0, w, h);
      canvas.toBlob(function (blob) {
        if (blob) _download(blob, filename);
      }, "image/png");
    };
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(s);
  }
  function downloadPNG(svg, filename, scale) {
    scale = scale || 3;
    var vb = (svg.getAttribute("viewBox") || "0 0 900 560").split(/\s+/);
    _pngFromString(toSVGString(svg),
      Math.round(parseFloat(vb[2]) * scale),
      Math.round(parseFloat(vb[3]) * scale),
      filename || "evidence-graph.png");
  }
  function downloadSVG169(entities, relationships, opts) {
    _download(new Blob([toSVGString169(entities, relationships, opts)],
      { type: "image/svg+xml" }),
      ((opts && opts.filename) || "evidence-graph") + "-169.svg");
  }
  function downloadPNG169(entities, relationships, opts, scale) {
    scale = scale || 2;   // 2 => 3840x2160
    _pngFromString(toSVGString169(entities, relationships, opts),
      1920 * scale, 1080 * scale,
      ((opts && opts.filename) || "evidence-graph") + "-169.png");
  }

  /* ---------- presentation view: light chrome, dark graph canvas ---------- */
  function openPresentation(entities, relationships, opts) {
    opts = opts || {};
    var old = document.getElementById("atlasgraph-presentation");
    if (old) old.remove();
    var ov = document.createElement("div");
    ov.id = "atlasgraph-presentation";
    ov.style.cssText = "position:fixed;inset:0;z-index:99999;background:#f4f6f9;" +
      "display:flex;flex-direction:column;padding:26px 30px;gap:12px";
    var bar = document.createElement("div");
    bar.style.cssText = "display:flex;align-items:center;gap:14px;flex-wrap:wrap";
    var title = document.createElement("div");
    title.style.cssText = "color:#182230;font:800 22px sans-serif;flex:1";
    title.textContent = opts.title || "Evidence Relationship Graph";
    bar.appendChild(title);
    function btn(text, fn, primary) {
      var b = document.createElement("button");
      b.textContent = text;
      b.style.cssText = primary ?
        "background:linear-gradient(135deg,#0e9f84,#1f6fb2);color:#fff;" +
        "border:none;border-radius:7px;padding:9px 18px;font:700 13px sans-serif;cursor:pointer" :
        "background:#fff;color:#182230;border:1px solid #d9e0e8;" +
        "border-radius:7px;padding:9px 16px;font:600 13px sans-serif;cursor:pointer";
      b.onclick = fn; bar.appendChild(b); return b;
    }
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.style.cssText = "flex:1;width:100%;background:" + BG +
      ";border-radius:12px";
    btn("SVG", function () {
      downloadSVG(svg, (opts.filename || "evidence-graph") + ".svg"); });
    btn("PNG 3x", function () {
      downloadPNG(svg, (opts.filename || "evidence-graph") + ".png", 3); });
    btn("Deck 16:9", function () {
      downloadSVG169(entities, relationships, opts);
      downloadPNG169(entities, relationships, opts, 2);
    }, true);
    btn("Close", function () { ov.remove(); });
    ov.appendChild(bar);
    if (opts.subtitle) {
      var sub = document.createElement("div");
      sub.style.cssText = "color:#5c6c7e;font:13px sans-serif";
      sub.textContent = opts.subtitle;
      ov.appendChild(sub);
    }
    ov.appendChild(svg);
    var foot = document.createElement("div");
    foot.style.cssText = "display:flex;justify-content:space-between;" +
      "align-items:center;gap:10px;flex-wrap:wrap";
    var legend = document.createElement("div");
    legend.innerHTML = legendHTML();
    var insp = document.createElement("div");
    insp.style.cssText = "color:#182230;font:12px sans-serif;flex:1;" +
      "text-align:right;min-height:16px";
    insp.textContent = "Click a node to inspect.";
    foot.appendChild(legend); foot.appendChild(insp);
    ov.appendChild(foot);
    document.body.appendChild(ov);
    document.addEventListener("keydown", function esc(ev) {
      if (ev.key === "Escape") { ov.remove();
        document.removeEventListener("keydown", esc); }
    });
    render(svg, entities, relationships,
           { campaigns: opts.campaigns, inspector: insp });
    return ov;
  }

  return { COLORS: COLORS, layout: layout, render: render,
           legendHTML: legendHTML, toSVGString: toSVGString,
           toSVGString169: toSVGString169,
           downloadSVG: downloadSVG, downloadPNG: downloadPNG,
           downloadSVG169: downloadSVG169, downloadPNG169: downloadPNG169,
           openPresentation: openPresentation };
})();
