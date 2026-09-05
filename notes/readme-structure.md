# Three things a first-time reader hits, and why they are not fixed yet

Found by reading the README cold, as someone who has never heard of this project — the
audience it is actually written for, and the one perspective the author cannot occupy.

All three are real. None is fixed, and that is a decision rather than an oversight: the
restructuring each would need is large, the document is load-bearing for the submission,
and **the pitch can carry the ordering the README does not.** State what it does, show it
working, then give the caveats. Fixing the sequence in the video is cheap and reversible;
fixing it in a 1,053-line document is neither.

Recorded here so they are known rather than discovered by a reviewer.

---

### 1. The caveat arrives before the capability

By line 67 the reader is inside *"Two precision figures, and which one to plan against"* —
a section about a number that **failed its own floor** — before being told what a
settlement is or what the system produces.

The honesty is this project's strongest asset and it should not be softened. But a reader
meeting the disclaimer before the demonstration has nothing to attach it to, and the
sequence invites the question *"does this work at all?"* at exactly the point where the
answer has not been given yet.

Compounding it: **62.91% coverage as the headline reads as "it fails 37% of the time"**
unless the reader already knows what selective prediction is. The number is the product of
the thesis, not a shortfall against it, and nothing above it says so.

### 2. 1,053 lines, no table of contents, and `Running it` is at line 655

Section titles are essayistic — *"A theorem about our own design"*, *"The audit trail
names where the evidence ran out"* — which reads well in sequence and defeats skimming
entirely. A reviewer looking for *what does it do → does it work → how do I run it* has to
scroll past roughly six hundred lines of failure analysis to reach the one command they
need.

The failure analysis is the best writing in the repository. It is also not what a first
read is for.

### 3. Evaluation vocabulary is assumed, for a finance audience

*Sealed*, *held out*, *pre-committed threshold*, *calibration*, *coverage*, *selective
prediction*, *isotonic* — all appear before any is explained, most of them before line 100.

This is Track 04, **AI Finance Controller**. The reader most likely to be impressed by
"the test set was sealed with sha256 hashes before the model existed, and opened once" is
precisely the reader least likely to already know why that is rare. The vocabulary carrying
the strongest claims is the vocabulary least likely to land.

---

### Seeding doubt underneath all three

The first screen of numbers says **99.90%** where the rest of the document says
**99.9037%**, and **`Rs`** where the rest says **`₹`** — both inside the auto-generated
`METRICS` block, so they drift from the hand-written prose around them.

Small, and on a project whose entire argument is measurement discipline, formatting drift
in the metrics table is the first thing a sceptical reader notices.

*(The badge/panel disagreement on the dashboard — 70% against 69.8% — was the same class of
problem and is fixed; the README's `Rs`/`₹` split is not.)*

---

### What would actually address them

Not attempted here, written down so the shape is known:

1. A short **"What it does"** section above `Metrics` — three sentences and one sentence on
   why abstention is the product, so 62.91% is read as a choice rather than a shortfall.
2. A table of contents, and descriptive subtitles beside the essayistic headings.
3. `Running it` moved above the failure analysis, or a one-line pointer to it near the top.
4. One glossary paragraph covering sealed / held-out / coverage / calibration.

None of these changes a number. All of them change what a reader believes after ninety
seconds.
