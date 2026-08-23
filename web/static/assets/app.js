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

  badges(data);  /* async; the header fills in when /health answers */
  buildTrack();
  buildReasons(data);

  document.getElementById("foot-run").textContent =
    `run ${data.run_id} · batch ${data.batch_dir} · model ${data.model_version || "—"}`;

  update();
}

async function badges(data) {
  const el = document.getElementById("badges");

  /* The share of exceptions settled without a model call is the badge worth showing.
     "LLM: mock" described how the run was invoked and read as a limitation; this describes
     what the architecture achieved, which is the same fact stated as the strength it is. */
  const breakdown = data.reason_breakdown || [];
  const total = breakdown.reduce((sum, r) => sum + r.count, 0) || 1;
  const free = breakdown.filter((r) => !r.needs_llm).reduce((sum, r) => sum + r.count, 0);

  let service = null;
  try {
    service = await (await fetch("/health")).json();
  } catch { /* the badge simply omits it */ }

  const items = [
    [`${fmtInt(data.settlements)} settlements`, ""],
    [data.calibrated ? `calibrated · ${data.calibration_method}` : "NOT calibrated",
      data.calibrated ? "live" : "mock"],
    [`${state.points.length} operating points`, ""],
    [`${((free / total) * 100).toFixed(0)}% of exceptions settled with no model call`, "live"],
  ];
  if (service) {
    /* Name it. "model configured" is service-availability information sitting beside the
       central claim, and a vague status badge next to a strong one invites the reader to
       doubt the strong one. A model name is concrete. */
    const shortName = (service.model || "").split("/").pop() || "";
    items.push([
      service.llm === "live" ? shortName : "no model configured",
      service.llm === "live" ? "" : "mock",
    ]);
  }

  el.innerHTML = items
    .map(([text, cls]) => `<span class="badge ${cls}">${text}</span>`)
    .join("");
}

/* ------------------------------------------------------------------ track */

function buildTrack() {
  const track = document.getElementById("track");
  track.innerHTML = "";

  const sizes = state.data.step_sizes || [];

  /* A step wide enough to read gets a label in the gap. Threshold is relative to the
     largest step, so this adapts to a different run rather than hardcoding 732. */
  const largest = Math.max(...sizes.slice(1), 1);
  const LABEL_AT = Math.max(20, largest * 0.08);

  state.points.forEach((point, i) => {
    const tick = document.createElement("button");
    tick.type = "button";
    tick.className = "tick";

    if (i === 0) {
      /* The first point is where the range of choice BEGINS, not a step within it.
         Scaling it by the 2,495 settlements it already matches pushed every real step
         into the right third of the track, and read as a layout bug before it read as
         information. */
      tick.classList.add("endpoint");
      tick.setAttribute("aria-label",
        `most conservative point: ${fmtPct(point.coverage)} auto-matched`);
    } else {
      const size = Math.max(1, sizes[i] || 1);
      tick.style.flexGrow = String(size);
      tick.dataset.big = String(size >= LABEL_AT);
      if (size >= LABEL_AT) {
        const label = document.createElement("span");
        label.className = "tick-label";
        label.textContent = `+${fmtInt(size)} in one step`;
        tick.appendChild(label);
      }
      tick.setAttribute("aria-label",
        `${fmtPct(point.coverage)} auto-matched, ${point.false_matches} wrong, ` +
        `${fmtInt(size)} more settlements than the previous point`);
    }

    tick.dataset.pastFloor = String(point.precision < 0.995);
    tick.addEventListener("click", () => { state.index = i; update(); });
    track.appendChild(tick);
  });

  const steps = sizes.slice(1);
  const foot = document.getElementById("track-foot");
  if (foot) {
    foot.textContent =
      `Left edge is the most conservative point this system can reach ` +
      `(${fmtInt(state.points[0].matched)} settlements). ` +
      `From there, ${steps.length} steps ranging from ` +
      `${fmtInt(Math.min(...steps))} to ${fmtInt(Math.max(...steps))} settlements.`;
  }

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

  /* vector-effect="non-scaling-stroke" is the whole reason this is legible on a shared
     screen. The SVG uses preserveAspectRatio="none" so it stretches to the card width,
     and without this the stroke stretches too -- appearing thinner the wider the card
     gets, which is exactly backwards. The marker is drawn in a nested un-stretched
     coordinate space for the same reason: a scaled circle becomes an ellipse. */
  const cx = x(currentIndex);
  const cy = y(values[currentIndex]);
  svg.innerHTML =
    `<path d="${path}" fill="none" stroke="${colour}" stroke-width="2.5"
       vector-effect="non-scaling-stroke"
       stroke-linejoin="round" stroke-linecap="round" opacity="0.9"/>` +
    `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="4.5" fill="${colour}"
       vector-effect="non-scaling-stroke" stroke="#fff" stroke-width="2"/>`;
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
        <div class="reason-code"><code>${r.code}</code>${
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

/* ==========================================================================
 * Screens two and three: the review queue, and running a batch.
 * ========================================================================== */

const screens = { exceptions: [] };

function switchTo(name) {
  document.querySelectorAll(".tab").forEach((tab) =>
    tab.setAttribute("aria-current", String(tab.dataset.screen === name))
  );
  if (name === "dashboard") render(state.data);
  else if (name === "review") renderReview();
  else renderUpload();
}

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => switchTo(tab.dataset.screen))
);

