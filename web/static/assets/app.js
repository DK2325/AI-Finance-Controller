/* The operating-point explorer.
 *
 * Three properties this must hold, each of them a claim the rest of the project makes:
 *
 *  1. It offers only points the system can reach. The track is built from the operating
 *     points the API served; there is no interpolation, and dragging snaps. A continuous
 *     slider would imply a resolution isotonic calibration does not have -- 99.7% of
 *     candidates share an exact calibrated probability.
 *
 *  2. Tick spacing is the step size. A step admitting 732 settlements is drawn 732 times
 *     wider than one admitting 1. Drawing them evenly would be a lie about the shape of
 *     the model, and the big step is the most interesting thing on the screen.
 *
 *  3. Precision is shown with its interval. At these counts the point estimate oversells
 *     itself -- 2 false in 3,237 cannot distinguish 99.94% from 99.8%.
 *
 * No build step and no framework. See BUILD.md for the reasoning.
 */

const state = { points: [], index: 0, data: null };

const fmtInt = (n) => n.toLocaleString("en-IN");
const fmtPct = (x) => (x * 100).toFixed(2) + "%";
const rupees = (paise) =>
  "₹" + (paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 0 });

/* The precision bar is drawn on a 98.0%-100.0% scale: the whole story lives in the last
   two points, and a 0-100% axis would render every operating point as the same full bar. */
const P_MIN = 0.98;
const P_MAX = 1.0;
const scaleP = (p) => Math.max(0, Math.min(100, ((p - P_MIN) / (P_MAX - P_MIN)) * 100));

async function boot() {
  const app = document.getElementById("app");
  try {
    const runs = await (await fetch("/api/runs")).json();
    if (!runs.runs.length) throw new Error("no runs are present");

    /* Prefer the seeded run; otherwise whichever has the most settlements. */
    const seeded =
      runs.runs.find((r) => r.run_id === "v1-train") ||
      runs.runs.slice().sort((a, b) => b.settlements - a.settlements)[0];

    const data = await (await fetch(`/api/runs/${seeded.run_id}`)).json();
    state.data = data;
    state.points = data.operating_points;
    state.index = data.selected_index || 0;

    render(data);
  } catch (err) {
    app.innerHTML =
      `<p class="loading">Could not load a run: ${String(err.message || err)}</p>`;
  }
}

function render(data) {
  const app = document.getElementById("app");
  app.innerHTML = "";
  app.appendChild(
    document.getElementById("explorer-template").content.cloneNode(true)
  );

  badges(data);
  buildTrack();
  buildReasons(data);

  document.getElementById("foot-run").textContent =
    `run ${data.run_id} · batch ${data.batch_dir} · model ${data.model_version || "—"}`;

  update();
}

function badges(data) {
  const el = document.getElementById("badges");
  const items = [
    [`${fmtInt(data.settlements)} settlements`, ""],
    [data.calibrated ? `calibrated · ${data.calibration_method}` : "NOT calibrated",
      data.calibrated ? "live" : "mock"],
    [data.mock_llm ? "LLM: mock" : "LLM: live", data.mock_llm ? "mock" : "live"],
    [`${state.points.length} operating points`, ""],
  ];
  el.innerHTML = items
    .map(([text, cls]) => `<span class="badge ${cls}">${text}</span>`)
    .join("");
}

/* ------------------------------------------------------------------ track */

function buildTrack() {
  const track = document.getElementById("track");
  track.innerHTML = "";

  const sizes = state.data.step_sizes || [];
  state.points.forEach((point, i) => {
    const tick = document.createElement("button");
    tick.type = "button";
    tick.className = "tick";
    /* flex-grow IS the step size, so spacing carries information rather than decoration.
       Minimum 1 so a zero-width step is still clickable. */
    tick.style.flexGrow = String(Math.max(1, sizes[i] || 1));
    tick.setAttribute("aria-label",
      `${fmtPct(point.coverage)} auto-matched, ${point.false_matches} wrong`);
    tick.dataset.pastFloor = String(point.precision < 0.995);
    tick.addEventListener("click", () => { state.index = i; update(); });
    track.appendChild(tick);
  });

  /* Keyboard: the track is a real control, not a decorative strip. */
  track.tabIndex = 0;
  track.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight" || event.key === "ArrowUp") {
      state.index = Math.min(state.points.length - 1, state.index + 1);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
      state.index = Math.max(0, state.index - 1);
    } else if (event.key === "Home") {
      state.index = 0;
    } else if (event.key === "End") {
      state.index = state.points.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    update();
  });
}

