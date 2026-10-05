# SPDX-License-Identifier: AGPL-3.0-only
"""Static judging page: three buttons, keys 1/2/3, then the next unlabeled row.

The page is one HTML file. Case text is embedded as JSON and written into the
document with ``textContent``, so a title cannot become markup. Saving is a
POST to ``/label`` on the origin that served the page (``python -m giye.audit
serve``). Opened as a file, the buttons report that the save failed.
"""

from __future__ import annotations

import json

_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Giye accuracy audit</title>
<style>
  body { font-family: "Iowan Old Style", Palatino, Georgia, serif; margin: 1.5rem auto; max-width: 46rem;
         line-height: 1.45; color: #1a1a1a; background: #f7f4ef; }
  h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 0.2rem; }
  #progress { color: #444; }
  #case { background: #fff; border: 1px solid #ddd; padding: 1rem 1.1rem; min-height: 12rem; }
  #case p { margin: 0.35rem 0; }
  #case h3 { font-size: 0.95rem; margin: 0.8rem 0 0.2rem; }
  pre { white-space: pre-wrap; font-family: ui-monospace, "Source Code Pro", monospace; font-size: 0.95rem;
        background: #f3f1ea; padding: 0.6rem 0.7rem; }
  textarea { width: 100%; min-height: 4.5rem; font: inherit; box-sizing: border-box; }
  .choices { display: flex; flex-wrap: wrap; gap: 0.6rem; margin: 0.8rem 0; }
  button.choice { font: inherit; font-size: 1.25rem; padding: 0.85rem 1.1rem; min-width: 11rem; cursor: pointer;
                  border: 2px solid #1a1a1a; background: #fff; }
  button.choice:focus { outline: 3px solid #1d4e89; }
  button.nav { font: inherit; margin-right: 0.4rem; }
  #status { min-height: 1.2rem; }
</style>
</head>
<body>
<h1>Giye accuracy audit</h1>
<p id="progress"></p>
<article id="case"></article>
<p><label for="note">note</label></p>
<textarea id="note"></textarea>
<div class="choices">
  <button type="button" class="choice" id="b1">1 correct</button>
  <button type="button" class="choice" id="b2">2 incorrect</button>
  <button type="button" class="choice" id="b3">3 cannot tell</button>
</div>
<p>
  <button type="button" class="nav" id="prev">previous</button>
  <button type="button" class="nav" id="next">next</button>
</p>
<p id="status"></p>
<script id="cases" type="application/json">__CASES__</script>
<script>
(function () {
  const cases = JSON.parse(document.getElementById("cases").textContent);
  const caseEl = document.getElementById("case");
  const noteEl = document.getElementById("note");
  const progressEl = document.getElementById("progress");
  const statusEl = document.getElementById("status");
  let index = cases.findIndex((row) => !row.label);
  if (index < 0) index = 0;

  function labeledCount() {
    return cases.filter((row) => row.label).length;
  }

  function addField(parent, label, value) {
    const p = document.createElement("p");
    const strong = document.createElement("strong");
    strong.textContent = label;
    p.appendChild(strong);
    p.appendChild(document.createTextNode(" " + (value || "")));
    parent.appendChild(p);
  }

  function addBlock(parent, label, value) {
    const h = document.createElement("h3");
    h.textContent = label;
    parent.appendChild(h);
    const pre = document.createElement("pre");
    pre.textContent = value || "";
    parent.appendChild(pre);
  }

  function show(i) {
    index = i;
    caseEl.replaceChildren();
    if (!cases.length) {
      addField(caseEl, "sheet", "no cases");
      progressEl.textContent = "0 / 0";
      return;
    }
    const row = cases[i];
    progressEl.textContent = (i + 1) + " / " + cases.length + " · labeled " + labeledCount();
    addField(caseEl, "stratum", row.stratum);
    addField(caseEl, "item", row.item_id);
    if (row.label) addField(caseEl, "saved label", row.label);
    if (row.kind === "cv") {
      addField(caseEl, "type", row.activity_type);
      addField(caseEl, "year", row.year);
      addField(caseEl, "title", row.title);
      addField(caseEl, "venue", row.venue);
      addField(caseEl, "role", row.role);
      addField(caseEl, "person", [row.name_ko, row.name_en, row.gy_id].filter(Boolean).join(" "));
      addField(caseEl, "source", row.source_url);
      addBlock(caseEl, "CV excerpt", row.excerpt);
    } else if (row.kind === "people") {
      addField(caseEl, "kept", [row.kept_name_ko, row.kept_name_en].filter(Boolean).join(" / "));
      addField(caseEl, "kept id", [row.kept_gy_id, row.kept_ledger_id].filter(Boolean).join(" "));
      addField(caseEl, "kept rosters", row.kept_rosters);
      addField(caseEl, "dropped", [row.dropped_name_ko, row.dropped_name_en].filter(Boolean).join(" / "));
      addField(caseEl, "dropped id", [row.dropped_gy_id, row.dropped_ledger_id].filter(Boolean).join(" "));
      addField(caseEl, "dropped rosters", row.dropped_rosters);
      addBlock(caseEl, "evidence", row.evidence);
    } else {
      addField(caseEl, "rule", row.rule);
      addField(caseEl, "kept spelling", (row.kept_spelling || "") + " (" + (row.kept_row_count || "") + ")");
      addBlock(caseEl, "kept examples", row.kept_examples);
      addField(caseEl, "joined spelling", (row.joined_spelling || "") + " (" + (row.joined_row_count || "") + ")");
      addBlock(caseEl, "joined examples", row.joined_examples);
    }
    noteEl.value = row.note || "";
    statusEl.textContent = "";
  }

  function advance() {
    for (let step = 1; step <= cases.length; step += 1) {
      const j = (index + step) % cases.length;
      if (!cases[j].label) {
        show(j);
        return;
      }
    }
    show(index);
    statusEl.textContent = "every row has a label";
  }

  async function choose(label) {
    if (!cases.length) return;
    const row = cases[index];
    const note = noteEl.value;
    statusEl.textContent = "saving";
    let response;
    try {
      response = await fetch("/label", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_id: row.item_id, label: label, note: note })
      });
    } catch (err) {
      statusEl.textContent = "save failed (open this page from giye.audit serve)";
      return;
    }
    if (!response.ok) {
      statusEl.textContent = "save failed (" + response.status + ")";
      return;
    }
    row.label = label;
    row.note = note;
    advance();
  }

  document.getElementById("b1").addEventListener("click", function () { choose("correct"); });
  document.getElementById("b2").addEventListener("click", function () { choose("incorrect"); });
  document.getElementById("b3").addEventListener("click", function () { choose("cannot tell"); });
  document.getElementById("prev").addEventListener("click", function () {
    if (cases.length) show((index - 1 + cases.length) % cases.length);
  });
  document.getElementById("next").addEventListener("click", function () {
    if (cases.length) show((index + 1) % cases.length);
  });
  document.addEventListener("keydown", function (event) {
    if (event.target === noteEl) return;
    if (event.key === "1") choose("correct");
    else if (event.key === "2") choose("incorrect");
    else if (event.key === "3") choose("cannot tell");
  });
  show(cases.length ? index : 0);
})();
</script>
</body>
</html>
"""


def render_page(rows: list[dict[str, str]]) -> str:
    """The judging page for these rows. ``<`` in the JSON is escaped so it cannot close the script."""
    payload = json.dumps(rows, ensure_ascii=False).replace("<", "\\u003c")
    return _PAGE.replace("__CASES__", payload)