/* ---------------------------------------------------------------- review */

async function renderReview() {
  const app = document.getElementById("app");
  app.innerHTML = "";
  app.appendChild(document.getElementById("review-template").content.cloneNode(true));

  const select = document.getElementById("filter-code");
  (state.data.reason_breakdown || []).forEach((r) => {
    const option = document.createElement("option");
    option.value = r.code;
    option.textContent = r.code + " (" + fmtInt(r.count) + ")";
    select.appendChild(option);
  });
  select.addEventListener("change", () => loadExceptions(select.value));

  await loadExceptions("");
}

async function loadExceptions(code) {
  const query = new URLSearchParams({ limit: "60" });
  if (code) query.set("code", code);

  const body = await (
    await fetch("/api/runs/" + state.data.run_id + "/exceptions?" + query)
  ).json();
  screens.exceptions = body.exceptions;

  document.getElementById("filter-count").textContent =
    fmtInt(body.exceptions.length) + " shown of " + fmtInt(body.total);

  const list = document.getElementById("ex-list");
  list.innerHTML = body.exceptions
    .map(
      (e, i) =>
        '<li class="ex-item" data-i="' + i + '" tabindex="0">' +
        '<span class="ex-id">' + e.entity_id + "</span>" +
        '<span class="ex-code">' + e.reason_code + "</span></li>"
    )
    .join("");

  list.querySelectorAll(".ex-item").forEach((item) => {
    const pick = () => selectException(Number(item.dataset.i));
    item.addEventListener("click", pick);
    item.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        pick();
      }
    });
  });

  if (body.exceptions.length) selectException(0);
}

async function selectException(index) {
  document.querySelectorAll(".ex-item").forEach((item, i) =>
    item.setAttribute("aria-current", String(i === index))
  );

  const row = screens.exceptions[index];
  const detail = document.getElementById("ex-detail");
  detail.innerHTML = '<p class="empty">Loading evidence…</p>';

  const ev = await (
    await fetch("/api/runs/" + state.data.run_id + "/exceptions/" + row.entity_id)
  ).json();
  detail.innerHTML = evidenceHtml(ev);
  wireActions(ev);
}

function rows(pairs) {
  return pairs
    .filter((pair) => pair[1] !== undefined && pair[1] !== null && pair[1] !== "")
    .map((pair) => '<div class="ev-row"><dt>' + pair[0] + "</dt><dd>" + pair[1] + "</dd></div>")
    .join("");
}