/* ---------------------------------------------------------------- sparks */

function spark(svg, values, currentIndex, rising) {
  const w = 200;
  const h = 44;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const x = (i) => (i / Math.max(1, values.length - 1)) * w;
  const y = (v) => h - 4 - ((v - min) / span) * (h - 10);

  const path = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)
    .join(" ");
  const colour = rising ? "#b3261e" : "#0d7a4a";

  svg.innerHTML =
    `<path d="${path}" fill="none" stroke="${colour}" stroke-width="2"
       stroke-linejoin="round" stroke-linecap="round" opacity="0.85"/>` +
    `<circle cx="${x(currentIndex).toFixed(1)}" cy="${y(values[currentIndex]).toFixed(1)}"
       r="4" fill="${colour}"/>`;
}

/* ---------------------------------------------------------------- update */

function update() {
  const point = state.points[state.index];
  const data = state.data;

  document.querySelectorAll(".tick").forEach((tick, i) =>
    tick.setAttribute("aria-current", String(i === state.index))
  );

  const set = (name, value) => {
    const el = document.querySelector(`[data-bind="${name}"]`);
    if (el) el.textContent = value;
  };

  set("coverage", fmtPct(point.coverage));
  set("matched", fmtInt(point.matched));
  set("settlements", fmtInt(data.settlements));
  set("to_review", fmtInt(point.to_review));

  set("to_review_v", fmtInt(point.to_review));
  set("wrong_money_v", rupees(point.wrong_money_paise));
  set("total_money", "₹" + point.total_money.split(".")[0]);
  set("cost_v", `₹${point.cost_low_inr.toFixed(2)}–₹${point.cost_high_inr.toFixed(2)}`);

  /* Counts first, percentage second: "2 wrong matches" is the sentence a finance operator
     reasons about; 99.94% is the one that invites a question the sample cannot answer. */
  set("precision", `${point.false_matches} wrong of ${fmtInt(point.matched)}`);
  set("ci", `${fmtPct(point.precision)} · 95% CI ${fmtPct(point.precision_ci_low)}–${fmtPct(point.precision_ci_high)}`);

  const bar = document.getElementById("ci-bar");
  const low = scaleP(point.precision_ci_low);
  const high = scaleP(point.precision_ci_high);
  bar.style.left = low + "%";
  bar.style.width = Math.max(0.6, high - low) + "%";

  const floorInside =
    point.precision_ci_low < 0.995 && point.precision_ci_high > 0.995;
  set(
    "precision_note",
    floorInside
      ? `The 99.5% floor sits inside this interval, so at ${point.false_matches} error` +
        `${point.false_matches === 1 ? "" : "s"} the estimate cannot distinguish holding ` +
        `the floor from missing it. The interval is the honest reading, not the point.`
      : point.precision >= 0.995
        ? "The whole interval clears the 99.5% floor."
        : "The whole interval sits below the 99.5% floor."
  );

  const cliff = document.getElementById("cliff");
  if (point.precision < 0.995) {
    cliff.hidden = false;
    set(
      "cliff_text",
      `${point.false_matches} wrong matches and ${rupees(point.wrong_money_paise)} ` +
      `posted against the wrong invoice — for ${fmtInt(point.to_review)} fewer reviews.`
    );
  } else {
    cliff.hidden = true;
  }

  const series = (key) => state.points.map((p) => p[key]);
  document.querySelectorAll("[data-spark]").forEach((svg) => {
    const key = svg.dataset.spark;
    spark(svg, series(key), state.index, key === "wrong_money_paise");
  });
}

/* --------------------------------------------------------------- reasons */

function buildReasons(data) {
  const list = document.getElementById("reasons");
  const total = data.reason_breakdown.reduce((sum, r) => sum + r.count, 0) || 1;
  const free = data.reason_breakdown
    .filter((r) => !r.needs_llm)
    .reduce((sum, r) => sum + r.count, 0);

  const el = document.querySelector('[data-bind="free_share"]');
  if (el) el.textContent = ((free / total) * 100).toFixed(1) + "%";

  list.innerHTML = data.reason_breakdown
    .map(
      (r) => `<li class="reason">
        <div><code>${r.code}</code>${
          r.needs_llm
            ? '<span class="pill llm">explained by model</span>'
            : '<span class="pill free">no model call</span>'
        }</div>
        <div class="count">${fmtInt(r.count)}</div>
        <div class="why">${r.description}</div>
      </li>`
    )
    .join("");
}

boot();
