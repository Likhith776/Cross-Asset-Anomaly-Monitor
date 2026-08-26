/* Dithered plasma hero.
   Original WebGL2 implementation of the reference effect: a slow greyscale
   plasma field, ordered-dithered through an 8x8 Bayer matrix onto a
   fixed grey ladder, with the headline drawn into a texture and composited
   inside the field so the type resolves out of the noise on first paint.

   No library, one full-screen quad, RAF paused when the hero is off-screen
   or the tab is hidden, and a static frame under prefers-reduced-motion.
   Without WebGL2 the DOM headline stays visible as the fallback. */
(function () {
  "use strict";

  /* The grey ladder measured on the reference site (0-255). */
  var LADDER = [0, 27, 48, 71, 94, 119, 145, 171, 198, 226, 255];
  var CELL = 3; /* device px per dither cell == 3 CSS px */

  var VERT = [
    "#version 300 es",
    "in vec2 a_position;",
    "void main(){ gl_Position = vec4(a_position, 0.0, 1.0); }"
  ].join("\n");

  var FRAG = [
    "#version 300 es",
    "precision highp float;",
    "uniform vec2 uRes;",
    "uniform float uTime;",
    "uniform float uReveal;",
    "uniform float uDisp;",
    "uniform float uHasText;",
    "uniform sampler2D uText;",
    "out vec4 fragColor;",

    "float ladderStep(float r){",
    "  r = clamp(r, 0.0, 255.0);",
    "  float v = 0.0;",
    "  v = mix(v, 27.0,  step(13.5,  r));",
    "  v = mix(v, 48.0,  step(37.5,  r));",
    "  v = mix(v, 71.0,  step(59.5,  r));",
    "  v = mix(v, 94.0,  step(82.5,  r));",
    "  v = mix(v, 119.0, step(106.5, r));",
    "  v = mix(v, 145.0, step(132.0, r));",
    "  v = mix(v, 171.0, step(158.0, r));",
    "  v = mix(v, 198.0, step(184.5, r));",
    "  v = mix(v, 226.0, step(212.0, r));",
    "  v = mix(v, 255.0, step(240.5, r));",
    "  return v / 255.0;",
    "}",

    "float bayer2(vec2 a){ a = floor(a); return fract(a.x * 0.5 + a.y * a.y * 0.75); }",
    "float bayer4(vec2 a){ return bayer2(a * 0.5) * 0.25 + bayer2(a); }",
    "float bayer8(vec2 a){ return bayer4(a * 0.5) * 0.25 + bayer2(a); }",

    "float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123); }",
    "float vnoise(vec2 p){",
    "  vec2 i = floor(p), f = fract(p);",
    "  vec2 u = f * f * (3.0 - 2.0 * f);",
    "  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),",
    "             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);",
    "}",
    "float fbm(vec2 p){",
    "  float a = 0.5, s = 0.0;",
    "  for (int i = 0; i < 6; i++){ s += a * vnoise(p); p = p * 2.03 + 7.31; a *= 0.5; }",
    "  return s;",
    "}",

    "float plasma(vec2 p, float t){",
    "  vec2 q = vec2(fbm(p + vec2(0.0, t * 0.06)), fbm(p + vec2(5.2, 1.3) - t * 0.05));",
    "  float v = fbm(p + 2.4 * q + vec2(t * 0.03, -t * 0.025));",
    "  v = v + 0.55 * fbm(p * 0.45 - q * 1.1 - t * 0.02);",
    "  return clamp((v - 0.18) * 1.35, 0.0, 1.0);",
    "}",

    "float vignette(vec2 uv){",
    "  vec2 d = (uv - vec2(0.5, 0.4)) / vec2(0.42, 0.38);",
    "  float s = clamp(length(d), 0.0, 1.0);",
    "  return 1.0 - s * 0.62 * smoothstep(0.15, 1.0, s);",
    "}",

    "void main(){",
    "  vec2 fc = gl_FragCoord.xy;",
    "  vec2 uv = fc / uRes;",
    "  vec2 asp = vec2(uRes.x / uRes.y, 1.0);",
    "  float t = uTime;",
    "  float lum = plasma(uv * asp * 1.6, t);",
    "  lum *= vignette(vec2(uv.x, 1.0 - uv.y));",
    "  float d = bayer8(fc);",
    "  float gray = ladderStep(lum * 255.0 + (d - 0.5) * 26.0);",
    "  vec3 col = vec3(gray);",
    "  if (uHasText > 0.5){",
    "    vec2 disp = vec2(fbm(uv * asp * 1.1 + t * 0.05), fbm(uv * asp * 1.1 + 9.7 - t * 0.04)) - 0.5;",
    "    vec2 snapped = floor(disp * uDisp / 2.0 + 0.5) * 2.0;",
    "    vec2 tuv = (fc + snapped) / uRes;",
    "    float mask = texture(uText, vec2(tuv.x, 1.0 - tuv.y)).a;",
    "    float grain = fbm(tuv * asp * 26.0);",
    "    float reveal = clamp(uReveal * 1.7 - grain * 1.1, 0.0, 1.0);",
    "    mask *= smoothstep(0.0, 1.0, reveal);",
    "    col = mix(col, vec3(1.0), mask);",
    "  }",
    "  fragColor = vec4(col, 1.0);",
    "}"
  ].join("\n");

  function compile(gl, type, src) {
    var sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      throw new Error("shader: " + gl.getShaderInfoLog(sh));
    }
    return sh;
  }

  function headlineCanvas(w, h, lines, fontSize, padX, padY) {
    var c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    var ctx = c.getContext("2d");
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#fff";
    ctx.textBaseline = "alphabetic";
    ctx.font = "400 " + fontSize + "px 'JetBrains Mono', 'Courier New', monospace";
    var lineH = fontSize * 1.15;
    var total = lines.length * lineH;
    var y = (h - total) / 2 + fontSize * 0.86;
    ctx.save();
    ctx.translate(0, padY);
    for (var i = 0; i < lines.length; i++) {
      ctx.fillText(lines[i], padX, y + i * lineH);
    }
    ctx.restore();
    return c;
  }

  function boot() {
    var hero = document.querySelector("[data-dither-hero]");
    if (!hero) return;
    var canvas = hero.querySelector(".dither-hero__canvas");
    var heading = hero.querySelector("h1");
    if (!canvas || !heading) return;

    var reduce = window.matchMedia("(prefers-reduced-motion: reduce)");

    var lines = (hero.getAttribute("data-hero-lines") || heading.textContent || "")
      .split("|").map(function (s) { return s.trim(); }).filter(Boolean);
    if (!lines.length) return;

    var gl = canvas.getContext("webgl2", {
      alpha: false,
      antialias: false,
      depth: false,
      stencil: false,
      powerPreference: "low-power",
      preserveDrawingBuffer: false
    });

    if (!gl) {
      hero.classList.add("is-hero-title-fallback", "is-canvas-title-ready");
      return;
    }

    var prog, buf, uLoc, textTex, textCanvas = null;
    try {
      prog = gl.createProgram();
      gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
      gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FRAG));
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
        throw new Error("link: " + gl.getProgramInfoLog(prog));
      }
      gl.useProgram(prog);

      buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      var aPos = gl.getAttribLocation(prog, "a_position");
      gl.enableVertexAttribArray(aPos);
      gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

      uLoc = {
        res: gl.getUniformLocation(prog, "uRes"),
        time: gl.getUniformLocation(prog, "uTime"),
        reveal: gl.getUniformLocation(prog, "uReveal"),
        disp: gl.getUniformLocation(prog, "uDisp"),
        hasText: gl.getUniformLocation(prog, "uHasText"),
        text: gl.getUniformLocation(prog, "uText")
      };

      textTex = gl.createTexture();
    } catch (err) {
      hero.classList.add("is-hero-title-fallback", "is-canvas-title-ready");
      return;
    }

    var state = { w: 0, h: 0, cssW: 0, cssH: 0 };

    function resize() {
      var rect = hero.getBoundingClientRect();
      var cssW = Math.max(320, Math.round(rect.width));
      var cssH = Math.max(240, Math.round(rect.height));
      if (cssW === state.cssW && cssH === state.cssH) return false;
      state.cssW = cssW;
      state.cssH = cssH;
      state.w = Math.max(1, Math.round(cssW / CELL));
      state.h = Math.max(1, Math.round(cssH / CELL));
      canvas.width = state.w;
      canvas.height = state.h;
      canvas.style.width = cssW + "px";
      canvas.style.height = cssH + "px";

      var cs = getComputedStyle(document.documentElement);
      var h1size = parseFloat(cs.getPropertyValue("--h1-size")) || 57;
      var padCard = parseFloat(cs.getPropertyValue("--spacing-card")) || 24;
      var fontSize = Math.max(12, h1size / CELL);
      var padX = Math.max(2, padCard / CELL);
      textCanvas = headlineCanvas(state.w, state.h, lines, fontSize, padX, 0);

      gl.bindTexture(gl.TEXTURE_2D, textTex);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, textCanvas);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

      gl.viewport(0, 0, state.w, state.h);
      gl.uniform2f(uLoc.res, state.w, state.h);
      gl.uniform1f(uLoc.disp, Math.max(2, 5 / CELL));
      gl.uniform1f(uLoc.hasText, 1);
      gl.uniform1i(uLoc.text, 0);
      return true;
    }

    var start = performance.now();
    var raf = null;
    var onScreen = true;
    var visible = true;

    function frame(now) {
      raf = null;
      if (!onScreen || !visible) return;
      var t = (now - start) / 1000;
      gl.uniform1f(uLoc.time, t);
      gl.uniform1f(uLoc.reveal, Math.min(1, t / 1.6));
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      raf = requestAnimationFrame(frame);
    }

    function play() {
      if (raf === null && onScreen && visible) raf = requestAnimationFrame(frame);
    }

    function drawStatic() {
      gl.uniform1f(uLoc.time, 1.5);
      gl.uniform1f(uLoc.reveal, 1);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }

    resize();
    hero.classList.add("is-canvas-title-ready");

    if (reduce.matches) {
      hero.classList.add("is-text-hidden");
      drawStatic();
    } else {
      hero.classList.add("is-text-hidden");
      play();
    }

    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        onScreen = entries[0].isIntersecting;
        if (onScreen && !reduce.matches) play();
      }, { rootMargin: "120px" }).observe(hero);
    }

    document.addEventListener("visibilitychange", function () {
      visible = !document.hidden;
      if (visible && !reduce.matches) play();
    });

    var resizeTimer = null;
    window.addEventListener("resize", function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        if (resize()) {
          if (reduce.matches) drawStatic();
          else play();
        }
      }, 160);
    });

    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () {
        state.cssW = -1;
        if (resize() && reduce.matches) drawStatic();
      });
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