function evidenceHtml(ev) {
  const s = ev.settlement;
  const t = ev.bank_txn;
  const i = ev.invoice;

  const settlementCard = s
    ? rows([
        ["settlement", s.settlement_id], ["UTR", s.utr], ["method", s.method],
        ["settled", s.settled_date], ["gross", s.gross], ["fee", s.fee],
        ["tax", s.tax], ["net", "<strong>" + s.net + "</strong>"],
      ])
    : "<p>not found</p>";

  const bankCard = t
    ? rows([
        ["txn", t.txn_id], ["bank", t.bank], ["value date", t.value_date],
        ["credit", "<strong>" + t.credit + "</strong>"],
      ]) + '<div class="ev-narration">' + t.narration + "</div>"
    : "<p>No bank credit was considered. Blocking produced no candidate for this payout " +
      "— the finding is the absence, not a gap in this screen.</p>";

  const invoiceCard = i
    ? rows([
        ["invoice", i.invoice_id], ["customer", i.customer_name],
        ["dated", i.invoice_date], ["TDS section", i.tds_section],
        ["amount", "<strong>" + i.amount + "</strong>"],
      ])
    : "<p>No invoice could be identified. Only ~38% of gateway rows carry order_receipt, " +
      "and the narration named none.</p>";

  const diff =
    ev.difference_paise !== null && ev.difference_paise !== undefined
      ? '<div class="ev-diff">Bank credit differs from the settlement net by <strong>₹' +
        ev.difference +
        "</strong>. That difference is what a human is being asked to explain.</div>"
      : "";

  return (
    '<div class="ev-head"><h3>' + ev.entity_id + "</h3>" +
    '<span class="ex-code">' + ev.reason_family + "</span></div>" +
    '<p class="ev-why"><code>' + ev.reason_code + "</code> " + ev.detail + "</p>" +
    '<div class="ev-cards">' +
    '<div class="ev-card"><h4>Gateway settlement</h4>' + settlementCard + "</div>" +
    '<div class="ev-card' + (t ? "" : " absent") + '"><h4>Bank credit</h4>' + bankCard + "</div>" +
    '<div class="ev-card' + (i ? "" : " absent") + '"><h4>Invoice</h4>' + invoiceCard + "</div>" +
    "</div>" + diff +
    '<div class="ev-actions">' +
    '<input id="approver" placeholder="your name" value="dushyant">' +
    '<button type="button" class="btn approve" data-action="approve">Approve</button>' +
    '<button type="button" class="btn reject" data-action="reject">Reject</button>' +
    '<button type="button" class="btn journal" id="propose">Propose journal entry</button>' +
    "</div>" +
    '<div class="ev-result" id="ev-result" hidden></div>'
  );
}

function wireActions(ev) {
  const result = document.getElementById("ev-result");
  const show = (html) => {
    result.hidden = false;
    result.innerHTML = html;
  };

  document.querySelectorAll("[data-action]").forEach((button) =>
    button.addEventListener("click", async () => {
      const approver = document.getElementById("approver").value.trim();
      if (!approver) return show("An approval with no approver is not an approval.");

      button.disabled = true;
      try {
        const response = await fetch(
          "/api/runs/" + state.data.run_id + "/exceptions/" + ev.entity_id + "/decision",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: button.dataset.action, approver: approver }),
          }
        );
        const body = await response.json();
        show(
          "Recorded as <strong>" + body.record.decision + "</strong> by <strong>" +
          body.record.approver + "</strong> — stored in <code>" + body.stored_in +
          "</code>, " + body.detail + ".<br>Filed as an escalation rather than a match: a " +
          "human verdict counted in the auto-match rate would corrupt the number the whole " +
          "thesis rests on."
        );
      } catch (err) {
        show("Could not record the decision: " + err.message);
      } finally {
        button.disabled = false;
      }
    })
  );

  document.getElementById("propose").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "Asking the model…";
    try {
      const body = await (
        await fetch(
          "/api/runs/" + state.data.run_id + "/exceptions/" + ev.entity_id + "/journal",
          { method: "POST" }
        )
      ).json();

      if (!body.proposed) {
        show("No entry proposed — <code>" + body.reason_code + "</code>: " + body.detail);
        return;
      }
      show(entryHtml(body));
    } catch (err) {
      show("Could not propose an entry: " + err.message);
    } finally {
      button.disabled = false;
      button.textContent = "Propose journal entry";
    }
  });
}

