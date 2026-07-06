"""Local CRM prototype — reads/writes the Rusty Stakeholder spreadsheet via gws CLI.

Run:
    pip install flask
    python app.py
    # open http://127.0.0.1:5000
"""
from __future__ import annotations
from flask import Flask, render_template_string, request, redirect, url_for, abort

import sheets

app = Flask(__name__)


BASE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Stakeholder CRM — HodgeSpot</title>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background: #f6f7f9; color: #222; }
    header { background: #1a2332; color: #fff; padding: 14px 24px; display:flex; align-items:center; justify-content:space-between; }
    header h1 { margin: 0; font-size: 18px; font-weight: 600; }
    header .stats { font-size: 13px; color: #9fb; }
    header a { color: #9fc; text-decoration: none; margin-left: 16px; }
    main { max-width: 1200px; margin: 24px auto; padding: 0 24px; }
    .toolbar { display:flex; gap:12px; align-items:center; margin-bottom:16px; }
    .toolbar input[type=text] { flex:1; padding:10px 14px; border:1px solid #ccc; border-radius:6px; font-size:14px; }
    .pills { display:flex; gap:6px; flex-wrap:wrap; }
    .pill { padding:4px 12px; border-radius:999px; background:#e4e7eb; color:#333; font-size:12px; cursor:pointer; border:none; }
    .pill.active { background:#1a2332; color:#fff; }
    table { width:100%; border-collapse:collapse; background:#fff; border-radius:8px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,0.06); }
    th { background:#eef1f5; text-align:left; padding:10px 12px; font-size:12px; text-transform:uppercase; color:#555; letter-spacing:0.5px; }
    td { padding:10px 12px; border-top:1px solid #eef1f5; font-size:14px; vertical-align:top; }
    tr:hover td { background:#fafbfc; }
    .linked { color:#1a73e8; font-weight:500; }
    .linked:after { content:" \\1F517"; font-size:11px; }
    .unlinked { color:#444; }
    .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:500; }
    .badge-linked { background:#d4edda; color:#155724; }
    .badge-unlinked { background:#fff3cd; color:#856404; }
    .card { background:#fff; border-radius:8px; padding:24px; box-shadow:0 1px 3px rgba(0,0,0,0.06); }
    .field { margin-bottom:14px; }
    .field label { display:block; font-size:11px; color:#666; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px; }
    .field input, .field textarea { width:100%; padding:8px 10px; border:1px solid #ccc; border-radius:4px; font-size:14px; font-family:inherit; }
    .field textarea { min-height:60px; resize:vertical; }
    .btn { padding:8px 16px; border-radius:6px; border:0; background:#1a73e8; color:#fff; font-size:14px; cursor:pointer; font-weight:500; }
    .btn-secondary { background:#e4e7eb; color:#333; }
    .btn-ghost { background:transparent; color:#1a73e8; }
    .btn:hover { filter:brightness(0.93); }
    .row-action { font-size:12px; color:#1a73e8; text-decoration:none; }
    .suggestion { padding:12px; background:#eef6ff; border-left:3px solid #1a73e8; border-radius:4px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center; }
    .suggestion-meta { font-size:12px; color:#555; margin-top:4px; }
    .score { font-weight:600; color:#1a73e8; }
    .score.high { color:#1e8449; }
    .score.med { color:#b7791f; }
    .score.low { color:#999; }
    .empty { padding:40px; text-align:center; color:#888; }
    .split { display:grid; grid-template-columns: 2fr 1fr; gap:20px; }
  </style>
</head>
<body>
<header>
  <h1>Stakeholder CRM <span style="opacity:0.6;font-weight:400">/ Rusty Communitry</span></h1>
  <div>
    <span class="stats">{{ stats }}</span>
    <a href="{{ url_for('index') }}">All</a>
    <a href="{{ url_for('index') }}?filter=unlinked">Needs linking</a>
    <a href="#" hx-post="{{ url_for('refresh') }}" hx-swap="none">Refresh</a>
  </div>
</header>
<main>
{{ body|safe }}
</main>
</body>
</html>
"""


LIST_BODY = """
<div class="toolbar">
  <input type="text" name="q" placeholder="Search contacts, stakeholders, locations..."
         hx-get="{{ url_for('index') }}" hx-trigger="keyup changed delay:200ms"
         hx-target="#results" hx-select="#results" hx-swap="outerHTML"
         value="{{ q or '' }}">
  <div class="pills">
    <a class="pill {{ 'active' if not filt else '' }}" href="{{ url_for('index') }}">All ({{ total }})</a>
    <a class="pill {{ 'active' if filt=='linked' else '' }}" href="{{ url_for('index', filter='linked') }}">Linked ({{ linked }})</a>
    <a class="pill {{ 'active' if filt=='unlinked' else '' }}" href="{{ url_for('index', filter='unlinked') }}">Unlinked ({{ unlinked }})</a>
  </div>
</div>

<div id="results">
<table>
  <thead>
    <tr>
      <th style="width:40px">Row</th>
      <th>Contact</th>
      <th>Stakeholder</th>
      <th>Location</th>
      <th>Follow Up</th>
      <th style="width:100px">Status</th>
      <th style="width:100px"></th>
    </tr>
  </thead>
  <tbody>
  {% for c in contacts %}
    <tr>
      <td style="color:#999">{{ c.row }}</td>
      <td><a href="{{ url_for('detail', row=c.row) }}"
             class="{{ 'linked' if c.contact_link else 'unlinked' }}">{{ c.contact }}</a></td>
      <td>{{ c.stakeholder }}</td>
      <td>{{ c.location }}</td>
      <td style="font-size:12px;max-width:250px;overflow:hidden;text-overflow:ellipsis">{{ c.follow_up }}</td>
      <td>
        {% if c.contact_link %}
          <span class="badge badge-linked">linked</span>
        {% else %}
          <span class="badge badge-unlinked">unlinked</span>
        {% endif %}
      </td>
      <td>
        {% if not c.contact_link %}
          <a class="row-action" href="{{ url_for('detail', row=c.row) }}#suggest">suggest →</a>
        {% endif %}
      </td>
    </tr>
  {% endfor %}
  {% if not contacts %}
    <tr><td colspan="7" class="empty">No contacts match.</td></tr>
  {% endif %}
  </tbody>
</table>
</div>
"""


DETAIL_BODY = """
<div style="margin-bottom:16px">
  <a href="{{ url_for('index') }}" class="btn btn-ghost">&larr; All contacts</a>
</div>

<div class="split">
  <div class="card">
    <h2 style="margin-top:0">{{ c.contact or '(unnamed)' }}</h2>
    {% if c.contact_link %}
      <p><span class="badge badge-linked">linked</span>
        <a href="{{ c.contact_link }}" target="_blank">Open meeting note &rarr;</a></p>
    {% else %}
      <p><span class="badge badge-unlinked">unlinked</span>
        — no meeting-note hyperlink on this row yet.</p>
    {% endif %}

    <form method="post" action="{{ url_for('save', row=c.row) }}">
      <div class="field"><label>Contact name</label>
        <input type="text" name="contact" value="{{ c.contact }}"></div>
      <div class="field"><label>Stakeholder / Org</label>
        <input type="text" name="stakeholder" value="{{ c.stakeholder }}"></div>
      <div class="field"><label>Location</label>
        <input type="text" name="location" value="{{ c.location }}"></div>
      <div class="field"><label>Contact info</label>
        <input type="text" name="contact_info" value="{{ c.contact_info }}"></div>
      <div class="field"><label>Stakes</label>
        <textarea name="stakes">{{ c.stakes }}</textarea></div>
      <div class="field"><label>Actions implemented</label>
        <textarea name="actions_implemented">{{ c.actions_implemented }}</textarea></div>
      <div class="field"><label>Follow up</label>
        <textarea name="follow_up">{{ c.follow_up }}</textarea></div>
      <div class="field"><label>Notes</label>
        <textarea name="notes">{{ c.notes }}</textarea></div>
      <button class="btn" type="submit">Save</button>
      <a class="btn btn-secondary" href="{{ url_for('detail', row=c.row) }}">Cancel</a>
    </form>
  </div>

  <div class="card" id="suggest">
    <h3 style="margin-top:0">Meeting-note suggestions</h3>
    <p style="font-size:12px;color:#666;margin-top:-8px">
      Fuzzy-matched from the Drive folder against this contact's name.
    </p>
    {% if suggestions %}
      {% for s in suggestions %}
        <div class="suggestion">
          <div>
            <div><strong>{{ s.name }}</strong></div>
            <div class="suggestion-meta">
              <span class="score {{ 'high' if s.score>=0.85 else ('med' if s.score>=0.55 else 'low') }}">
                {{ s.score }}
              </span>
              &middot; <a href="{{ s.url }}" target="_blank">open doc</a>
            </div>
          </div>
          {% if not c.contact_link %}
            <form method="post" action="{{ url_for('link_contact', row=c.row) }}" style="margin:0">
              <input type="hidden" name="doc_url" value="{{ s.url }}">
              <input type="hidden" name="name" value="{{ c.contact }}">
              <button class="btn" type="submit"
                onclick="return confirm('Link {{ c.contact }} to {{ s.name|replace(chr(39),'') }}?')">
                Apply
              </button>
            </form>
          {% endif %}
        </div>
      {% endfor %}
    {% else %}
      <p class="empty" style="padding:20px 0">No plausible matches in the Drive folder.</p>
    {% endif %}
  </div>
</div>
"""


# ---------------- routes ----------------

@app.route("/")
def index():
    q = (request.args.get("q") or "").strip().lower()
    filt = request.args.get("filter") or ""
    contacts = sheets.load_contacts()
    total = len(contacts)
    linked = sum(1 for c in contacts if c["contact_link"])
    unlinked = total - linked

    if filt == "linked":
        contacts = [c for c in contacts if c["contact_link"]]
    elif filt == "unlinked":
        contacts = [c for c in contacts if not c["contact_link"]]
    if q:
        contacts = [
            c for c in contacts
            if q in (c["contact"] or "").lower()
            or q in (c["stakeholder"] or "").lower()
            or q in (c["location"] or "").lower()
            or q in (c["follow_up"] or "").lower()
        ]

    body = render_template_string(
        LIST_BODY, contacts=contacts, q=q, filt=filt,
        total=total, linked=linked, unlinked=unlinked,
    )
    stats = f"{total} total · {linked} linked · {unlinked} unlinked"
    return render_template_string(BASE, body=body, stats=stats)


@app.route("/c/<int:row>")
def detail(row: int):
    contacts = sheets.load_contacts()
    c = next((x for x in contacts if x["row"] == row), None)
    if not c:
        abort(404)
    suggestions = [] if c["contact_link"] else sheets.suggest_links(c["contact"], top=5)
    body = render_template_string(DETAIL_BODY, c=c, suggestions=suggestions, chr=chr)
    return render_template_string(BASE, body=body, stats=f"row {row}")


@app.route("/c/<int:row>/save", methods=["POST"])
def save(row: int):
    for field, col_idx in sheets.EDITABLE_COLS.items():
        if field == "contact":
            # Leave contact name alone here — don't clobber existing hyperlinks.
            # Use /c/<row>/rename for explicit contact renames.
            continue
        value = request.form.get(field, "")
        sheets.update_cell_string(row, col_idx, value)
    return redirect(url_for("detail", row=row))


@app.route("/c/<int:row>/link", methods=["POST"])
def link_contact(row: int):
    doc_url = request.form["doc_url"]
    name = request.form["name"]
    sheets.apply_hyperlink(row, name, doc_url)
    return redirect(url_for("detail", row=row))


@app.route("/refresh", methods=["POST"])
def refresh():
    sheets.invalidate_cache()
    return "", 204


if __name__ == "__main__":
    print("Starting Stakeholder CRM on http://127.0.0.1:5000")
    print("(first request may take 3-5 seconds while it fetches from the sheet)")
    app.run(host="127.0.0.1", port=5000, debug=True)