function entryHtml(body) {
  const entry = body.proposed;
  const money = (paise) =>
    paise ? "₹" + (paise / 100).toLocaleString("en-IN") : "";

  const lines = (entry.lines || [])
    .map(
      (l) =>
        "<tr><td>" + l.account_code + "</td><td>" + (l.narrative || "") +
        '</td><td class="num">' + money(l.debit) +
        '</td><td class="num">' + money(l.credit) + "</td></tr>"
    )
    .join("");

  return (
    "<div><strong>Proposed for a human to approve — never posted automatically.</strong> " +
    (entry.narrative || "") + "</div>" +
    '<div class="entry"><table><thead><tr><th>Account</th><th>Line</th>' +
    "<th>Debit</th><th>Credit</th></tr></thead><tbody>" + lines + "</tbody></table></div>" +
    '<div style="margin-top:10px">The chart of accounts is a closed set, so the model ' +
    "cannot post to an account that does not exist — and the entry had to balance to the " +
    "paisa or it would have been refused before reaching this screen.<br><code>" +
    body.audit.prompt_version + "</code> · " + fmtInt(body.usage.billed_tokens) + " tokens</div>"
  );
}

/* ---------------------------------------------------------------- upload */

function renderUpload() {
  const app = document.getElementById("app");
  app.innerHTML = "";
  app.appendChild(document.getElementById("upload-template").content.cloneNode(true));

  const inputs = ["f-gateway", "f-bank", "f-invoices"].map((id) =>
    document.getElementById(id)
  );
  const runButton = document.getElementById("run-upload");

  inputs.forEach((input) =>
    input.addEventListener("change", () => {
      input.closest(".drop").classList.toggle("filled", Boolean(input.files.length));
      runButton.disabled = !inputs.every((f) => f.files.length);
    })
  );

  document.getElementById("run-demo").addEventListener("click", () =>
    submitRun({ batch_dir: "data/demo", run_id: "demo-live", mock_llm: true })
  );

  runButton.addEventListener("click", async () => {
    const form = new FormData();
    form.append("gateway", inputs[0].files[0]);
    form.append("bank", inputs[1].files[0]);
    form.append("invoices", inputs[2].files[0]);

    setJob("Uploading…", 0.1, "");
    try {
      const uploaded = await (
        await fetch("/api/upload", { method: "POST", body: form })
      ).json();
      if (uploaded.detail) return setJob("Upload refused", 0, uploaded.detail);
      await submitRun({
        batch_dir: uploaded.batch_dir,
        run_id: "upload-" + Date.now(),
        mock_llm: true,
      });
    } catch (err) {
      setJob("Upload failed", 0, err.message);
    }
  });
}

function setJob(step, progress, note) {
  document.getElementById("job").hidden = false;
  document.getElementById("job-step").textContent = step;
  document.getElementById("job-fill").style.width = Math.round(progress * 100) + "%";
  document.getElementById("job-note").innerHTML = note;
}

async function submitRun(payload) {
  setJob(
    "Submitting…", 0.05,
    "Long runs are jobs with status polling, never a blocking request."
  );

  const job = await (
    await fetch("/api/jobs/recon", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
  ).json();

  if (!job.job_id) return setJob("Refused", 0, job.detail || "unknown error");

  /* Poll rather than wait. The API never blocks on a run, so neither does the screen. */
  const poll = setInterval(async () => {
    const status = await (await fetch("/api/jobs/" + job.job_id)).json();
    setJob(status.step || status.status, status.progress || 0.3, "");

    if (status.status === "done") {
      clearInterval(poll);
      const result = status.result || {};
      setJob(
        "Done", 1,
        fmtInt(result.matched || 0) + " matched, " +
        fmtInt(result.exceptions || 0) + ' exceptions. <a href="#" id="open-run">Open this run</a>'
      );
      const link = document.getElementById("open-run");
      if (link) {
        link.addEventListener("click", async (event) => {
          event.preventDefault();
          const data = await (await fetch("/api/runs/" + result.run_id)).json();
          state.data = data;
          state.points = data.operating_points;
          state.index = data.selected_index || 0;
          switchTo("dashboard");
        });
      }
    } else if (status.status === "failed") {
      clearInterval(poll);
      setJob("Failed", 0, status.error || "unknown error");
    }
  }, 900);
}
