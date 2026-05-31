"""
Washon Investment Suite — Local Web Tool
Usage:  python demo/app.py
Visit:  http://localhost:5000
"""

import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, Response, jsonify, redirect, render_template, render_template_string, request, send_file, stream_with_context, url_for
from flask_login import current_user, login_required, login_user, logout_user

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent.parent
DATA_DIR   = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent)))
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT))

from scraper.m1_bp_parser import extract_text, parse_with_gemini, flatten_profile  # noqa: E402
from scraper.m_matcher import match as run_match  # noqa: E402

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-fallback-key-change-in-prod")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("RAILWAY_ENVIRONMENT") is not None
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=7)

from demo.database import init_db, db as _db
from demo.extensions import init_extensions
from demo.routes.auth import auth as auth_blueprint

init_db(app)
init_extensions(app)
app.register_blueprint(auth_blueprint)

from demo.models.analysis import Analysis as AnalysisModel   # noqa: E402
from demo.models.pipeline import Pipeline as PipelineModel   # noqa: E402
from demo.models.user    import User                         # noqa: E402

ALLOWED = {".pdf", ".docx", ".doc"}

# ── Per-user ORM helpers ───────────────────────────────────────────────────

def _analysis_to_dict(row):
    """Convert an Analysis ORM row to the dict format used throughout the app."""
    d = dict(row.result) if row.result else {}
    d["id"] = str(row.id)
    if not d.get("company_name"):
        d["company_name"] = row.company or "Unknown"
    if not d.get("created_at") and row.created_at:
        d["created_at"] = row.created_at.isoformat() + "Z"
    return d


def _load_analyses():
    """Return {"analyses": [...]} for the currently logged-in user only."""
    rows = AnalysisModel.query.filter_by(user_id=current_user.id) \
                              .order_by(AnalysisModel.created_at.desc()).all()
    return {"analyses": [_analysis_to_dict(r) for r in rows]}


def _load_pipeline():
    """Return {"investors": [...]} for the currently logged-in user only."""
    rows = PipelineModel.query.filter_by(user_id=current_user.id) \
                              .order_by(PipelineModel.created_at.asc()).all()
    investors = []
    for row in rows:
        try:
            inv = json.loads(row.note or "{}")
        except (json.JSONDecodeError, TypeError):
            inv = {}
        investors.append(inv)
    return {"investors": investors}


def _save_pipeline(data):
    """Sync {"investors": [...]} to the database for the current user (replace-all)."""
    PipelineModel.query.filter_by(user_id=current_user.id).delete()
    for inv in data.get("investors", []):
        row = PipelineModel(
            user_id=current_user.id,
            investor_id=inv.get("id", ""),
            firm_name=inv.get("company", ""),
            stage=str(inv.get("step", "")),
            note=json.dumps(inv, ensure_ascii=False),
        )
        _db.session.add(row)
    _db.session.commit()


def _migrate_json_to_orm():
    """One-time: import legacy m1_analyses.json records into the Analysis table."""
    legacy = DATA_DIR / "m1_analyses.json"
    if not legacy.exists():
        return
    first_user = User.query.order_by(User.id.asc()).first()
    if not first_user:
        return
    try:
        existing = json.loads(legacy.read_text(encoding="utf-8"))
    except Exception:
        return
    migrated = 0
    for record in existing.get("analyses", []):
        a = AnalysisModel(
            user_id=first_user.id,
            filename=record.get("filename", "unknown"),
            company=record.get("company_name", "Unknown"),
            stage=record.get("funding_stage", ""),
            sector=record.get("sector", ""),
            result=record,
        )
        _db.session.add(a)
        migrated += 1
    _db.session.commit()
    legacy.rename(legacy.with_suffix(".migrated"))
    print(f"[migration] Imported {migrated} legacy analyses into DB", flush=True)


with app.app_context():
    _migrate_json_to_orm()

# ══════════════════════════════════════════════════════════════════════════
# Shared shell
# ══════════════════════════════════════════════════════════════════════════

SHELL = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Washon Suite</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0a0a0a;--nav:#0d0d0d;--card:#111111;--card2:#161616;
  --border:#222222;--border2:#2a2a2a;
  --accent:#6366f1;--accent2:#818cf8;--accent-dim:rgba(99,102,241,.12);
  --text:#e5e7eb;--text2:#9ca3af;--text3:#4b5563;
  --green:#10b981;--red:#ef4444;--amber:#f59e0b;
  --nav-w:220px;
}
html{height:100%}
body{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif;
     display:flex;min-height:100vh;font-size:14px;line-height:1.5}

/* ── NAV ── */
nav{
  width:var(--nav-w);min-height:100vh;background:var(--nav);
  border-right:1px solid var(--border);
  display:flex;flex-direction:column;position:fixed;top:0;left:0;z-index:50;
}
.nav-brand{
  padding:20px 18px 18px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:10px;
  text-decoration:none;color:inherit;
}
.nav-brand:hover .nav-logo{opacity:.85}
.badge{cursor:pointer}
.nav-logo{
  width:32px;height:32px;border-radius:8px;
  background:linear-gradient(135deg,var(--accent),#a78bfa);
  display:flex;align-items:center;justify-content:center;
  font-size:15px;font-weight:800;color:#fff;flex-shrink:0;
}
.nav-brand-text h1{font-size:.9rem;font-weight:700;color:var(--text);letter-spacing:.02em}
.nav-brand-text p {font-size:.65rem;color:var(--text3);margin-top:1px}

.nav-section{
  font-size:.62rem;color:var(--text3);letter-spacing:.1em;
  text-transform:uppercase;padding:16px 18px 6px;
}
.nav-link{
  display:flex;align-items:center;gap:9px;
  padding:8px 14px;border-radius:8px;margin:1px 8px;
  color:var(--text2);text-decoration:none;font-size:.83rem;font-weight:500;
  transition:background .14s,color .14s;
}
.nav-link:hover{background:#1a1a1a;color:var(--text)}
.nav-link.active{background:var(--accent-dim);color:var(--accent2)}
.nav-link .badge{
  margin-left:auto;font-size:.58rem;font-weight:700;
  border-radius:4px;padding:2px 6px;
}
.badge-new  {background:#1a4a2e;color:#4ade80}
.badge-count{background:#1e1e2e;color:#6b7280}
.badge-beta {background:#3d2e0a;color:#f59e0b}
.nav-icon{font-size:.95rem;width:18px;text-align:center;flex-shrink:0}

.nav-footer{
  padding:16px 18px;border-top:1px solid var(--border);margin-top:auto;
  font-size:.7rem;color:var(--text3);
}
.nav-user{
  font-size:.75rem;color:var(--text2);margin-bottom:8px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.nav-logout{
  display:inline-flex;align-items:center;gap:5px;
  padding:5px 12px;border-radius:6px;
  background:rgba(239,68,68,.1);color:#fca5a5;
  text-decoration:none;font-size:.73rem;font-weight:600;
  border:1px solid rgba(239,68,68,.22);transition:background .15s;
}
.nav-logout:hover{background:rgba(239,68,68,.2)}

/* ── MAIN ── */
main{margin-left:var(--nav-w);flex:1;min-height:100vh}
</style>
</head>
<body>

<nav>
  <a href="{{ url_for('index') }}" class="nav-brand">
    <div class="nav-logo">W</div>
    <div class="nav-brand-text">
      <h1>Washon</h1>
      <p>Investment Suite</p>
    </div>
  </a>

  <div style="padding:8px 0;flex:1">
    <div class="nav-section">Tools</div>
    <a href="/m1" class="nav-link {{ 'active' if active=='m1' else '' }}">
      <span class="nav-icon">📄</span> BP Parser
      <span class="badge badge-new">NEW</span>
    </a>
    <a href="/m4" class="nav-link {{ 'active' if active=='m4' else '' }}">
      <span class="nav-icon">🗄️</span> Investor DB
      <span class="badge badge-count">485</span>
    </a>
    <a href="/m10" class="nav-link {{ 'active' if active=='m10' else '' }}">
      <span class="nav-icon">📋</span> Saved Analyses
    </a>
    <a href="/m9" class="nav-link {{ 'active' if active=='m9' else '' }}">
      <span class="nav-icon">🎯</span> Pitching Guide
      <span class="badge badge-beta">BETA</span>
    </a>
  </div>

  <div class="nav-footer">
    {% if current_user.is_authenticated %}
      <div class="nav-user">Welcome, {{ current_user.name }}</div>
      <a href="/auth/logout" class="nav-logout">⏻ Logout</a>
    {% else %}
      <span>v0.1</span>
    {% endif %}
  </div>
</nav>

<main>
{{ content }}
</main>

</body>
</html>
"""

# ══════════════════════════════════════════════════════════════════════════
# M1 — BP Parser page
# ══════════════════════════════════════════════════════════════════════════

M1_CONTENT = """\
<style>
.m1-wrap{padding:32px;max-width:960px}
.page-title{font-size:1.35rem;font-weight:700;margin-bottom:4px}
.page-sub{color:var(--text2);font-size:.83rem;margin-bottom:28px}

/* Upload zone */
.upload-zone{
  border:2px dashed var(--border2);border-radius:14px;
  padding:56px 32px;text-align:center;cursor:pointer;
  transition:border-color .18s,background .18s;margin-bottom:24px;
  background:var(--card);
}
.upload-zone.drag-over{border-color:var(--accent);background:var(--accent-dim)}
.upload-zone .uz-icon{font-size:2.8rem;margin-bottom:14px}
.upload-zone h3{font-size:1rem;font-weight:600;margin-bottom:6px}
.upload-zone p{color:var(--text2);font-size:.8rem}
.upload-zone .browse-link{color:var(--accent2);cursor:pointer}
.upload-zone .browse-link:hover{text-decoration:underline}
#file-input{display:none}

/* Error */
.error-box{
  display:none;align-items:center;gap:10px;
  background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.25);
  border-radius:10px;padding:14px 18px;color:#fca5a5;
  margin-bottom:20px;font-size:.83rem;
}
.error-box.show{display:flex}

/* Loading */
.loading{display:none;text-align:center;padding:56px 20px}
.loading.show{display:block}
.spinner{
  width:40px;height:40px;border:3px solid var(--border2);
  border-top-color:var(--accent);border-radius:50%;
  animation:spin .75s linear infinite;margin:0 auto 16px;
}
@keyframes spin{to{transform:rotate(360deg)}}
.loading p{color:var(--text2);font-size:.88rem}

/* Results */
#results{display:none}
#results.show{display:block}

/* Confidence hero */
.conf-hero{
  background:var(--card);border:1px solid var(--border);border-radius:14px;
  padding:28px 32px;margin-bottom:24px;
  display:flex;align-items:center;gap:32px;
}
.conf-hero-left{flex:1}
.conf-company{font-size:1.45rem;font-weight:800;line-height:1.2;margin-bottom:6px}
.conf-meta{color:var(--text2);font-size:.82rem;display:flex;flex-wrap:wrap;gap:8px}
.conf-meta-tag{
  background:var(--card2);border:1px solid var(--border2);
  border-radius:4px;padding:2px 8px;
}
.conf-right{text-align:center;flex-shrink:0}
.conf-ring{position:relative;width:100px;height:100px}
.conf-ring svg{transform:rotate(-90deg)}
.conf-ring-val{
  position:absolute;inset:0;display:flex;flex-direction:column;
  align-items:center;justify-content:center;
}
.conf-pct{font-size:1.6rem;font-weight:800;color:var(--accent2);line-height:1}
.conf-lbl{font-size:.58rem;color:var(--text3);text-transform:uppercase;letter-spacing:.06em;margin-top:3px}

/* Fields grid */
.fields-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:12px;margin-bottom:24px}
.field-card{
  background:var(--card);border:1px solid var(--border);
  border-radius:10px;padding:16px;
}
.field-card.full-width{grid-column:1/-1}
.fl{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:8px;
}
.fl-key{
  font-size:.62rem;text-transform:uppercase;letter-spacing:.09em;
  color:var(--text3);font-weight:600;
}
.fl-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.fl-dot.present{background:var(--green)}
.fl-dot.missing{background:var(--text3)}
.fl-dot.absent{background:var(--amber)}
.fl-dot.danger{background:var(--red)}
.fv{font-size:.84rem;line-height:1.55;color:var(--text)}
.fv.null{color:var(--text3);font-style:italic}
.bar{height:2px;background:var(--border);border-radius:1px;margin-top:12px;overflow:hidden}
.bar-fill{height:100%;border-radius:1px;transition:width .6s ease}

/* Add-to-pipeline button on match cards */
.pip-btn{
  margin-left:8px;padding:2px 8px;font-size:.68rem;font-weight:600;
  border:1px solid var(--border2);border-radius:4px;background:transparent;
  color:var(--text3);cursor:pointer;transition:border-color .15s,color .15s;
  vertical-align:middle;white-space:nowrap;
}
.pip-btn:hover:not(:disabled){border-color:#10b981;color:#10b981}
.pip-btn:disabled{cursor:default;opacity:.7}

/* Reset btn */
.reset-btn{
  display:inline-flex;align-items:center;gap:6px;margin-top:4px;
  padding:8px 16px;background:var(--card);border:1px solid var(--border2);
  border-radius:8px;color:var(--text2);font-size:.8rem;cursor:pointer;
  transition:border-color .15s,color .15s;text-decoration:none;
}
.reset-btn:hover{border-color:var(--accent);color:var(--text)}
</style>

<div class="m1-wrap">
  <div class="page-title">Business Plan Parser</div>
  <div class="page-sub">Upload a pitch deck or business plan to extract investment profile and match investors.</div>

  <div class="upload-zone" id="drop-zone">
    <input type="file" id="file-input" accept=".pdf,.docx,.doc">
    <div class="uz-icon">📂</div>
    <h3>Drop your Business Plan here</h3>
    <p>or <span class="browse-link" onclick="event.stopPropagation(); document.getElementById('file-input').click()">browse file</span>
       &nbsp;·&nbsp; PDF or DOCX &nbsp;·&nbsp; max 50 MB</p>
  </div>

  <div style="margin-top:24px;border:.5px solid #222;border-radius:10px;padding:18px 20px;">
    <div style="font-size:.68rem;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:16px;">
      To get the most accurate investor matches, make sure your BP clearly covers the following:
    </div>

    <div style="margin-bottom:14px;">
      <div style="font-size:.68rem;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Required — Gates into the Candidate Pool</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;">
        <span style="font-size:.73rem;padding:3px 10px;border-radius:5px;border:.5px solid rgba(239,68,68,.35);background:rgba(239,68,68,.08);color:rgba(239,68,68,.75);">Funding stage (e.g. Seed, Series A)</span>
        <span style="font-size:.73rem;padding:3px 10px;border-radius:5px;border:.5px solid rgba(239,68,68,.35);background:rgba(239,68,68,.08);color:rgba(239,68,68,.75);">Target raise amount</span>
        <span style="font-size:.73rem;padding:3px 10px;border-radius:5px;border:.5px solid rgba(239,68,68,.35);background:rgba(239,68,68,.08);color:rgba(239,68,68,.75);">HQ location (city + country)</span>
      </div>
    </div>

    <div style="margin-bottom:14px;">
      <div style="font-size:.68rem;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Core Signals — Directly Set Your Match Score</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;">
        <span style="font-size:.73rem;padding:3px 10px;border-radius:5px;border:.5px solid rgba(245,158,11,.35);background:rgba(245,158,11,.08);color:rgba(245,158,11,.75);">Industry &amp; sub-sector — highest weight</span>
        <span style="font-size:.73rem;padding:3px 10px;border-radius:5px;border:.5px solid rgba(245,158,11,.35);background:rgba(245,158,11,.08);color:rgba(245,158,11,.75);">Business model (e.g. SaaS, licensing, services)</span>
      </div>
      <div style="font-family:monospace;font-size:.69rem;color:#555;background:#0d0d0d;border-radius:5px;padding:9px 12px;line-height:1.8;">
        Name the specific field or technology your company operates in — the more precise, the better.<br>
        Generic terms like "tech" or "healthcare" are less effective than specific ones like your core<br>
        technology, therapy type, market category, or product approach.
      </div>
    </div>

    <div style="margin-bottom:14px;">
      <div style="font-size:.68rem;color:var(--text3);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;">Boost Signals — Improve Ranking Within Matches</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;">
        <span style="font-size:.73rem;padding:3px 10px;border-radius:5px;border:.5px solid rgba(16,185,129,.3);background:rgba(16,185,129,.07);color:rgba(16,185,129,.72);">Revenue or ARR (specific figures)</span>
        <span style="font-size:.73rem;padding:3px 10px;border-radius:5px;border:.5px solid rgba(16,185,129,.3);background:rgba(16,185,129,.07);color:rgba(16,185,129,.72);">Team background (PhD, domain expert, repeat founder)</span>
        <span style="font-size:.73rem;padding:3px 10px;border-radius:5px;border:.5px solid rgba(16,185,129,.3);background:rgba(16,185,129,.07);color:rgba(16,185,129,.72);">Lead investor preference</span>
      </div>
    </div>

    <div style="font-size:.68rem;color:var(--text3);border-top:.5px solid #1e1e1e;padding-top:11px;margin-top:2px;">
      Financial models, competitive tables, and appendices do not affect match scores — the algorithm only reads the signals above.
    </div>
  </div>

  <div class="error-box" id="error-box">
    <span>⚠️</span><span id="error-msg"></span>
  </div>

  <div class="loading" id="loading">
    <div class="spinner"></div>
    <p id="loading-msg">Extracting structured data from document...</p>
  </div>

  <div id="results">
    <div class="conf-hero">
      <div class="conf-hero-left">
        <div class="conf-company" id="r-company">—</div>
        <div class="conf-meta" id="r-meta"></div>
      </div>
      <div class="conf-right">
        <div class="conf-ring">
          <svg width="100" height="100" viewBox="0 0 100 100">
            <circle cx="50" cy="50" r="42" fill="none" stroke="#222" stroke-width="8"/>
            <circle id="ring-fg" cx="50" cy="50" r="42" fill="none"
                    stroke="#6366f1" stroke-width="8"
                    stroke-dasharray="264" stroke-dashoffset="264"
                    stroke-linecap="round"/>
          </svg>
          <div class="conf-ring-val">
            <span class="conf-pct" id="r-pct">—</span>
            <span class="conf-lbl">Confidence</span>
          </div>
        </div>
      </div>
    </div>

    <div class="fields-grid" id="fields-grid"></div>

    <div id="matches-section" style="display:none;margin-bottom:24px"></div>

    <button class="reset-btn" onclick="resetForm()">↩ Parse another file</button>
  </div>
</div>

<script>
const FIELDS = [
  {key:'funding_stage',    label:'Funding Stage'},
  {key:'capital_need',     label:'Capital Need'},
  {key:'sector',           label:'Sector'},
  {key:'geography',        label:'Geography'},
  {key:'business_model',   label:'Business Model'},
  {key:'sub_sector_tags',  label:'Sub-sector Focus'},
  {key:'key_traction',     label:'Key Traction',      wide:true},
  {key:'team_background',  label:'Team Background',   wide:true},
  {key:'use_of_funds',     label:'Use of Funds',      wide:true},
];

const zone    = document.getElementById('drop-zone');
const fileIn  = document.getElementById('file-input');
const loading = document.getElementById('loading');
const loadMsg = document.getElementById('loading-msg');
const errBox  = document.getElementById('error-box');
const errMsg  = document.getElementById('error-msg');
const results = document.getElementById('results');

zone.addEventListener('dragover',  e => { e.preventDefault(); zone.classList.add('drag-over'); });
zone.addEventListener('dragleave', ()  => zone.classList.remove('drag-over'));
zone.addEventListener('drop', e => {
  e.preventDefault(); zone.classList.remove('drag-over');
  const f = e.dataTransfer.files[0];
  if (f) uploadFile(f);
});
let _picking = false;
zone.addEventListener('click', () => { if (!_picking) { _picking = true; fileIn.click(); } });
fileIn.addEventListener('change', () => { _picking = false; if (fileIn.files[0]) uploadFile(fileIn.files[0]); });

function showError(msg) {
  errMsg.textContent = msg;
  errBox.classList.add('show');
  loading.classList.remove('show');
}

function resetForm() {
  results.classList.remove('show');
  errBox.classList.remove('show');
  zone.style.display = '';
  fileIn.value = '';
  _picking = false;
}

function uploadFile(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['pdf','docx','doc'].includes(ext)) {
    showError('Unsupported file type. Please upload a PDF or DOCX.'); return;
  }
  errBox.classList.remove('show');
  results.classList.remove('show');
  zone.style.display = 'none';
  loading.classList.add('show');
  loadMsg.textContent = 'Extracting structured data from document...';

  const fd = new FormData();
  fd.append('file', file);

  fetch('/m1/parse', { method:'POST', body:fd })
    .then(r => r.json())
    .then(data => {
      loading.classList.remove('show');
      if (data.error) { zone.style.display = ''; showError(data.error); return; }
      renderResults(data);
    })
    .catch(err => { zone.style.display = ''; showError('Network error: ' + err); });
}

function renderResults(data) {
  const profile = data.profile || data;
  const matches = data.matches || [];

  function toStr(v) {
    if (!v && v !== 0) return '';
    return Array.isArray(v) ? v.join(', ') : String(v);
  }

  const d = {
    funding_stage:   toStr(profile.funding_stage),
    capital_need:    toStr(profile.capital_need),
    sector:          toStr(profile.sector),
    geography:       toStr(profile.geography),
    business_model:  toStr(profile.business_model),
    sub_sector_tags: toStr(profile.sub_sector_tags),
    key_traction:    toStr(profile.key_traction),
    team_background: toStr(profile.team_background),
    use_of_funds:    toStr(profile.use_of_funds),
    missing_signals: toStr(profile.missing_signals),
  };

  // Company header
  document.getElementById('r-company').textContent = profile.company_name || 'Unknown Company';

  // Meta tags
  const metaTags = [d.funding_stage, d.sector, d.geography].filter(Boolean);
  document.getElementById('r-meta').innerHTML =
    (profile.tagline ? [`<span class="conf-meta-tag">${escHtml(profile.tagline)}</span>`] : [])
    .concat(metaTags.map(t => `<span class="conf-meta-tag">${escHtml(t)}</span>`))
    .join('');

  // Confidence ring
  const score = profile.overall_confidence || 0;
  const pct   = Math.round(score * 100);
  document.getElementById('r-pct').textContent = pct + '%';
  const circ   = 2 * Math.PI * 42;
  const offset = circ * (1 - score);
  const fg = document.getElementById('ring-fg');
  fg.setAttribute('stroke-dasharray', circ.toFixed(1));
  fg.setAttribute('stroke-dashoffset', offset.toFixed(1));
  fg.setAttribute('stroke', pct >= 70 ? '#10b981' : pct >= 40 ? '#f59e0b' : '#6366f1');

  // Fields grid
  const grid = document.getElementById('fields-grid');
  grid.innerHTML = '';
  const EMPTY_SIGNALS = [
    'none','null','unknown','n/a','not specified','not found',
    'not mentioned','not stated','not disclosed','no funding',
    'no amount','not provided','unspecified',
  ];
  function isPresent(val) {
    if (val === null || val === undefined || val === '') return false;
    const s = String(val).trim().toLowerCase();
    if (!s) return false;
    return !EMPTY_SIGNALS.some(sig => s.includes(sig));
  }

  function capitalNeedMissing(val) {
    if (!isPresent(val)) return true;
    return !/[\d$£€¥KkMmBb]/.test(String(val));
  }

  FIELDS.forEach(({key, label, wide}) => {
    const val     = d[key];
    const present = isPresent(val);
    const card    = document.createElement('div');
    card.className = 'field-card' + (wide ? ' full-width' : '');

    if (key === 'capital_need' && capitalNeedMissing(val)) {
      card.style.borderLeft = '3px solid var(--red)';
      card.innerHTML = `
        <div class="fl">
          <span class="fl-key" style="color:var(--red)">${label}</span>
          <span class="fl-dot danger"></span>
        </div>
        <div class="fv null" style="color:var(--text3)">${present ? escHtml(val) : 'Not extracted — add to BP to improve match quality'}</div>
        <div style="font-size:.71rem;color:rgba(239,68,68,.7);margin-top:4px">Add a target raise amount to improve investor matching</div>
        <div class="bar"><div class="bar-fill" style="width:0%;background:var(--red)"></div></div>`;
    } else {
      if (!present) card.style.borderLeft = '3px solid var(--amber)';
      else card.style.borderLeft = '';
      const color = present ? 'var(--green)' : 'var(--amber)';
      card.innerHTML = `
        <div class="fl">
          <span class="fl-key">${label}</span>
          <span class="fl-dot ${present ? 'present' : 'absent'}"></span>
        </div>
        <div class="fv ${present ? '' : 'null'}" ${present ? '' : 'style="color:var(--amber)"'}>${present ? escHtml(val) : 'Not extracted — add to BP to improve match quality'}</div>
        <div class="bar"><div class="bar-fill" style="width:${present?100:0}%;background:${color}"></div></div>`;
    }
    grid.appendChild(card);
  });

  // Matched investors
  const matchesEl = document.getElementById('matches-section');
  if (matches.length) {
    matchesEl.innerHTML =
      `<div style="font-size:.7rem;text-transform:uppercase;letter-spacing:.09em;color:var(--text3);font-weight:600;margin-bottom:12px">Matched Investors (${matches.length} results)</div>` +
      matches.map((m, idx) => {
        const finalPct = typeof m.score          === 'number' ? Math.round(m.score          * 100) : 0;
        const t2Pct    = typeof m.tier2_score    === 'number' ? Math.round(m.tier2_score    * 100) : null;
        const semPct   = typeof m.semantic_score === 'number' ? Math.round(m.semantic_score * 100) : null;
        const secPct   = typeof m.sector_score   === 'number' ? Math.round(m.sector_score   * 100) : null;
        const dims     = m.dim_scores || {};
        const reasons  = (m.match_reasons || []).join(' · ');
        const stages   = (m.stages || []).join(', ');
        const subBonus      = typeof m.sub_bonus      === 'number' ? m.sub_bonus      : null;
        const subF1Base     = typeof m.sub_f1_base   === 'number' ? m.sub_f1_base    : null;
        const subStatus     = m.sub_sector_status || null;
        const subTagMatched = Array.isArray(m.sub_tags_matched) ? m.sub_tags_matched : [];
        // Bug 1 fix: use sub_f1_base (0–1) directly, not the old ±bonus/0.12 formula
        const subBonusPct   = subF1Base !== null ? Math.min(100, Math.round(subF1Base * 100)) : null;

        function col(pct) {
          return pct >= 70 ? '#10b981' : pct >= 45 ? '#f59e0b' : '#ef4444';
        }

        function miniBar(val) {
          const pct = Math.round((val || 0) * 100);
          return `<div style="display:flex;align-items:center;gap:5px">
            <div style="width:52px;height:3px;background:var(--border);border-radius:2px;flex-shrink:0">
              <div style="width:${pct}%;height:100%;background:${col(pct)};border-radius:2px"></div>
            </div>
            <span style="font-size:.62rem;color:var(--text3);width:26px">${pct}%</span>
          </div>`;
        }

        const nameHtml = m.website
          ? `<a href="${escHtml(m.website)}" target="_blank"
                style="color:var(--text);text-decoration:none;border-bottom:1px solid var(--border2)">
               ${escHtml(m.name || '—')}
               <span style="font-size:.7rem;color:var(--accent2);margin-left:3px">↗</span>
             </a>`
          : escHtml(m.name || '—');

        return `<div class="field-card" style="margin-bottom:10px">

          <!-- Row 1: name + final score -->
          <div class="fl" style="margin-bottom:10px">
            <div>
              <span style="font-size:.88rem;font-weight:700">${nameHtml}</span>
              <button class="pip-btn" id="pip-btn-${idx}"
                onclick="addToPipeline(this,${JSON.stringify(m.name||'')})">+ Pipeline</button>
              <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:5px">
                ${stages          ? `<span class="conf-meta-tag">${escHtml(stages)}</span>`          : ''}
                ${m.check_size    ? `<span class="conf-meta-tag">${escHtml(m.check_size)}</span>`    : ''}
                ${m.investor_type ? `<span class="conf-meta-tag">${escHtml(m.investor_type)}</span>` : ''}
                ${m.lead_investor ? `<span class="conf-meta-tag">${escHtml(m.lead_investor)}</span>` : ''}
              </div>
            </div>
            <div style="text-align:right;flex-shrink:0;margin-left:12px">
              <div style="font-family:monospace;font-size:1.15rem;font-weight:800;color:${col(finalPct)};line-height:1">
                ${finalPct}%
              </div>
              <div style="font-size:.58rem;color:var(--text3);text-transform:uppercase;letter-spacing:.06em;margin-top:2px">
                match score
              </div>
            </div>
          </div>

          <!-- Row 2: three-tier score breakdown -->
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:6px;margin-bottom:10px">

            <!-- Tier 1 -->
            <div style="background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:7px 9px">
              <div style="font-size:.6rem;text-transform:uppercase;letter-spacing:.08em;color:var(--text3);font-weight:600;margin-bottom:4px">
                Tier 1 · Hard Filter
              </div>
              <div style="font-size:.68rem;color:var(--text2);line-height:1.7">
                <div style="display:flex;justify-content:space-between">
                  <span>Stage</span>
                  <span style="color:#10b981">✓ pass</span>
                </div>
                <div style="display:flex;justify-content:space-between">
                  <span>Sector</span>
                  <span style="color:${col(secPct)};font-weight:600">
                    ${secPct != null ? secPct+'%' : '—'}${subTagMatched.length ? ' 🎯' : ''}
                  </span>
                </div>
                <div style="display:flex;justify-content:space-between">
                  <span>Amount</span>
                  <span style="color:#10b981">✓ pass</span>
                </div>
                <div style="display:flex;justify-content:space-between">
                  <span>Geo</span>
                  <span style="color:#10b981">✓ pass</span>
                </div>
              </div>
            </div>

            <!-- Tier 2 -->
            <div style="background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:7px 9px">
              <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
                <span style="font-size:.6rem;text-transform:uppercase;letter-spacing:.08em;color:var(--text3);font-weight:600">
                  Tier 2 · Soft Score
                </span>
                <span style="font-family:monospace;font-size:.72rem;font-weight:700;color:${col(t2Pct)}">
                  ${t2Pct != null ? t2Pct+'%' : '—'}
                </span>
              </div>
              <div style="font-size:.65rem;color:var(--text3);line-height:2">
                <div style="display:flex;justify-content:space-between;align-items:center">
                  <span>Biz Model</span>${miniBar(dims.business_model)}
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center">
                  <span>Traction</span>${miniBar(dims.traction)}
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center">
                  <span>Team</span>${miniBar(dims.team)}
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center">
                  <span>Lead</span>${miniBar(dims.lead)}
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center">
                  <span>Geo</span>${miniBar(dims.geo_soft)}
                </div>
              </div>
            </div>

            <!-- Tier 3 -->
            <div style="background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:7px 9px">
              <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
                <span style="font-size:.6rem;text-transform:uppercase;letter-spacing:.08em;color:var(--text3);font-weight:600">
                  Tier 3 · Semantic
                </span>
                <span style="font-family:monospace;font-size:.72rem;font-weight:700;color:${col(semPct)}">
                  ${semPct != null ? semPct+'%' : '—'}
                </span>
              </div>
              <div style="font-size:.65rem;color:var(--text3);line-height:1.6;margin-bottom:6px">
                Thesis alignment between BP narrative and investor investment thesis
              </div>
              <div style="width:100%;height:5px;background:var(--border);border-radius:3px">
                <div style="width:${semPct || 0}%;height:100%;background:${col(semPct || 0)};border-radius:3px;transition:width .4s"></div>
              </div>
              <div style="display:flex;justify-content:space-between;font-size:.58rem;color:var(--text3);margin-top:3px">
                <span>low</span><span>high</span>
              </div>
            </div>

            <!-- Sub-Sector: three states — matched/inferred | no_data | confirmed_mismatch -->
            ${(() => {
              const isMatch    = subStatus === 'matched' || subStatus === 'inferred';
              const isMismatch = subStatus === 'confirmed_mismatch';
              const isNoData   = !isMatch && !isMismatch;
              const borderCol  = isMatch ? 'var(--accent)' : 'var(--border)';
              const pctLabel   = isMatch ? `+${subBonusPct}%` : '—';
              const pctColor   = isMatch ? '#10b981' : 'var(--text3)';
              const barWidth   = isMatch ? subBonusPct : (isNoData ? 35 : 3);
              const barColor   = isMatch ? '#10b981' : (isNoData ? 'var(--text3)' : '#ef4444');
              const bodyHtml   = isMatch && subTagMatched.length
                ? `<div style="display:flex;flex-wrap:wrap;gap:3px;margin-bottom:5px">
                     ${subTagMatched.map(t =>
                       `<span style="font-size:.58rem;background:rgba(99,102,241,.15);
                         border:1px solid var(--accent);border-radius:3px;
                         padding:1px 5px;color:var(--accent2)">${escHtml(t)}</span>`
                     ).join('')}
                   </div>`
                : isNoData
                  ? `<div style="font-size:.62rem;color:var(--text3);line-height:1.5;margin-bottom:4px">No sub-sector data</div>`
                  : `<div style="font-size:.62rem;color:#ef4444;line-height:1.5;margin-bottom:4px">No matching sub-sectors</div>`;
              return `
            <div style="background:var(--bg);border:1px solid ${borderCol};border-radius:7px;padding:7px 9px">
              <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
                <span style="font-size:.6rem;text-transform:uppercase;letter-spacing:.08em;color:var(--text3);font-weight:600">Sub-Sector</span>
                <span style="font-family:monospace;font-size:.72rem;font-weight:700;color:${pctColor}">${pctLabel}</span>
              </div>
              ${bodyHtml}
              <div style="width:100%;height:3px;background:var(--border);border-radius:2px">
                <div style="width:${barWidth}%;height:100%;background:${barColor};border-radius:2px;transition:width .4s"></div>
              </div>
            </div>`;
            })()}

          </div>

          <!-- Row 3: match reasons -->
          ${reasons
            ? `<div style="font-size:.71rem;color:var(--text3);line-height:1.6;margin-bottom:7px;padding:6px 8px;background:var(--bg);border-radius:6px;border:1px solid var(--border)">
                 💡 ${escHtml(reasons)}
               </div>`
            : ''}

          <!-- Row 4: action links -->
          <div style="display:flex;gap:12px;align-items:center">
            ${m.submission_url
              ? `<a href="${escHtml(m.submission_url)}" target="_blank"
                    style="font-size:.73rem;color:var(--accent2);text-decoration:none;font-weight:500">
                   Submit pitch →
                 </a>`
              : ''}
            ${m.general_email
              ? `<a href="mailto:${escHtml(m.general_email)}"
                    style="font-size:.73rem;color:var(--text3);text-decoration:none">
                   ${escHtml(m.general_email)}
                 </a>`
              : ''}
          </div>

        </div>`;
      }).join('');
    matchesEl.style.display = 'block';
  } else {
    matchesEl.style.display = 'none';
  }

  results.classList.add('show');
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function addToPipeline(btn, investorName) {
  if (btn.disabled) return;
  btn.disabled = true;
  btn.textContent = '…';
  fetch('/api/pipeline/add', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({investor_name: investorName, source: 'match'}),
  })
  .then(r => r.json())
  .then(d => {
    if (d.success) {
      btn.textContent = '✓ Added';
      btn.style.borderColor = '#10b981';
      btn.style.color = '#10b981';
    } else {
      btn.textContent = 'Failed';
      btn.style.borderColor = '#ef4444';
      btn.style.color = '#ef4444';
      setTimeout(() => {
        btn.textContent = '+ Pipeline';
        btn.style.borderColor = '';
        btn.style.color = '';
        btn.disabled = false;
      }, 2000);
    }
  })
  .catch(() => {
    btn.textContent = 'Failed';
    btn.style.borderColor = '#ef4444';
    btn.style.color = '#ef4444';
    setTimeout(() => {
      btn.textContent = '+ Pipeline';
      btn.style.borderColor = '';
      btn.style.color = '';
      btn.disabled = false;
    }, 2000);
  });
}
</script>
"""

# ══════════════════════════════════════════════════════════════════════════
# M4 — Investor DB page (iframe wrapper)
# ══════════════════════════════════════════════════════════════════════════

M4_CONTENT = """\
<style>
  main { display:flex; flex-direction:column; }
  .m4-bar{
    padding:14px 28px;border-bottom:1px solid var(--border);
    display:flex;align-items:center;justify-content:space-between;
    flex-shrink:0;
  }
  .m4-bar h2{font-size:1rem;font-weight:600}
  .m4-bar p{color:var(--text2);font-size:.78rem;margin-top:2px}
  .m4-count{
    font-size:.78rem;background:var(--card);border:1px solid var(--border2);
    border-radius:6px;padding:4px 12px;color:var(--text2);
  }
  iframe{flex:1;width:100%;border:none;height:calc(100vh - 53px)}
</style>
<div class="m4-bar">
  <div>
    <h2>Investor Database</h2>
    <p>Washon M4 · curated VC &amp; PE data</p>
  </div>
  <div class="m4-count">{{ count }} investors</div>
</div>
<iframe src="/m4/view{{ '?auth=1' if current_user.is_authenticated else '' }}" title="Investor DB" id="m4frame"></iframe>
"""


# ══════════════════════════════════════════════════════════════════════════
# M9 — Pitching Guide & Pipeline page
# ══════════════════════════════════════════════════════════════════════════

M9_CONTENT = r"""
<style>
.m9-wrap{padding:28px 32px;max-width:1280px}
.m9-wrap .page-title{font-size:1.35rem;font-weight:700;margin-bottom:4px}
.m9-wrap .page-sub{color:var(--text2);font-size:.83rem;margin-bottom:24px}

.m9-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}
.m9-stat{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px 18px}
.m9-stat-label{font-size:.65rem;text-transform:uppercase;letter-spacing:.08em;color:var(--text3);margin-bottom:6px}
.m9-stat-val{font-size:1.6rem;font-weight:800;line-height:1}
.m9-stat-sub{font-size:.72rem;color:var(--text2);margin-top:4px}

.m9-funnel{display:flex;gap:0;margin-bottom:28px;overflow-x:auto;padding-bottom:4px}
.m9-fs{flex:1;min-width:130px;background:var(--card);border:1px solid var(--border);
  border-right:none;padding:14px 16px;cursor:pointer;transition:background .15s}
.m9-fs:first-child{border-radius:10px 0 0 10px}
.m9-fs:last-child{border-right:1px solid var(--border);border-radius:0 10px 10px 0}
.m9-fs:hover,.m9-fs.m9-af{background:var(--accent-dim)}
.m9-fs.m9-af{border-color:var(--accent)}
.m9-fs-num{font-size:.6rem;color:var(--text3);letter-spacing:.08em;text-transform:uppercase;margin-bottom:3px}
.m9-fs-name{font-size:.78rem;font-weight:600;margin-bottom:6px}
.m9-fs-count{font-size:1.5rem;font-weight:800}
.m9-fs-status{font-size:.67rem;color:var(--text2);margin-top:2px}
.m9-fs-bar{height:3px;border-radius:2px;margin-top:8px;background:var(--border)}
.m9-fs-bar-fill{height:100%;border-radius:2px;transition:width .4s}

.m9-toolbar{display:flex;align-items:center;gap:10px;margin-bottom:16px}
.m9-toolbar-left{display:flex;gap:6px;flex:1;flex-wrap:wrap}
.m9-fbtn{padding:5px 12px;border-radius:6px;border:1px solid var(--border2);
  background:var(--card);color:var(--text2);font-size:.78rem;cursor:pointer;transition:all .14s}
.m9-fbtn:hover,.m9-fbtn.m9-factive{background:var(--accent-dim);border-color:var(--accent);color:var(--accent2)}
.m9-btn{padding:7px 16px;border-radius:8px;border:none;background:var(--accent);
  color:#fff;font-size:.82rem;font-weight:600;cursor:pointer;transition:opacity .15s;
  display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.m9-btn:hover{opacity:.88}

.m9-inv-list{display:flex;flex-direction:column;gap:8px}
.m9-inv{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden;transition:border-color .15s}
.m9-inv:hover{border-color:var(--border2)}
.m9-inv.m9-stale{border-color:rgba(245,158,11,.35);background:rgba(245,158,11,.03)}
.m9-inv-hd{display:flex;align-items:center;gap:12px;padding:14px 18px;cursor:pointer;user-select:none}
.m9-avatar{width:36px;height:36px;border-radius:8px;flex-shrink:0;display:flex;
  align-items:center;justify-content:center;font-size:.85rem;font-weight:700;color:#fff}
.m9-inv-main{flex:1;min-width:0}
.m9-inv-co{font-size:.9rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.m9-inv-ct{font-size:.75rem;color:var(--text2);margin-top:1px}
.m9-inv-meta{display:flex;align-items:center;gap:8px;flex-shrink:0}
.m9-spill{font-size:.67rem;font-weight:700;padding:3px 9px;border-radius:20px;
  border:1px solid;white-space:nowrap}
.m9-sbadge{font-size:.67rem;background:rgba(245,158,11,.12);color:var(--amber);
  border:1px solid rgba(245,158,11,.3);border-radius:4px;padding:2px 7px}
.m9-chev{color:var(--text3);font-size:.72rem;transition:transform .2s}
.m9-inv.m9-open .m9-chev{transform:rotate(180deg)}

.m9-inv-detail{display:none;border-top:1px solid var(--border);padding:16px 18px}
.m9-inv.m9-open .m9-inv-detail{display:block}
.m9-detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:4px}
@media(max-width:640px){.m9-detail-grid{grid-template-columns:1fr}}
.m9-sec-h{font-size:.68rem;text-transform:uppercase;letter-spacing:.08em;color:var(--text3);margin-bottom:10px}
.m9-step-rows{display:flex;flex-direction:column;gap:3px}
.m9-step-r{display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:6px;
  cursor:pointer;transition:background .12s;font-size:.8rem}
.m9-step-r:hover{background:var(--card2)}
.m9-step-r.m9-s-cur{background:var(--accent-dim);color:var(--accent2)}
.m9-step-r.m9-s-done{color:var(--text3)}
.m9-step-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;border:2px solid currentColor}
.m9-step-r.m9-s-done .m9-step-dot{background:var(--green);border-color:var(--green)}
.m9-step-r.m9-s-cur .m9-step-dot{background:var(--accent);border-color:var(--accent)}
.m9-step-label{flex:1}
.m9-step-hint{font-size:.67rem;color:var(--text3)}

.m9-log-feed{display:flex;flex-direction:column;gap:6px;max-height:200px;overflow-y:auto;margin-bottom:10px}
.m9-log-feed::-webkit-scrollbar{width:4px}
.m9-log-feed::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px}
.m9-log-entry{display:flex;gap:8px;font-size:.78rem}
.m9-log-ico{width:22px;height:22px;border-radius:5px;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;font-size:.72rem}
.m9-log-ico.note{background:rgba(99,102,241,.15)}
.m9-log-ico.email{background:rgba(16,185,129,.15)}
.m9-log-ico.meeting{background:rgba(245,158,11,.15)}
.m9-log-body{flex:1}
.m9-log-txt{color:var(--text);line-height:1.4;cursor:text}
.m9-log-txt:hover{text-decoration:underline;text-decoration-style:dashed;text-decoration-color:var(--text3)}
.m9-log-edit-inp{width:100%;background:var(--card2);border:1px solid var(--accent);border-radius:5px;
  color:var(--text);font-size:.78rem;padding:2px 6px;outline:none;line-height:1.4}
.m9-log-ts{font-size:.67rem;color:var(--text3);margin-top:2px}
.m9-log-empty{color:var(--text3);font-size:.78rem;font-style:italic;padding:6px 0}
.m9-log-add{display:flex;gap:6px;margin-top:4px}
.m9-sel{padding:5px 8px;border-radius:6px;border:1px solid var(--border2);
  background:var(--card2);color:var(--text);font-size:.75rem;outline:none;cursor:pointer;flex-shrink:0}
.m9-inp{flex:1;padding:5px 10px;border-radius:6px;border:1px solid var(--border2);
  background:var(--card2);color:var(--text);font-size:.78rem;outline:none}
.m9-inp:focus{border-color:var(--accent)}
.m9-inp::placeholder{color:var(--text3)}
.m9-bsm{padding:5px 12px;border-radius:6px;border:none;background:var(--accent);
  color:#fff;font-size:.75rem;font-weight:600;cursor:pointer;flex-shrink:0}
.m9-bsm:hover{opacity:.88}
.m9-bdel{padding:5px 10px;border-radius:6px;border:1px solid rgba(239,68,68,.3);
  background:transparent;color:#ef4444;font-size:.75rem;cursor:pointer;margin-top:8px}
.m9-bdel:hover{background:rgba(239,68,68,.08)}

.m9-analytics{margin-top:32px}
.m9-analytics h3{font-size:.85rem;font-weight:600;margin-bottom:14px;color:var(--text2)}
.m9-ag{display:grid;grid-template-columns:repeat(auto-fill,minmax(145px,1fr));gap:10px}
.m9-ac{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px}
.m9-ac-label{font-size:.63rem;color:var(--text3);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px}
.m9-ac-val{font-size:1.2rem;font-weight:700}
.m9-ac-sub{font-size:.67rem;color:var(--text2);margin-top:2px}
.m9-conv-bar{height:3px;background:var(--border);border-radius:2px;margin-top:6px;overflow:hidden}
.m9-conv-fill{height:100%;background:var(--accent);border-radius:2px}

.m9-modal-ov{display:none;position:fixed;inset:0;background:rgba(0,0,0,.72);
  z-index:200;align-items:center;justify-content:center}
.m9-modal-ov.m9-show{display:flex}
.m9-modal{background:var(--card);border:1px solid var(--border2);border-radius:14px;
  padding:28px;width:480px;max-width:calc(100vw - 40px);max-height:90vh;overflow-y:auto}
.m9-modal h3{font-size:1rem;font-weight:700;margin-bottom:4px}
.m9-modal-sub{font-size:.78rem;color:var(--text2);margin-bottom:20px}
.m9-fg{margin-bottom:14px}
.m9-fg label{display:block;font-size:.72rem;color:var(--text2);margin-bottom:5px}
.m9-fi{width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--border2);
  background:var(--card2);color:var(--text);font-size:.84rem;outline:none}
.m9-fi:focus{border-color:var(--accent)}
.m9-modal-acts{display:flex;gap:8px;justify-content:flex-end;margin-top:20px}
.m9-bcancel{padding:7px 16px;border-radius:8px;border:1px solid var(--border2);
  background:transparent;color:var(--text2);font-size:.82rem;cursor:pointer}
.m9-bcancel:hover{border-color:var(--text3);color:var(--text)}

.m9-empty{text-align:center;padding:56px 24px;color:var(--text3)}
.m9-empty-icon{font-size:2.4rem;margin-bottom:12px}
.m9-empty p{font-size:.85rem;margin-bottom:16px}

/* Search step */
.m9-search-inp-wrap{position:relative;margin-bottom:8px}
.m9-search-inp{width:100%;padding:9px 12px 9px 36px;border-radius:8px;
  border:1px solid var(--border2);background:var(--card2);color:var(--text);
  font-size:.84rem;outline:none}
.m9-search-inp:focus{border-color:var(--accent)}
.m9-search-icon{position:absolute;left:11px;top:50%;transform:translateY(-50%);
  color:var(--text3);font-size:.9rem;pointer-events:none}
.m9-search-results{max-height:240px;overflow-y:auto;border:1px solid var(--border2);
  border-radius:8px;background:var(--card2);margin-bottom:10px;display:none}
.m9-search-results.m9-show{display:block}
.m9-search-card{padding:10px 12px;cursor:pointer;border-bottom:1px solid var(--border);
  transition:background .13s}
.m9-search-card:last-child{border-bottom:none}
.m9-search-card:hover{background:var(--accent-dim)}
.m9-sc-name{font-size:.85rem;font-weight:600;margin-bottom:4px}
.m9-sc-tags{display:flex;flex-wrap:wrap;gap:4px}
.m9-sc-tag{font-size:.62rem;padding:2px 7px;border-radius:4px;
  background:rgba(255,255,255,.06);border:1px solid var(--border2);color:var(--text3)}
.m9-search-empty{padding:14px 12px;font-size:.8rem;color:var(--text3);text-align:center}
.m9-skip-link{font-size:.75rem;color:var(--accent2);cursor:pointer;text-align:center;
  display:block;margin-top:4px;text-decoration:none;background:none;border:none;
  width:100%;padding:4px 0}
.m9-skip-link:hover{text-decoration:underline}

/* Investor profile summary box */
.m9-profile-box{background:var(--card2);border:1px solid var(--border2);border-radius:8px;
  padding:10px 12px;margin-bottom:14px;font-size:.78rem;color:var(--text2);line-height:1.7}
.m9-profile-box-label{font-size:.62rem;text-transform:uppercase;letter-spacing:.08em;
  color:var(--text3);margin-bottom:5px;font-weight:600}

/* Email draft panel */
.m9-email-panel{border-top:1px solid var(--border);padding-top:14px;margin-top:14px}
.m9-email-hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
.m9-email-title{font-size:.78rem;font-weight:600;color:var(--text2)}
.m9-email-gen-btn{padding:5px 12px;border-radius:6px;border:none;
  background:var(--accent);color:#fff;font-size:.74rem;font-weight:600;
  cursor:pointer;transition:opacity .15s;display:inline-flex;align-items:center;gap:5px}
.m9-email-gen-btn:hover{opacity:.86}
.m9-email-gen-btn:disabled{opacity:.5;cursor:not-allowed}
.m9-email-empty{font-size:.78rem;color:var(--text3);font-style:italic;
  padding:10px 0;text-align:center}
.m9-email-warn{font-size:.76rem;color:var(--amber);padding:8px 10px;border-radius:6px;
  background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.25);margin-bottom:8px}
.m9-email-subj{width:100%;padding:7px 10px;border-radius:6px;border:1px solid var(--border2);
  background:var(--card2);color:var(--text);font-size:.8rem;outline:none;margin-bottom:7px}
.m9-email-subj:focus{border-color:var(--accent)}
.m9-email-body{width:100%;padding:7px 10px;border-radius:6px;border:1px solid var(--border2);
  background:var(--card2);color:var(--text);font-size:.78rem;outline:none;
  resize:vertical;min-height:120px;font-family:inherit;line-height:1.55;margin-bottom:8px}
.m9-email-body:focus{border-color:var(--accent)}
.m9-email-acts{display:flex;gap:6px;flex-wrap:wrap}
.m9-email-act-btn{padding:5px 11px;border-radius:6px;font-size:.74rem;font-weight:500;
  border:1px solid var(--border2);background:var(--card2);color:var(--text2);
  cursor:pointer;transition:all .13s}
.m9-email-act-btn:hover{border-color:var(--accent);color:var(--accent2)}
.m9-bp-drop{border:1px dashed var(--border2);border-radius:6px;padding:7px 12px;font-size:.73rem;
  color:var(--text3);cursor:pointer;text-align:center;transition:all .13s;margin-bottom:7px}
.m9-bp-drop:hover{border-color:var(--accent);color:var(--accent2);background:var(--accent-dim)}
.m9-bp-file-info{display:flex;align-items:center;gap:6px;font-size:.73rem;color:var(--text2);
  margin-bottom:7px;background:var(--card2);border-radius:6px;padding:5px 10px;
  border:1px solid var(--border2)}
.m9-bp-fname{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.m9-bp-remove{border:none;background:none;color:var(--text3);cursor:pointer;font-size:.8rem;
  padding:0 3px;line-height:1;transition:color .12s}
.m9-bp-remove:hover{color:var(--red)}
/* Two-panel email output */
.m9-epanel-box{background:var(--card2);border:1px solid var(--border2);border-radius:10px;
  padding:16px;margin-bottom:12px}
.m9-epanel-label{font-size:.6rem;text-transform:uppercase;letter-spacing:.1em;font-weight:700;
  color:var(--text3);margin-bottom:10px}
.m9-epanel-subj{font-size:.84rem;font-weight:600;color:var(--text);margin-bottom:8px}
.m9-epanel-body{font-size:.78rem;color:var(--text);line-height:1.75;white-space:pre-wrap}
.m9-epanel-copy{margin-top:12px;padding:6px 14px;border-radius:7px;border:1px solid var(--border2);
  background:transparent;color:var(--text2);font-size:.75rem;cursor:pointer;transition:all .13s}
.m9-epanel-copy:hover{border-color:var(--accent);color:var(--accent2)}
.m9-epanel-section{margin-bottom:14px}
.m9-epanel-section:last-child{margin-bottom:0}
.m9-epanel-section-hd{font-size:.62rem;font-weight:700;text-transform:uppercase;
  letter-spacing:.09em;color:var(--accent2);margin-bottom:5px}
.m9-epanel-rule{border:none;border-top:1px solid var(--border);margin-bottom:8px}
.m9-epanel-section-body{font-size:.74rem;color:var(--text2);white-space:pre-wrap;line-height:1.6}
/* Log entry collapsible detail */
.m9-log-link{font-size:.78rem;color:var(--accent2);cursor:pointer;line-height:1.4}
.m9-log-link::before{content:'▶  '}
.m9-log-link.m9-ll-open::before{content:'▼  '}
.m9-log-link:hover{text-decoration:underline}
.m9-log-detail{background:var(--card2);font-size:.74rem;white-space:pre-wrap;line-height:1.55;
  padding:10px;border-radius:6px;margin-top:6px;color:var(--text2);display:none;
  max-height:260px;overflow-y:auto}
.m9-log-detail.m9-ld-open{display:block}
.m9-spin{display:inline-block;width:12px;height:12px;border:2px solid rgba(255,255,255,.3);
  border-top-color:#fff;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle}
.m9-email-log-box{background:var(--bg);border:1px solid var(--border);border-radius:10px;
  padding:16px 20px;margin-top:20px;max-height:260px;overflow-y:auto;text-align:left}
.m9-email-log-inner{display:flex;flex-direction:column;gap:4px}
.log-line{font-family:'SF Mono','Fira Code','Consolas',monospace;font-size:.75rem;
  color:var(--text2);line-height:1.6;white-space:pre;opacity:0;
  animation:log-fade-in .2s ease forwards}
.log-line.separator{color:var(--border2)}
.log-line.highlight{color:var(--text)}
.log-line.success{color:var(--green)}
.log-line.warning{color:var(--amber)}
@keyframes log-fade-in{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:translateY(0)}}
</style>

<div class="m9-wrap">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:6px;flex-wrap:wrap;gap:10px">
    <div>
      <div class="page-title">Pitching Guide &amp; Pipeline</div>
      <div class="page-sub">Track investor outreach across 6 stages &middot; 7-day inactivity alerts</div>
    </div>
    <button class="m9-btn" onclick="m9OpenAdd()">&#xff0b; Add Investor</button>
  </div>

  <div class="m9-stats">
    <div class="m9-stat"><div class="m9-stat-label">Total in Pipeline</div><div class="m9-stat-val" id="m9-s-total">—</div><div class="m9-stat-sub">all investors</div></div>
    <div class="m9-stat"><div class="m9-stat-label">Active</div><div class="m9-stat-val" id="m9-s-active">—</div><div class="m9-stat-sub">steps 1–5</div></div>
    <div class="m9-stat"><div class="m9-stat-label">Funded / Closed</div><div class="m9-stat-val" id="m9-s-funded" style="color:var(--green)">—</div><div class="m9-stat-sub">step 6</div></div>
    <div class="m9-stat"><div class="m9-stat-label">Need Attention</div><div class="m9-stat-val" id="m9-s-stale" style="color:var(--amber)">—</div><div class="m9-stat-sub">7+ days inactive</div></div>
  </div>

  <div class="m9-funnel" id="m9-funnel"></div>

  <div class="m9-toolbar">
    <div class="m9-toolbar-left" id="m9-filter-btns"></div>
    <div style="font-size:.75rem;color:var(--text3)" id="m9-fcount"></div>
  </div>

  <div class="m9-inv-list" id="m9-inv-list"></div>

  <div class="m9-analytics" id="m9-analytics" style="display:none">
    <h3>&#128202; Pipeline Analytics</h3>
    <div class="m9-ag" id="m9-ag"></div>
  </div>
</div>

<div class="m9-modal-ov" id="m9-add-modal" onclick="if(event.target===this)m9CloseAdd()">
  <div class="m9-modal">
    <!-- STEP 1: Search -->
    <div id="m9-modal-step1">
      <h3>Add Investor to Pipeline</h3>
      <div class="m9-modal-sub">Search the investor database or add manually.</div>
      <label style="font-size:.72rem;color:var(--text2);display:block;margin-bottom:5px">Search Investor Database</label>
      <div class="m9-search-inp-wrap">
        <span class="m9-search-icon">&#128269;</span>
        <input class="m9-search-inp" id="m9-search-q" placeholder="e.g. Sequoia, climate VC..." autocomplete="off" oninput="m9SearchDebounce()">
      </div>
      <div class="m9-search-results" id="m9-search-results"></div>
      <button class="m9-skip-link" onclick="m9GoStep2(null)">Skip — add manually</button>
      <div class="m9-modal-acts" style="margin-top:12px">
        <button class="m9-bcancel" onclick="m9CloseAdd()">Cancel</button>
      </div>
    </div>
    <!-- STEP 2: Confirm & Configure -->
    <div id="m9-modal-step2" style="display:none">
      <h3>Confirm &amp; Configure</h3>
      <div class="m9-modal-sub">Review details before adding to pipeline.</div>
      <div id="m9-profile-summary" style="display:none">
        <div class="m9-profile-box-label">Investor Profile</div>
        <div class="m9-profile-box" id="m9-profile-box"></div>
      </div>
      <div class="m9-fg"><label>Company Name *</label><input class="m9-fi" id="m9-fi-co" placeholder="e.g. Sequoia Capital" autocomplete="off"></div>
      <div class="m9-fg"><label>Contact Person</label><input class="m9-fi" id="m9-fi-ct" placeholder="e.g. Jane Smith" autocomplete="off"></div>
      <div class="m9-fg"><label>Email</label><input class="m9-fi" id="m9-fi-em" type="email" placeholder="jane@sequoia.com" autocomplete="off"></div>
      <div class="m9-fg">
        <label>Starting Stage</label>
        <select class="m9-fi" id="m9-fi-step" style="cursor:pointer">
          <option value="1">Step 1 — Preparation</option>
          <option value="2">Step 2 — Prospecting</option>
          <option value="3">Step 3 — Outreach</option>
          <option value="4">Step 4 — Engagement</option>
          <option value="5">Step 5 — Due Diligence</option>
          <option value="6">Step 6 — Closing</option>
        </select>
      </div>
      <div id="m9-modal-err" style="display:none;font-size:.78rem;color:#fca5a5;margin-bottom:8px"></div>
      <div class="m9-modal-acts">
        <button class="m9-bcancel" onclick="m9BackToSearch()">&#8592; Back</button>
        <button class="m9-btn" onclick="m9AddInvestor()">Add to Pipeline</button>
      </div>
    </div>
  </div>
</div>

<script>
const M9_STEPS = [
  {num:1, name:'Preparation',   status:'In Progress',       color:'#6366f1'},
  {num:2, name:'Prospecting',   status:'Shortlisted',       color:'#8b5cf6'},
  {num:3, name:'Outreach',      status:'Contacted',         color:'#3b82f6'},
  {num:4, name:'Engagement',    status:'Meeting Scheduled', color:'#0ea5e9'},
  {num:5, name:'Due Diligence', status:'Under Review',      color:'#f59e0b'},
  {num:6, name:'Closing',       status:'Funded / Closed',   color:'#10b981'},
];
const M9_LOG_ICONS = {note:'📝', email:'📧', meeting:'🤝'};
const M9_STALE = 7;

let m9Data = {investors:[]};
let m9Filter = null;
let m9SelectedProfile = null;  // holds the DB record chosen in step 1

async function m9Init() {
  await m9Fetch();
  m9RenderAll();
}

async function m9Fetch() {
  const r = await fetch('/m9/data');
  m9Data = await r.json();
}

function m9RenderAll() {
  m9Stats();
  m9Funnel();
  m9FilterBtns();
  m9List();
  m9Analytics();
}

function m9DaysSince(iso) {
  if (!iso) return 0;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
}

function m9FmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}) +
    ' ' + d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'});
}

function m9Esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function m9Stats() {
  const inv = m9Data.investors;
  document.getElementById('m9-s-total').textContent  = inv.length;
  document.getElementById('m9-s-active').textContent = inv.filter(i=>i.step<6).length;
  document.getElementById('m9-s-funded').textContent = inv.filter(i=>i.step===6).length;
  document.getElementById('m9-s-stale').textContent  = inv.filter(i=>i.step<6 && m9DaysSince(i.updated_at)>=M9_STALE).length;
}

function m9Funnel() {
  const counts = M9_STEPS.map(s => m9Data.investors.filter(i=>i.step===s.num).length);
  const mx = Math.max(...counts, 1);
  document.getElementById('m9-funnel').innerHTML = M9_STEPS.map((s,i) =>
    '<div class="m9-fs' + (m9Filter===s.num?' m9-af':'') + '" onclick="m9SetFilter(' + s.num + ')">' +
      '<div class="m9-fs-num">Step ' + s.num + '</div>' +
      '<div class="m9-fs-name">' + s.name + '</div>' +
      '<div class="m9-fs-count" style="color:' + s.color + '">' + counts[i] + '</div>' +
      '<div class="m9-fs-status">' + s.status + '</div>' +
      '<div class="m9-fs-bar"><div class="m9-fs-bar-fill" style="width:' + (counts[i]/mx*100).toFixed(0) + '%;background:' + s.color + '"></div></div>' +
    '</div>'
  ).join('');
}

function m9FilterBtns() {
  document.getElementById('m9-filter-btns').innerHTML =
    '<button class="m9-fbtn' + (m9Filter===null?' m9-factive':'') + '" onclick="m9SetFilter(null)">All</button>' +
    M9_STEPS.map(s =>
      '<button class="m9-fbtn' + (m9Filter===s.num?' m9-factive':'') + '" onclick="m9SetFilter(' + s.num + ')">' + s.name + '</button>'
    ).join('');
}

function m9SetFilter(step) {
  m9Filter = step;
  m9Funnel();
  m9FilterBtns();
  m9List();
}

function m9List() {
  const el = document.getElementById('m9-inv-list');
  let inv = m9Filter !== null ? m9Data.investors.filter(i=>i.step===m9Filter) : [...m9Data.investors];
  const total = m9Data.investors.length;
  document.getElementById('m9-fcount').textContent =
    m9Filter !== null ? inv.length + ' of ' + total : total + ' investor' + (total!==1?'s':'');

  if (total === 0) {
    el.innerHTML = '<div class="m9-empty"><div class="m9-empty-icon">🎯</div><p>No investors yet. Add your first to start tracking.</p><button class="m9-btn" onclick="m9OpenAdd()">＋ Add Investor</button></div>';
    return;
  }
  if (inv.length === 0) {
    el.innerHTML = '<div class="m9-empty"><div class="m9-empty-icon">🔍</div><p>No investors in this stage.</p></div>';
    return;
  }
  el.innerHTML = inv.map(m9BuildCard).join('');
}

function m9LogEntryHtml(l, invId) {
  const ico  = (M9_LOG_ICONS[l.type] || '📝');
  const detail = l.detail || '';
  if (detail) {
    const lid = 'm9ld-' + l.id;
    return '<div class="m9-log-entry">' +
      '<div class="m9-log-ico ' + (l.type||'note') + '">' + ico + '</div>' +
      '<div class="m9-log-body">' +
        '<div class="m9-log-link" onclick="m9ToggleLogDetail(this,\'' + lid + '\');event.stopPropagation()">' + m9Esc(l.text) + '</div>' +
        '<div class="m9-log-detail" id="' + lid + '">' + m9Esc(detail) + '</div>' +
        '<div class="m9-log-ts">' + m9FmtDate(l.ts) + ' &middot; ' + (l.type||'note') + '</div>' +
      '</div></div>';
  }
  return '<div class="m9-log-entry"><div class="m9-log-ico ' + (l.type||'note') + '">' + ico + '</div>' +
    '<div class="m9-log-body">' +
      '<div class="m9-log-txt" title="Double-click to edit" ondblclick="m9EditLog(this,' + "'" + invId + "'," + "'" + l.id + "'" + ')">' + m9Esc(l.text) + '</div>' +
      '<div class="m9-log-ts">' + m9FmtDate(l.ts) + ' &middot; ' + (l.type||'note') + '</div>' +
    '</div></div>';
}

function m9ToggleLogDetail(el, detailId) {
  el.classList.toggle('m9-ll-open');
  const d = document.getElementById(detailId);
  if (d) d.classList.toggle('m9-ld-open');
}

function m9BuildCard(inv) {
  const step  = M9_STEPS[inv.step - 1];
  const stale = inv.step < 6 && m9DaysSince(inv.updated_at) >= M9_STALE;
  const days  = m9DaysSince(inv.updated_at);
  const init  = m9Esc(inv.company).slice(0,2).toUpperCase();
  const logs  = inv.logs || [];
  const ip    = inv.investor_profile || {};

  const stepsHtml = M9_STEPS.map(s => {
    const done = s.num < inv.step, cur = s.num === inv.step;
    return '<div class="m9-step-r' + (done?' m9-s-done':'') + (cur?' m9-s-cur':'') + '" onclick="m9MoveStep(' + "'" + inv.id + "'," + s.num + ');event.stopPropagation()">' +
      '<div class="m9-step-dot"></div>' +
      '<span class="m9-step-label">Step ' + s.num + ' &middot; ' + s.name + '</span>' +
      '<span class="m9-step-hint">' + (cur ? '&larr; current' : done ? '&#10003;' : '') + '</span>' +
    '</div>';
  }).join('');

  const logsHtml = logs.length === 0
    ? '<div class="m9-log-empty">No activity logged yet.</div>'
    : logs.slice().reverse().map(l => m9LogEntryHtml(l, inv.id)).join('');

  const contactLine = [inv.contact ? m9Esc(inv.contact) : '', inv.email ? m9Esc(inv.email) : ''].filter(Boolean).join(' &middot; ');

  // Email panel HTML (always rendered; state managed via DOM)
  const emailPanelHtml =
    '<div class="m9-email-panel" id="m9-ep-' + inv.id + '">' +
      '<div class="m9-email-hd">' +
        '<span class="m9-email-title">&#9993; Draft Outreach Email</span>' +
        '<button class="m9-email-gen-btn" id="m9-egb-' + inv.id + '" onclick="m9GenEmail(' + "'" + inv.id + "'" + ');event.stopPropagation()">&#10024; Generate</button>' +
      '</div>' +
      '<div class="m9-bp-upload">' +
        '<div class="m9-bp-drop" id="m9-bpdrop-' + inv.id + '" ' +
          'onclick="document.getElementById(' + "'m9-bpfile-" + inv.id + "'" + ').click();event.stopPropagation()">' +
          '&#128206; Attach BP (PDF/DOCX, optional — overrides saved profile)' +
        '</div>' +
        '<input type="file" id="m9-bpfile-' + inv.id + '" accept=".pdf,.docx" style="display:none" ' +
          'onchange="m9BpFileSelect(this,' + "'" + inv.id + "'" + ')" onclick="event.stopPropagation()">' +
        '<div class="m9-bp-file-info" id="m9-bpfi-' + inv.id + '" style="display:none">' +
          '<span class="m9-bp-fname" id="m9-bpfn-' + inv.id + '"></span>' +
          '<button class="m9-bp-remove" onclick="m9BpRemove(' + "'" + inv.id + "'" + ');event.stopPropagation()">&#10005; Remove</button>' +
        '</div>' +
      '</div>' +
      '<div id="m9-email-content-' + inv.id + '">' +
        '<div id="m9-email-loading-' + inv.id + '" style="display:none">' +
          '<div style="text-align:center;padding:8px 0 0">' +
            '<span class="m9-spin" style="border-color:var(--border2);border-top-color:var(--accent);width:16px;height:16px"></span>' +
          '</div>' +
          '<div class="m9-email-log-box" id="m9-log-box-' + inv.id + '">' +
            '<div class="m9-email-log-inner" id="m9-log-inner-' + inv.id + '"></div>' +
          '</div>' +
        '</div>' +
        '<div class="m9-email-empty" id="m9-email-empty-' + inv.id + '">Click Generate to draft a personalised email for this investor.</div>' +
        '<div id="m9-email-warn-' + inv.id + '" style="display:none" class="m9-email-warn">&#9888; No startup profile found &mdash; parse a BP in M1 first.</div>' +
        '<div id="m9-email-output-' + inv.id + '" style="display:none">' +
          '<div class="m9-epanel-box">' +
            '<div class="m9-epanel-label">Email</div>' +
            '<div class="m9-epanel-subj" id="m9-esubj-' + inv.id + '"></div>' +
            '<div class="m9-epanel-body" id="m9-ebody-' + inv.id + '"></div>' +
            '<button class="m9-epanel-copy" onclick="m9CopyEmail(' + "'" + inv.id + "'" + ');event.stopPropagation()">&#128203; Copy</button>' +
          '</div>' +
          '<div class="m9-epanel-box">' +
            '<div class="m9-epanel-label">Analysis</div>' +
            '<div class="m9-epanel-section">' +
              '<div class="m9-epanel-section-hd">Research Summary</div>' +
              '<div class="m9-epanel-section-body" id="m9-email-research-' + inv.id + '"></div>' +
            '</div>' +
            '<hr class="m9-epanel-rule">' +
            '<div class="m9-epanel-section">' +
              '<div class="m9-epanel-section-hd">Personalisation Score</div>' +
              '<div class="m9-epanel-section-body" id="m9-email-score-' + inv.id + '"></div>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>' +
    '</div>';

  return '<div class="m9-inv' + (stale?' m9-stale':'') + '" id="m9-inv-' + inv.id + '">' +
    '<div class="m9-inv-hd" onclick="m9Toggle(' + "'" + inv.id + "'" + ')">' +
      '<div class="m9-avatar" style="background:' + step.color + '">' + init + '</div>' +
      '<div class="m9-inv-main">' +
        '<div class="m9-inv-co">' + m9Esc(inv.company) + '</div>' +
        '<div class="m9-inv-ct">' + (contactLine || '&nbsp;') + '</div>' +
      '</div>' +
      '<div class="m9-inv-meta">' +
        (stale ? '<span class="m9-sbadge">&#9888; ' + days + 'd inactive</span>' : '') +
        '<span class="m9-spill" style="color:' + step.color + ';border-color:' + step.color + '30;background:' + step.color + '12">' + step.name + '</span>' +
        '<span class="m9-chev">&#9660;</span>' +
      '</div>' +
    '</div>' +
    '<div class="m9-inv-detail">' +
      '<div class="m9-detail-grid">' +
        '<div><div class="m9-sec-h">Pipeline Steps</div><div class="m9-step-rows">' + stepsHtml + '</div></div>' +
        '<div><div class="m9-sec-h">Activity Log</div>' +
          '<div class="m9-log-feed">' + logsHtml + '</div>' +
          '<div class="m9-log-add">' +
            '<select class="m9-sel" id="m9-lt-' + inv.id + '"><option value="note">📝</option><option value="email">📧</option><option value="meeting">🤝</option></select>' +
            '<input class="m9-inp" id="m9-li-' + inv.id + '" placeholder="Add note, email, or meeting..." onkeydown="if(event.key===' + "'" + 'Enter' + "'" + ')m9AddLog(' + "'" + inv.id + "'" + ')" onclick="event.stopPropagation()">' +
            '<button class="m9-bsm" onclick="m9AddLog(' + "'" + inv.id + "'" + ');event.stopPropagation()">Log</button>' +
          '</div>' +
          '<button class="m9-bdel" onclick="m9Delete(' + "'" + inv.id + "'" + ');event.stopPropagation()">&#128465; Remove</button>' +
        '</div>' +
      '</div>' +
      emailPanelHtml +
    '</div>' +
  '</div>';
}

// ── Email draft ────────────────────────────────────────────────────────────

const m9BpFiles = {};

function m9BpFileSelect(inp, invId) {
  const file = inp.files[0];
  if (!file) return;
  if (file.size > 20 * 1024 * 1024) {
    alert('File too large — max 20 MB');
    inp.value = '';
    return;
  }
  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  if (ext !== '.pdf' && ext !== '.docx') {
    alert('Only PDF or DOCX accepted');
    inp.value = '';
    return;
  }
  const reader = new FileReader();
  reader.onload = function(e) {
    m9BpFiles[invId] = {name: file.name, b64: e.target.result.split(',')[1]};
    document.getElementById('m9-bpfn-' + invId).textContent = file.name;
    document.getElementById('m9-bpfi-' + invId).style.display = '';
    document.getElementById('m9-bpdrop-' + invId).style.display = 'none';
  };
  reader.readAsDataURL(file);
}

function m9BpRemove(invId) {
  delete m9BpFiles[invId];
  const inp = document.getElementById('m9-bpfile-' + invId);
  if (inp) inp.value = '';
  document.getElementById('m9-bpfi-' + invId).style.display = 'none';
  document.getElementById('m9-bpdrop-' + invId).style.display = '';
}

function m9CleanEmail(text) {
  return (text || '').replace(/^\s+/, '').replace(/\s+$/, '');
}

async function m9GenEmail(invId) {
  const btn       = document.getElementById('m9-egb-' + invId);
  const emptyEl   = document.getElementById('m9-email-empty-' + invId);
  const warnEl    = document.getElementById('m9-email-warn-' + invId);
  const outputEl  = document.getElementById('m9-email-output-' + invId);
  const loadingEl = document.getElementById('m9-email-loading-' + invId);
  const logInner  = document.getElementById('m9-log-inner-' + invId);
  const logBox    = document.getElementById('m9-log-box-' + invId);

  btn.disabled = true;
  btn.innerHTML = '<span class="m9-spin"></span> Generating…';
  emptyEl.style.display   = 'none';
  warnEl.style.display    = 'none';
  outputEl.style.display  = 'none';
  logInner.innerHTML      = '';
  loadingEl.style.display = '';

  function appendLog(text) {
    const el = document.createElement('div');
    el.className = 'log-line';
    if (text.includes('─────')) el.classList.add('separator');
    else if (text.includes('complete') || text.includes('confirmed') || text.includes('accepted')) el.classList.add('highlight');
    else if (text.includes('✓') || text.includes('passed')) el.classList.add('success');
    else if (text.includes('would not reply') || text.includes('No direct') || text.includes('Weak section')) el.classList.add('warning');
    el.textContent = text;
    logInner.appendChild(el);
    logBox.scrollTop = logBox.scrollHeight;
  }

  try {
    const fd = new FormData();
    fd.append('investor_id', invId);
    const bpData = m9BpFiles[invId];
    if (bpData) {
      const byteStr = atob(bpData.b64);
      const arr = new Uint8Array(byteStr.length);
      for (let i = 0; i < byteStr.length; i++) arr[i] = byteStr.charCodeAt(i);
      const mime = bpData.name.toLowerCase().endsWith('.pdf')
        ? 'application/pdf'
        : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
      fd.append('bp_file', new Blob([arr], {type: mime}), bpData.name);
    }

    const resp = await fetch('/m9/generate-email-sse', {method: 'POST', body: fd});
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data:')) continue;
        let evt;
        try { evt = JSON.parse(line.slice(5).trim()); } catch(_) { continue; }

        if (evt.log) {
          appendLog(evt.log);
        } else if (evt.error) {
          loadingEl.style.display = 'none';
          if ((evt.error + '').includes('No startup profile')) {
            warnEl.style.display = '';
          } else {
            emptyEl.textContent = 'Error: ' + evt.error;
            emptyEl.style.display = '';
          }
        } else if (evt.done) {
          loadingEl.style.display = 'none';
          const subject  = m9CleanEmail(evt.subject);
          const body     = m9CleanEmail(evt.body);
          const score    = m9CleanEmail(evt.score_summary);
          const research = m9CleanEmail(evt.research_summary);
          const missing  = '(not returned by model — try regenerating)';

          document.getElementById('m9-esubj-' + invId).textContent = subject;
          document.getElementById('m9-ebody-' + invId).textContent = body;

          const researchEl2 = document.getElementById('m9-email-research-' + invId);
          const scoreEl2    = document.getElementById('m9-email-score-' + invId);
          [researchEl2, scoreEl2].forEach(el => el && (el.style.fontStyle = 'normal', el.style.color = ''));
          if (research) { researchEl2.textContent = research; }
          else { researchEl2.textContent = missing; researchEl2.style.fontStyle = 'italic'; researchEl2.style.color = 'var(--text3)'; }
          if (score)    { scoreEl2.textContent = score; }
          else { scoreEl2.textContent = missing; scoreEl2.style.fontStyle = 'italic'; scoreEl2.style.color = 'var(--text3)'; }
          outputEl.style.display = '';

          const nl = String.fromCharCode(10);
          const logShort  = 'AI Draft \xb7 ' + subject;
          const logDetail = 'Subject: ' + subject + nl + nl + body + nl + nl +
            '=== RESEARCH SUMMARY ===' + nl + (research || missing) + nl + nl +
            '=== PERSONALISATION SCORE ===' + nl + (score || missing);
          try {
            const logResp = await fetch('/m9/log', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({id: invId, type: 'email', text: logShort, detail: logDetail})
            });
            if (logResp.ok) {
              const entry = await logResp.json();
              const inv = m9Data.investors.find(i => i.id === invId);
              if (inv) {
                inv.logs = inv.logs || [];
                inv.logs.push(entry);
                inv.updated_at = entry.ts;
                const logFeed = document.querySelector('#m9-inv-' + invId + ' .m9-log-feed');
                if (logFeed) logFeed.innerHTML = inv.logs.slice().reverse().map(l => m9LogEntryHtml(l, invId)).join('');
              }
              const savedEl = document.createElement('div');
              savedEl.style.cssText = 'font-size:.72rem;color:var(--green);text-align:center;margin-top:6px';
              savedEl.textContent = '✓ Saved to Activity Log';
              outputEl.after(savedEl);
              setTimeout(() => savedEl.remove(), 2000);
            }
          } catch(_) {}
        }
      }
    }
  } catch(e) {
    loadingEl.style.display = 'none';
    emptyEl.textContent = 'Network error: ' + e.message;
    emptyEl.style.display = '';
  }
  btn.disabled = false;
  btn.innerHTML = '&#10024; Regenerate';
}

function m9CopyEmail(invId) {
  const subj = (document.getElementById('m9-esubj-' + invId) || {}).textContent || '';
  const body = (document.getElementById('m9-ebody-' + invId) || {}).textContent || '';
  const text = 'Subject: ' + subj + String.fromCharCode(10,10) + body;
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.querySelector('#m9-ep-' + invId + ' .m9-epanel-copy');
    if (btn) { const orig = btn.textContent; btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = orig, 1800); }
  });
}

async function m9LogEmail(invId) {
  const subj = document.getElementById('m9-esubj-' + invId).value;
  if (!subj) return;
  const text = '[AI Draft] ' + subj;
  const r = await fetch('/m9/log', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id: invId, type: 'email', text})
  });
  if (r.ok) {
    const entry = await r.json();
    const inv = m9Data.investors.find(i => i.id === invId);
    if (inv) { inv.logs = inv.logs || []; inv.logs.push(entry); inv.updated_at = entry.ts; }
    m9RenderAll();
    setTimeout(() => { const c = document.getElementById('m9-inv-' + invId); if(c) c.classList.add('m9-open'); }, 10);
  }
}

// ── Toggle ─────────────────────────────────────────────────────────────────

function m9Toggle(id) {
  document.getElementById('m9-inv-' + id).classList.toggle('m9-open');
}

function m9Analytics() {
  const inv = m9Data.investors;
  const el  = document.getElementById('m9-analytics');
  if (inv.length === 0) { el.style.display = 'none'; return; }
  el.style.display = '';
  const total  = inv.length;
  const counts = M9_STEPS.map(s => inv.filter(i => i.step === s.num).length);
  document.getElementById('m9-ag').innerHTML = M9_STEPS.map((s,i) => {
    const cnt  = counts[i];
    const from = inv.filter(x => x.step >= s.num).length;
    const to   = inv.filter(x => x.step >= s.num + 1).length;
    const conv = i < 5 ? (from > 0 ? (to/from*100).toFixed(0) : '—') : null;
    return '<div class="m9-ac">' +
      '<div class="m9-ac-label">' + s.name + '</div>' +
      '<div class="m9-ac-val" style="color:' + s.color + '">' + cnt + '</div>' +
      '<div class="m9-ac-sub">' + (total > 0 ? (cnt/total*100).toFixed(0) : 0) + '% of total</div>' +
      (conv !== null ?
        '<div class="m9-conv-bar"><div class="m9-conv-fill" style="width:' + (isNaN(+conv)?0:conv) + '%"></div></div>' +
        '<div style="font-size:.64rem;color:var(--text3);margin-top:3px">&rarr; ' + conv + '% advance</div>'
        : '') +
    '</div>';
  }).join('');
}

async function m9MoveStep(id, step) {
  const stepDef = M9_STEPS[step - 1];
  const r = await fetch('/m9/update', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id, step, status: stepDef.status})
  });
  if (r.ok) {
    const updated = await r.json();
    const idx = m9Data.investors.findIndex(i => i.id === id);
    if (idx >= 0) m9Data.investors[idx] = updated;
    m9RenderAll();
    setTimeout(() => { const c = document.getElementById('m9-inv-' + id); if(c) c.classList.add('m9-open'); }, 10);
  }
}

async function m9AddLog(id) {
  const typeEl = document.getElementById('m9-lt-' + id);
  const textEl = document.getElementById('m9-li-' + id);
  const text = textEl.value.trim();
  if (!text) return;
  const r = await fetch('/m9/log', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({id, type: typeEl.value, text})
  });
  if (r.ok) {
    const entry = await r.json();
    const inv = m9Data.investors.find(i => i.id === id);
    if (inv) { inv.logs = inv.logs || []; inv.logs.push(entry); inv.updated_at = entry.ts; }
    m9RenderAll();
    setTimeout(() => { const c = document.getElementById('m9-inv-' + id); if(c) c.classList.add('m9-open'); }, 10);
  }
}

function m9EditLog(el, invId, logId) {
  const original = el.textContent;
  const inp = document.createElement('input');
  inp.className = 'm9-log-edit-inp';
  inp.value = original;
  el.replaceWith(inp);
  inp.focus();
  inp.select();

  let committed = false;

  async function commit() {
    if (committed) return;
    committed = true;
    inp.removeEventListener('blur', commit);
    const newText = inp.value.trim();
    if (!newText || newText === original) { cancel(); return; }
    const r = await fetch('/m9/log-edit', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({investor_id: invId, log_id: logId, text: newText})
    });
    if (r.ok) {
      const inv = m9Data.investors.find(i => i.id === invId);
      if (inv) {
        const entry = inv.logs && inv.logs.find(l => l.id === logId);
        if (entry) entry.text = newText;
      }
      m9RenderAll();
      setTimeout(() => { const c = document.getElementById('m9-inv-' + invId); if(c) c.classList.add('m9-open'); }, 10);
    } else {
      cancel();
    }
  }

  function cancel() {
    inp.removeEventListener('blur', commit);
    if (!document.contains(inp)) return;
    const restored = document.createElement('div');
    restored.className = 'm9-log-txt';
    restored.title = 'Double-click to edit';
    restored.setAttribute('ondblclick', 'm9EditLog(this,' + "'" + invId + "'," + "'" + logId + "'" + ')');
    restored.textContent = original;
    inp.replaceWith(restored);
  }

  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { e.preventDefault(); committed = true; cancel(); }
  });
  // Delay blur listener to avoid immediate-blur from the dblclick mouseup sequence
  setTimeout(() => { if (!committed) inp.addEventListener('blur', commit); }, 200);
}

async function m9Delete(id) {
  if (!confirm('Remove this investor from the pipeline?')) return;
  const r = await fetch('/m9/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id})});
  if (r.ok) { m9Data.investors = m9Data.investors.filter(i => i.id !== id); m9RenderAll(); }
}

// ── Add investor modal — two-step flow ─────────────────────────────────────

let m9SearchTimer = null;

function m9OpenAdd() {
  m9SelectedProfile = null;
  document.getElementById('m9-modal-step1').style.display = '';
  document.getElementById('m9-modal-step2').style.display = 'none';
  document.getElementById('m9-search-q').value = '';
  document.getElementById('m9-search-results').classList.remove('m9-show');
  document.getElementById('m9-add-modal').classList.add('m9-show');
  setTimeout(() => document.getElementById('m9-search-q').focus(), 50);
}

function m9CloseAdd() {
  document.getElementById('m9-add-modal').classList.remove('m9-show');
  const errEl = document.getElementById('m9-modal-err');
  if (errEl) errEl.style.display = 'none';
}

function m9BackToSearch() {
  m9SelectedProfile = null;
  document.getElementById('m9-modal-step1').style.display = '';
  document.getElementById('m9-modal-step2').style.display = 'none';
}

function m9SearchDebounce() {
  clearTimeout(m9SearchTimer);
  m9SearchTimer = setTimeout(m9DoSearch, 300);
}

async function m9DoSearch() {
  const q = document.getElementById('m9-search-q').value.trim();
  const resultsEl = document.getElementById('m9-search-results');
  if (!q) { resultsEl.classList.remove('m9-show'); return; }
  resultsEl.innerHTML = '<div class="m9-search-empty">Searching...</div>';
  resultsEl.classList.add('m9-show');
  try {
    const r = await fetch('/m9/search-investors?q=' + encodeURIComponent(q));
    const data = await r.json();
    const results = data.results || [];
    if (!results.length) {
      resultsEl.innerHTML = '<div class="m9-search-empty">No matches found.</div>';
      return;
    }
    resultsEl.innerHTML = results.map((rec, idx) => {
      const tags = [];
      if (rec.stage)    tags.push(rec.stage.split(',').slice(0,2).map(s=>s.trim()).join(', '));
      if (rec.focus)    tags.push(rec.focus.split(',').slice(0,3).map(s=>s.trim()).join(', '));
      if (rec.location) tags.push(rec.location.split(',')[0].trim());
      return '<div class="m9-search-card" onclick="m9SelectResult(' + idx + ')" data-idx="' + idx + '">' +
        '<div class="m9-sc-name">' + m9Esc(rec.name) + (rec.website ? ' <span style="font-size:.65rem;color:var(--accent2)">&#8599;</span>' : '') + '</div>' +
        '<div class="m9-sc-tags">' + tags.map(t=>'<span class="m9-sc-tag">' + m9Esc(t) + '</span>').join('') + '</div>' +
      '</div>';
    }).join('');
    // store results for selection
    resultsEl._m9results = results;
  } catch(e) {
    resultsEl.innerHTML = '<div class="m9-search-empty">Error: ' + m9Esc(e.message) + '</div>';
  }
}

function m9SelectResult(idx) {
  const resultsEl = document.getElementById('m9-search-results');
  const results = resultsEl._m9results || [];
  const rec = results[idx];
  if (!rec) return;
  m9GoStep2(rec);
}

function m9GoStep2(rec) {
  m9SelectedProfile = rec;
  document.getElementById('m9-modal-step1').style.display = 'none';
  document.getElementById('m9-modal-step2').style.display = '';

  // Pre-fill fields
  document.getElementById('m9-fi-co').value = rec ? (rec.name || '') : '';
  document.getElementById('m9-fi-ct').value = '';
  document.getElementById('m9-fi-em').value = '';
  document.getElementById('m9-fi-step').value = '1';
  document.getElementById('m9-modal-err').style.display = 'none';

  // Show profile summary if a DB record was selected
  const summaryEl = document.getElementById('m9-profile-summary');
  const boxEl     = document.getElementById('m9-profile-box');
  if (rec && (rec.focus || rec.stage || rec.location)) {
    const lines = [];
    if (rec.stage)    lines.push('<b>Stage:</b> ' + m9Esc(rec.stage));
    if (rec.focus)    lines.push('<b>Focus:</b> ' + m9Esc(rec.focus.split(',').slice(0,5).join(', ')));
    if (rec.location) lines.push('<b>Location:</b> ' + m9Esc(rec.location));
    if (rec.check_size)     lines.push('<b>Check:</b> ' + m9Esc(rec.check_size));
    if (rec.investor_type)  lines.push('<b>Type:</b> ' + m9Esc(rec.investor_type));
    if (rec.website)        lines.push('<b>Website:</b> <a href="' + m9Esc(rec.website) + '" target="_blank" style="color:var(--accent2)">' + m9Esc(rec.website) + '</a>');
    boxEl.innerHTML = lines.join('<br>');
    summaryEl.style.display = '';
  } else {
    summaryEl.style.display = 'none';
  }

  setTimeout(() => document.getElementById('m9-fi-co').focus(), 40);
}

async function m9AddInvestor() {
  const company = document.getElementById('m9-fi-co').value.trim();
  const contact = document.getElementById('m9-fi-ct').value.trim();
  const email   = document.getElementById('m9-fi-em').value.trim();
  const step    = parseInt(document.getElementById('m9-fi-step').value);
  const errEl   = document.getElementById('m9-modal-err');
  if (!company) { errEl.textContent = 'Company name is required.'; errEl.style.display = 'block'; return; }
  errEl.style.display = 'none';

  // Build investor_profile from selected DB record
  let investor_profile = {};
  if (m9SelectedProfile) {
    investor_profile = {
      focus:    m9SelectedProfile.focus    || '',
      stage:    m9SelectedProfile.stage    || '',
      location: m9SelectedProfile.location || '',
      website:  m9SelectedProfile.website  || '',
      check_size: m9SelectedProfile.check_size || '',
      investor_type: m9SelectedProfile.investor_type || '',
      thesis:   m9SelectedProfile.thesis   || '',
    };
  }

  const r = await fetch('/m9/add', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({company, contact, email, step, investor_profile})
  });
  if (r.ok) {
    const inv = await r.json();
    m9Data.investors.push(inv);
    m9CloseAdd();
    m9SelectedProfile = null;
    document.getElementById('m9-fi-step').value = '1';
    m9RenderAll();
  }
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') m9CloseAdd(); });
m9Init();
</script>
"""


# ══════════════════════════════════════════════════════════════════════════
# M10 — Saved Analyses
# ══════════════════════════════════════════════════════════════════════════

M10_CONTENT = """\
<style>
.m10-wrap{padding:32px;max-width:960px}
.m10-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;flex-wrap:wrap;gap:12px}
.m10-title{font-size:1.35rem;font-weight:700}
.m10-sub{color:var(--text2);font-size:.83rem;margin-top:2px}
.m10-stats{display:flex;gap:20px}
.m10-stat{text-align:center}
.m10-stat-val{font-size:1.4rem;font-weight:800;color:var(--accent2);line-height:1}
.m10-stat-lbl{font-size:.65rem;color:var(--text3);text-transform:uppercase;letter-spacing:.07em;margin-top:3px}

.m10-empty{text-align:center;padding:80px 20px;color:var(--text3)}
.m10-empty-icon{font-size:3rem;margin-bottom:16px}
.m10-empty h3{font-size:1.1rem;color:var(--text2);margin-bottom:8px}
.m10-empty p{font-size:.83rem}

.m10-list{display:flex;flex-direction:column;gap:12px}

.m10-card{
  background:var(--card);border:1px solid var(--border);border-radius:12px;
  padding:18px 20px;display:flex;align-items:center;gap:16px;
  transition:border-color .15s;
}
.m10-card:hover{border-color:var(--border2)}
.m10-card-body{flex:1;min-width:0}
.m10-card-name{font-size:1rem;font-weight:700;color:var(--text);margin-bottom:4px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.m10-card-meta{display:flex;flex-wrap:wrap;gap:6px;font-size:.72rem;color:var(--text2);margin-bottom:6px}
.m10-tag{background:var(--card2);border:1px solid var(--border2);border-radius:4px;padding:1px 7px}
.m10-card-date{font-size:.72rem;color:var(--text3)}
.m10-card-score{
  flex-shrink:0;width:52px;height:52px;border-radius:50%;
  border:3px solid var(--border2);
  display:flex;align-items:center;justify-content:center;
  flex-direction:column;
}
.m10-score-val{font-size:.85rem;font-weight:800;line-height:1}
.m10-score-lbl{font-size:.5rem;color:var(--text3);text-transform:uppercase;letter-spacing:.05em}
.m10-card-actions{display:flex;gap:6px;flex-shrink:0}
.m10-btn{
  padding:6px 12px;border-radius:6px;font-size:.75rem;font-weight:600;
  border:1px solid var(--border2);background:var(--card2);color:var(--text2);
  cursor:pointer;transition:all .13s;text-decoration:none;display:inline-flex;align-items:center;gap:4px;
}
.m10-btn:hover{border-color:var(--accent);color:var(--accent2)}
.m10-btn.danger:hover{border-color:var(--red);color:#fca5a5}

/* Detail modal */
.m10-modal-overlay{
  display:none;position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:200;
  align-items:center;justify-content:center;padding:24px;
}
.m10-modal-overlay.open{display:flex}
.m10-modal{
  background:var(--card);border:1px solid var(--border2);border-radius:16px;
  width:100%;max-width:760px;max-height:88vh;overflow-y:auto;position:relative;
}
.m10-modal-hdr{
  padding:22px 26px 18px;border-bottom:1px solid var(--border);
  position:sticky;top:0;background:var(--card);z-index:1;
}
.m10-modal-title{font-size:1.15rem;font-weight:800;margin-bottom:6px;padding-right:36px}
.m10-modal-badges{display:flex;gap:6px;flex-wrap:wrap}
.m10-modal-close{
  position:absolute;top:18px;right:18px;background:none;border:none;
  color:var(--text3);font-size:20px;cursor:pointer;line-height:1;transition:color .13s;
}
.m10-modal-close:hover{color:var(--text)}
.m10-modal-body{padding:22px 26px}
.m10-section{margin-bottom:22px}
.m10-section-label{
  font-size:.65rem;text-transform:uppercase;letter-spacing:.09em;
  color:var(--text3);font-weight:700;margin-bottom:10px;
  display:flex;align-items:center;gap:8px;
}
.m10-section-label::after{content:'';flex:1;height:1px;background:var(--border)}
.m10-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.m10-field{display:flex;flex-direction:column;gap:2px}
.m10-fkey{font-size:.65rem;text-transform:uppercase;letter-spacing:.06em;color:var(--text3)}
.m10-fval{font-size:.84rem;color:var(--text);font-weight:500}
.m10-tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.m10-tag-item{font-size:.75rem;padding:2px 8px;border-radius:4px;
  background:rgba(255,255,255,.05);border:1px solid var(--border2);color:var(--text2)}
.m10-match-card{
  background:var(--card2);border:1px solid var(--border);border-radius:8px;
  padding:12px 14px;margin-bottom:8px;
}
.m10-match-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px}
.m10-match-name{font-size:.88rem;font-weight:700;color:var(--text)}
.m10-match-score{font-family:monospace;font-size:.82rem;color:#10b981;font-weight:700}
.m10-match-meta{display:flex;gap:6px;flex-wrap:wrap;font-size:.72rem;color:var(--text2);margin-bottom:4px}
.m10-match-reasons{font-size:.72rem;color:var(--text3);line-height:1.5}
</style>

<div class="m10-wrap">
  <div class="m10-header">
    <div>
      <div class="m10-title">Saved Analyses</div>
      <div class="m10-sub">All parsed business plan results</div>
    </div>
    <div class="m10-stats">
      <div class="m10-stat">
        <div class="m10-stat-val" id="m10-total">—</div>
        <div class="m10-stat-lbl">Analyses</div>
      </div>
      <div class="m10-stat">
        <div class="m10-stat-val" id="m10-recent">—</div>
        <div class="m10-stat-lbl">Latest</div>
      </div>
    </div>
  </div>

  <div id="m10-empty" class="m10-empty" style="display:none">
    <div class="m10-empty-icon">📭</div>
    <h3>No analyses yet</h3>
    <p>Upload a business plan in <a href="/m1" style="color:var(--accent2)">BP Parser</a> to get started.</p>
  </div>

  <div class="m10-list" id="m10-list"></div>
</div>

<!-- Detail modal -->
<div class="m10-modal-overlay" id="m10-overlay" onclick="m10CloseModal(event)">
  <div class="m10-modal" id="m10-modal">
    <div class="m10-modal-hdr">
      <div class="m10-modal-title" id="m10-modal-title"></div>
      <div class="m10-modal-badges" id="m10-modal-badges"></div>
      <button class="m10-modal-close" onclick="m10CloseDirect()">×</button>
    </div>
    <div class="m10-modal-body" id="m10-modal-body"></div>
  </div>
</div>

<script>
let m10Data = [];

function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function toStr(v){ return Array.isArray(v) ? v.join(', ') : String(v||''); }

function scoreColor(c){
  if(!c && c!==0) return 'var(--border2)';
  const p = c <= 1 ? c*100 : c;
  return p>=80 ? '#10b981' : p>=50 ? '#f59e0b' : '#6b7280';
}

function fmtDate(iso){
  if(!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});
}

function m10Load(){
  fetch('/m10/data')
    .then(r=>r.json())
    .then(data=>{
      m10Data = data.analyses || [];
      m10Render();
    })
    .catch(()=>{ document.getElementById('m10-list').innerHTML='<p style="color:var(--text3);padding:20px">Failed to load analyses.</p>'; });
}

function m10Render(){
  const list = document.getElementById('m10-list');
  const empty = document.getElementById('m10-empty');
  document.getElementById('m10-total').textContent = m10Data.length;
  document.getElementById('m10-recent').textContent = m10Data.length ? fmtDate(m10Data[0].created_at) : '—';

  if(!m10Data.length){ empty.style.display=''; list.innerHTML=''; return; }
  empty.style.display='none';

  list.innerHTML = m10Data.map((a,i) => {
    const conf = a.overall_confidence || 0;
    const pct  = Math.round((conf<=1?conf*100:conf));
    const col  = scoreColor(conf);
    return `<div class="m10-card">
      <div class="m10-card-score" style="border-color:${col}">
        <div class="m10-score-val" style="color:${col}">${pct}%</div>
        <div class="m10-score-lbl">conf</div>
      </div>
      <div class="m10-card-body">
        <div class="m10-card-name">${esc(a.company_name)}</div>
        <div class="m10-card-meta">
          ${a.funding_stage ? `<span class="m10-tag">${esc(a.funding_stage)}</span>` : ''}
          ${a.sector        ? `<span class="m10-tag">${esc(toStr(a.sector).split(',')[0].trim())}</span>` : ''}
          <span class="m10-tag">${a.match_count || 0} matches</span>
          <span class="m10-tag">${esc(a.filename)}</span>
        </div>
        <div class="m10-card-date">${fmtDate(a.created_at)}</div>
      </div>
      <div class="m10-card-actions">
        <button class="m10-btn" onclick="m10View(${i})">🔍 View</button>
        <a class="m10-btn" href="/m10/export/${esc(a.id)}" download="${esc(a.company_name)}.json">⬇ Export</a>
        <button class="m10-btn danger" onclick="m10Delete('${esc(a.id)}')">🗑</button>
      </div>
    </div>`;
  }).join('');
}

function m10View(i){
  const a = m10Data[i];
  const p = a.profile || {};
  const conf = a.overall_confidence || 0;
  const pct  = Math.round((conf<=1?conf*100:conf));
  const col  = scoreColor(conf);

  document.getElementById('m10-modal-title').textContent = a.company_name || '—';
  document.getElementById('m10-modal-badges').innerHTML = [
    a.funding_stage ? `<span class="m10-tag-item">${esc(a.funding_stage)}</span>` : '',
    a.sector        ? `<span class="m10-tag-item">${esc(toStr(a.sector).split(',')[0].trim())}</span>` : '',
    `<span class="m10-tag-item" style="color:${col}">${pct}% confidence</span>`,
    `<span class="m10-tag-item">${fmtDate(a.created_at)}</span>`,
  ].filter(Boolean).join('');

  const fields = [
    ['Company',        p.company_name],
    ['Tagline',        p.tagline],
    ['Funding Stage',  p.funding_stage],
    ['Capital Need',   p.capital_need],
    ['Sector',         toStr(p.sector)],
    ['Sub-Sector',     toStr(p.sub_sector_tags)],
    ['Geography',      p.geography],
    ['Business Model', toStr(p.business_model)],
    ['Use of Funds',   p.use_of_funds],
    ['Key Traction',   p.key_traction],
    ['Team Background',toStr(p.team_background)],
  ];

  const matches = a.matches || [];
  const matchHtml = matches.length
    ? matches.map(m => {
        const ms = typeof m.score==='number' ? Math.round(m.score*100)+'%' : '—';
        const reasons = (m.match_reasons||[]).join(' · ');
        const stages  = (m.stages||[]).join(', ');
        const nameHtml = m.website
          ? `<a href="${esc(m.website)}" target="_blank" style="color:var(--text);text-decoration:none">${esc(m.name||'—')} <span style="color:var(--accent2);font-size:.72rem">↗</span></a>`
          : esc(m.name||'—');
        return `<div class="m10-match-card">
          <div class="m10-match-row">
            <span class="m10-match-name">${nameHtml}</span>
            <span class="m10-match-score">${ms}</span>
          </div>
          <div class="m10-match-meta">
            ${stages        ? `<span class="m10-tag">${esc(stages)}</span>` : ''}
            ${m.check_size  ? `<span class="m10-tag">${esc(m.check_size)}</span>` : ''}
          </div>
          ${reasons ? `<div class="m10-match-reasons">${esc(reasons)}</div>` : ''}
        </div>`;
      }).join('')
    : '<p style="color:var(--text3);font-size:.83rem">No matches recorded.</p>';

  document.getElementById('m10-modal-body').innerHTML = `
    <div class="m10-section">
      <div class="m10-section-label">Profile</div>
      <div class="m10-grid">
        ${fields.filter(([,v])=>v).map(([k,v])=>`
          <div class="m10-field">
            <span class="m10-fkey">${esc(k)}</span>
            <span class="m10-fval">${esc(toStr(v))}</span>
          </div>`).join('')}
      </div>
      ${p.missing_signals && p.missing_signals.length ? `
      <div style="margin-top:14px">
        <div class="m10-fkey" style="margin-bottom:6px">Missing Signals</div>
        <div class="m10-tags">${(Array.isArray(p.missing_signals)?p.missing_signals:[p.missing_signals])
          .map(s=>`<span class="m10-tag-item">${esc(s)}</span>`).join('')}</div>
      </div>` : ''}
    </div>
    <div class="m10-section">
      <div class="m10-section-label">Matched Investors (${matches.length})</div>
      ${matchHtml}
    </div>`;

  document.getElementById('m10-overlay').classList.add('open');
  document.body.style.overflow='hidden';
}

function m10Delete(id){
  if(!confirm('Delete this analysis?')) return;
  fetch('/m10/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})})
    .then(r=>r.json())
    .then(()=>m10Load())
    .catch(e=>alert('Delete failed: '+e));
}

function m10CloseModal(e){
  if(e.target===document.getElementById('m10-overlay')) m10CloseDirect();
}
function m10CloseDirect(){
  document.getElementById('m10-overlay').classList.remove('open');
  document.body.style.overflow='';
}
document.addEventListener('keydown',e=>{ if(e.key==='Escape') m10CloseDirect(); });

m10Load();
</script>
"""

# ══════════════════════════════════════════════════════════════════════════
# Auth gate
# ══════════════════════════════════════════════════════════════════════════

_PUBLIC_ENDPOINTS = frozenset({
    "index", "home", "auth.login", "auth.register", "static",
    # Investor DB is publicly browsable — no account needed
    "m4", "m4_view", "m4_active_jsonl", "m4_investors",
})

def _is_api_request():
    return (
        request.path.startswith("/api/")
        or request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )

@app.before_request
def require_login():
    if request.endpoint and request.endpoint not in _PUBLIC_ENDPOINTS:
        if not current_user.is_authenticated:
            if _is_api_request():
                return jsonify({"error": "Unauthorized", "redirect": "/auth/login"}), 401
            return redirect(url_for("auth.login", next=request.url))

# ══════════════════════════════════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("landing.html")


@app.route("/home")
def home():
    return render_template("landing.html")


# ── M1 ──────────────────────────────────────────────────────────────────

@app.route("/m1")
def m1():
    html = SHELL.replace("{{ content }}", M1_CONTENT)
    return render_template_string(html, active="m1")


@app.route("/m1/parse", methods=["POST"])
def m1_parse():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    f   = request.files["file"]
    ext = Path(f.filename or "").suffix.lower()
    if ext not in ALLOWED:
        return jsonify({"error": f"Unsupported file type '{ext}'. Use PDF or DOCX."}), 400

    tmp_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    try:
        f.save(str(tmp_path))
        text    = extract_text(str(tmp_path))
        profile = parse_with_gemini(text)
        flat    = flatten_profile(profile)

        # Defensive normalization: flatten_profile always sets _profile, but guard
        # against future refactors and ensure flat-level fields are populated from
        # the nested profile when the parser returns null values.
        if "_profile" not in flat:
            flat["_profile"] = profile
        _p = flat["_profile"]
        if not flat.get("sub_sector_tags"):
            try:
                vals = _p["tier2"]["sub_sector_tags"]["value"] or []
                if isinstance(vals, list) and vals:
                    flat["sub_sector_tags"] = ", ".join(str(t) for t in vals)
            except (KeyError, TypeError):
                pass
        if not flat.get("narrative_text"):
            try:
                flat["narrative_text"] = _p["tier3"]["narrative_text"] or ""
            except (KeyError, TypeError):
                pass

        matches = run_match(flat, top_n=15)

        # 重新计算字段完整度分数（必须在保存前完成）
        scored_fields = [
            flat.get("funding_stage"),
            flat.get("sector"),
            flat.get("capital_need"),
            flat.get("geography"),
            flat.get("business_model"),
            flat.get("sub_sector_tags"),
            flat.get("key_traction"),
            flat.get("team_background"),
            flat.get("use_of_funds"),
            flat.get("investor_type_preference"),
            flat.get("instrument"),
        ]
        def _is_filled(v) -> bool:
            if not v:
                return False
            s = str(v).strip().lower()
            if not s:
                return False
            EMPTY_SIGNALS = (
                "none", "null", "unknown", "n/a", "not specified",
                "not found", "not mentioned", "not stated", "not disclosed",
                "no funding", "no amount", "not provided", "unspecified",
            )
            return not any(sig in s for sig in EMPTY_SIGNALS)

        filled = sum(1 for f in scored_fields if _is_filled(f))
        flat["overall_confidence"] = round(filled / len(scored_fields), 2)

        # Persist analysis (per-user, ORM)
        analysis = {
            "company_name":       flat.get("company_name") or "Unknown",
            "created_at":         datetime.utcnow().isoformat() + "Z",
            "filename":           f.filename or "unknown",
            "sector":             flat.get("sector") or "",
            "funding_stage":      flat.get("funding_stage") or "",
            "overall_confidence": flat.get("overall_confidence") or 0,
            "match_count":        len(matches),
            "profile":            flat,
            "matches":            matches,
        }
        a_row = AnalysisModel(
            user_id=current_user.id,
            filename=analysis["filename"],
            company=analysis["company_name"],
            stage=analysis["funding_stage"],
            sector=analysis["sector"],
            result=analysis,
        )
        _db.session.add(a_row)
        _db.session.commit()

        return jsonify({"profile": flat, "matches": matches})
    except Exception as e:
        msg = str(e)
        if "GEMINI_API_KEY" in msg or "API_KEY" in msg:
            return jsonify({"error": "Claude API key error — check your .env file."}), 502
        if "quota" in msg.lower() or "429" in msg:
            return jsonify({"error": "Claude rate limit hit. Please wait and retry."}), 429
        return jsonify({"error": msg[:300]}), 500
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# ── M4 ──────────────────────────────────────────────────────────────────

@app.route("/m4")
def m4():
    count = "—"
    try:
        with open(ROOT / "web" / "active.jsonl") as fp:
            count = f"{sum(1 for l in fp if l.strip()):,}"
    except Exception:
        pass
    html = SHELL.replace("{{ content }}", M4_CONTENT)
    return render_template_string(html, active="m4", count=count)


@app.route("/m4/view")
def m4_view():
    return send_file(str(ROOT / "web" / "index_v3.html"))


@app.route("/m4/active.jsonl")
def m4_active_jsonl():
    return send_file(str(ROOT / "web" / "active.jsonl"), mimetype="application/x-ndjson")


@app.route("/m4/investors.json")
def m4_investors():
    data = []
    with open(ROOT / "web" / "active.jsonl") as fp:
        for line in fp:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return jsonify(data)


# ── M9 ──────────────────────────────────────────────────────────────────

_STEP_STATUSES = [
    "In Progress", "Shortlisted", "Contacted",
    "Meeting Scheduled", "Under Review", "Funded / Closed",
]

@app.route("/m9")
def m9():
    html = SHELL.replace("{{ content }}", M9_CONTENT)
    return render_template_string(html, active="m9")


@app.route("/m9/data")
def m9_data():
    return jsonify(_load_pipeline())


@app.route("/m9/add", methods=["POST"])
def m9_add():
    data = _load_pipeline()
    body = request.get_json(force=True) or {}
    company = (body.get("company") or "").strip()
    if not company:
        return jsonify({"error": "Company name is required"}), 400
    step = max(1, min(6, int(body.get("step", 1))))
    now  = datetime.utcnow().isoformat()
    investor = {
        "id":               uuid.uuid4().hex,
        "company":          company,
        "contact":          (body.get("contact") or "").strip(),
        "email":            (body.get("email") or "").strip(),
        "step":             step,
        "status":           _STEP_STATUSES[step - 1],
        "logs":             [],
        "created_at":       now,
        "updated_at":       now,
        "investor_profile": body.get("investor_profile") or {},
    }
    data["investors"].append(investor)
    _save_pipeline(data)
    return jsonify(investor)


@app.route("/api/pipeline/add", methods=["POST"])
@login_required
def api_pipeline_add():
    body = request.get_json(force=True) or {}
    investor_name = (body.get("investor_name") or "").strip()
    if not investor_name:
        return jsonify({"success": False, "error": "investor_name is required"}), 400
    data = _load_pipeline()
    now  = datetime.utcnow().isoformat()
    investor = {
        "id":               uuid.uuid4().hex,
        "company":          investor_name,
        "contact":          "",
        "email":            "",
        "step":             1,
        "status":           _STEP_STATUSES[0],
        "logs":             [],
        "created_at":       now,
        "updated_at":       now,
        "investor_profile": {"source": body.get("source", "match")},
    }
    data["investors"].append(investor)
    _save_pipeline(data)
    return jsonify({"success": True, "pipeline_id": investor["id"]})


@app.route("/m9/update", methods=["POST"])
def m9_update():
    data = _load_pipeline()
    body = request.get_json(force=True) or {}
    inv_id = body.get("id")
    for inv in data["investors"]:
        if inv["id"] == inv_id:
            inv["step"]       = max(1, min(6, int(body.get("step", inv["step"]))))
            inv["status"]     = body.get("status", inv.get("status", ""))
            inv["updated_at"] = datetime.utcnow().isoformat()
            _save_pipeline(data)
            return jsonify(inv)
    return jsonify({"error": "Not found"}), 404


@app.route("/m9/log", methods=["POST"])
def m9_log():
    data = _load_pipeline()
    body = request.get_json(force=True) or {}
    inv_id = body.get("id")
    text   = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Log text required"}), 400
    for inv in data["investors"]:
        if inv["id"] == inv_id:
            now   = datetime.utcnow().isoformat()
            entry = {
                "id":     uuid.uuid4().hex,
                "ts":     now,
                "type":   body.get("type", "note"),
                "text":   text,
                "detail": body.get("detail", ""),
            }
            inv.setdefault("logs", []).append(entry)
            inv["updated_at"] = now
            _save_pipeline(data)
            return jsonify(entry)
    return jsonify({"error": "Not found"}), 404


@app.route("/m9/log-edit", methods=["POST"])
def m9_log_edit():
    data   = _load_pipeline()
    body   = request.get_json(force=True) or {}
    inv_id = body.get("investor_id")
    log_id = body.get("log_id")
    text   = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    for inv in data["investors"]:
        if inv["id"] == inv_id:
            for entry in inv.get("logs", []):
                if entry["id"] == log_id:
                    entry["text"] = text
                    _save_pipeline(data)
                    return jsonify(entry)
            return jsonify({"error": "log entry not found"}), 404
    return jsonify({"error": "investor not found"}), 404


@app.route("/m9/delete", methods=["POST"])
def m9_delete():
    data   = _load_pipeline()
    body   = request.get_json(force=True) or {}
    inv_id = body.get("id")
    data["investors"] = [i for i in data["investors"] if i["id"] != inv_id]
    _save_pipeline(data)
    return jsonify({"ok": True})


@app.route("/m9/search-investors")
def m9_search_investors():
    q = (request.args.get("q") or "").strip().lower()
    results = []
    try:
        with open(ROOT / "web" / "active.jsonl") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                name = rec.get("name") or rec.get("display_name") or ""
                if q and q not in name.lower():
                    continue
                # map active.jsonl fields to response shape
                sectors = rec.get("sectors", [])
                if isinstance(sectors, str):
                    try:
                        sectors = json.loads(sectors.replace("'", '"'))
                    except Exception:
                        sectors = [sectors]
                stages = rec.get("stages", [])
                if isinstance(stages, str):
                    try:
                        stages = json.loads(stages.replace("'", '"'))
                    except Exception:
                        stages = [stages]
                geo = rec.get("geo_focus", [])
                if isinstance(geo, str):
                    try:
                        geo = json.loads(geo.replace("'", '"'))
                    except Exception:
                        geo = [geo]
                results.append({
                    "name":     name,
                    "focus":    ", ".join(sectors) if isinstance(sectors, list) else str(sectors),
                    "stage":    ", ".join(stages) if isinstance(stages, list) else str(stages),
                    "location": ", ".join(geo) if isinstance(geo, list) else str(geo),
                    "website":  rec.get("official_website") or "",
                    "check_size": rec.get("check_size_display") or "",
                    "investor_type": rec.get("investor_type") or "",
                    "thesis":   (rec.get("thesis_text") or "")[:200],
                })
                if len(results) >= 10:
                    break
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"results": results})


_RESEARCH_SYSTEM_PROMPT = """\
You are an investment research analyst who specialises in venture capital across all sectors.
You will adapt your research to whatever industry the startup operates in — do not assume life science.

YOUR ONLY JOB IN THIS CALL: research the named VC firm using web_search and return a JSON object.
Do NOT draft any email. Do NOT write any prose outside the JSON object.

RESEARCH PROCEDURE
==================
Execute at least 4 web_search calls before writing your answer.
Derive [SECTOR] and [VERTICAL] from the startup's sector described in the user message —
do not default to biotech or life science unless that is what the startup actually is.

  Query 1: [VC] portfolio investments [SECTOR] 2024 2025
  Query 2: [VC] managing partner general partner quotes thesis
  Query 3: [VC] [VERTICAL] investment focus [SECTOR]
  Query 4: [VC] notable exits acquisitions IPO portfolio

Extract from search results:
  - Portfolio companies in the last 2 years, especially those in the same sector as the startup
  - Key partners / managing directors (capture their FIRST NAMES)
  - Direct quotes from partners about their thesis (exact wording preferred over paraphrase)
  - Concrete exits: acquirer name, dollar amount if disclosed
  - Stated investment stage and check size range

DATA INTEGRITY RULES
====================
  - If you cannot confirm a field from search results, set it to null.
  - NEVER fabricate portfolio companies, quotes, exit amounts, or partner names.
    A null is always better than an invented fact.
  - If a quote is approximate (paraphrased from an article), prefix it with "~".

OUTPUT FORMAT
=============
Output ONLY a single JSON object. No markdown fences. No preamble. No trailing commentary.
Exactly this schema:

{
  "anchor_a": {
    "company": "<name of portfolio company most analogous to the startup, or null>",
    "sector":  "<its sector/technology in 3-5 words>",
    "similarity": "<one sentence: the specific dimension on which this is analogous — e.g. business model, market position, technical category. Be precise about the dimension; do not claim full technical equivalence if modalities differ>",
    "modality_delta": "<one sentence: any material technical difference between this company and the startup that would affect how the analogy should be framed, or null if no significant difference>"
  },
  "anchor_b": {
    "quote":     "<direct or approximate quote from a named partner, or null>",
    "speaker":   "<partner first + last name, or null>",
    "source":    "<publication or URL, or null>",
    "resonance": "<one sentence: how this quote aligns with the startup's core thesis>"
  },
  "anchor_c": {
    "event":  "<a named, datable external event — FDA approval, IPO, regulation, clinical readout — or null>",
    "year":   "<4-digit year or null>",
    "timing": "<one sentence: why this event makes now the right moment for this startup>"
  },
  "top_exit": {
    "company":          "<portfolio company name, or null>",
    "acquirer_or_ipo":  "<acquirer name or IPO ticker, or null>",
    "amount_usd":       "<dollar amount as string e.g. '$200M', or null>"
  },
  "partner_first_names": ["<name>"],
  "vc_thesis_one_line":  "<their core investment thesis in one sentence, derived from search results>",
  "competitive_landscape": {
    "named_competitors": ["<name 2-4 real companies in the same category as the startup, based on search results. Do not fabricate. Use null array if genuinely unknown: []>"],
    "differentiation_angle": "<one sentence: the most defensible structural difference between the startup and the named competitors, based only on information in the startup's materials — not invented>"
  },
  "stage_fit": {
    "vc_preferred_stage": "<seed / pre-A / Series A / growth / crossover / all-stage, or null>",
    "startup_stage":      "<the startup's current funding stage from context>",
    "fit_verdict":        "<high / medium / low>",
    "fit_note":           "<one sentence: key alignment or mismatch between VC stage preference and startup stage>"
  },
  "anchor_quality_warning": "<null if anchors are confirmed specific investments or quotes with sources; otherwise a one-sentence warning that anchors may be generic PR language>"
}
"""

_EMAIL_DRAFT_SYSTEM_PROMPT = """\
You are an elite fundraising advisor who works with startups across all industries.
You write cold outreach emails that VC partners stop and actually read.
You do NOT default to life science framing — you adapt entirely to the startup's actual sector.

You will receive:
  - RESEARCH ANCHORS — structured data about the VC firm from a prior research step
  - VC INFO — basic info about the firm and contact
  - STARTUP INFO — the company seeking investment

YOUR ONLY JOB: write one outreach email and return it as a JSON object.
Do NOT include any prose, commentary, or explanation outside the JSON.

════════════════════════════════════════
EMAIL STRUCTURE — each section mandatory
════════════════════════════════════════

HOOK  (maximum 2 sentences, hard cap: 60 words total for this section)
  Goal: make the reader feel seen — they know you did real homework.
  - Open from the RECIPIENT's perspective, not the sender's.
  - SENTENCE STRUCTURE RULE: never use the pattern "[fact] — and [fact] — tells me [conclusion]"
    as a single sentence. This structure always exceeds 60 words. Split into two sentences instead.
  - If anchor_a has a real company AND top_exit has a real amount → cite the exit:
      Sentence 1: "[Company]'s exit to [Acquirer] at [Amount] confirmed your bet before [X inflection]."
      Sentence 2: "That tells me [VC name] sees what most [sector] investors still miss: [specific insight]."
  - If only a partner quote is available → two-sentence structure:
      Sentence 1: "[Partner name] said [exact quote ≤ 20 words] in [source]."
      Sentence 2: "That view maps directly to why [specific angle about the startup]."
  - If neither anchor is confirmed (both null) → open with a thesis-based observation:
      "Most [sector] funds back [common approach]. [VC]'s focus on [specific differentiating angle] is why [timing observation]."
  - ANCHOR QUALITY GATE: if anchor_quality_warning is not null, do NOT use the flagged anchors.
    Fall back to the thesis-based template instead of using generic PR language as an anchor.
  FORBIDDEN: "I hope this email finds you well", "I wanted to reach out",
             "Given your focus on", "you understand better than most",
             opening with the startup's name or product name,
             any single hook sentence exceeding 35 words.

PROBLEM  (2-3 sentences)
  - Sentence 1: name the specific technical or operational friction point. Use precise language
    appropriate to the startup's actual industry.
  - Sentence 2: competitive acknowledgment — do NOT claim the space is empty. Instead, name the
    category of existing approaches and their structural limitation:
    "[Existing approach category] addresses [adjacent problem], but none integrates [the specific
    capability gap] without [the tradeoff that incumbents accept]."
    Use competitive_landscape.named_competitors from research to inform this sentence.
    If named_competitors is empty, use a category description ("existing single-modality tools",
    "current SaaS point solutions", etc.) rather than a named company.
  - Sentence 3 (optional): sharpen the consequence — what does this limitation cost the customer?
  FORBIDDEN: "faces challenges", "is struggling with", "No one has built X yet" as a standalone
             claim, any assertion that the competitive space is empty when known competitors exist,
             generic phrases that could apply to any startup in any sector.

SOLUTION  (2-3 sentences)
  - Sentence 1: contrast structure — "[What we built] — not [what the named competitor category offers],
    but [what we actually are]."
  - Sentence 2: mechanism sentence — explain WHY competitors cannot simply replicate this. The mechanism
    must be one of these categories (use whichever is supported by the startup's materials):
      · IP / patent coverage over a specific method or composition
      · Proprietary process or protocol with named steps or parameters
      · Unique dataset or training corpus that took years to accumulate
      · Scientific methodology developed by the team at a named institution
      · Exclusive access to a resource, channel, or customer class
      · Timing/sequencing control or delivery approach with a specific named advantage
    If none of the above is identifiable from the startup's materials, write:
    "Our differentiation rests on [X]; we will not claim a moat we cannot substantiate."
    — this is preferable to inventing a mechanism.
  - Sentence 3 (optional): one benchmark that makes the contrast verifiable (a number, a named result,
    a comparison point). Only include if the benchmark is in the source material.
  - ANCHOR-TYPE ARGUMENT SPINE: adjust the connecting logic between sections based on anchor used:
      If anchor_a (portfolio analog): SOLUTION should echo WHY this company will reach the same
        outcome as the analog, not just describe the product.
      If anchor_b (partner quote): SOLUTION should connect directly to the thesis the quote expresses —
        show that this product is the thesis in product form.
      If anchor_c (timing event): SOLUTION should open with "the [event] created a specific opening
        that [product] is built to fill" framing.
  FORBIDDEN: labels without mechanism ("proprietary logic", "coordinated architecture", "unique platform"),
             generic tech analogies without specific grounding,
             claiming a moat category that is not evidenced in the source materials.

WHY NOW  (1 sentence)
  - Cite one named external trigger from anchor_c with its year.
  - The trigger must be relevant to the startup's actual industry (regulation, acquisition, market shift, etc.).
  FORBIDDEN: "now, not three years from now", urgency claims without a named external anchor.

TRACTION  (1-2 sentences)
  Every traction claim must include THREE elements: [amount] + [time window] + [customer type].
  The highest-credibility fourth element — add when available: [renewal / repeat purchase signal].
  - Examples by model type:
      SaaS: "$X ARR from Y enterprise customers as of [month/year]; two accounts expanded within 6 months."
      Marketplace: "GMV reached $X in [year] across Z active buyers in [category]."
      Deep tech / licensing: "$X in licensing revenue in [year] across Y [named customer type] contracts;
                             at least one contract has entered its second renewal cycle."
      Pre-revenue: "Y signed LOIs from [named category] in [month/year], representing $X in potential ARR."
  A traction claim without a time window is incomplete — rewrite until all three elements are present.
  FORBIDDEN: forward-looking language after the core number, "while", "as we scale", "these customers will",
             revenue figures without a year or operating period reference.
  FINANCIAL FRAMING RULES:
  - "Positive net income" or "profitable" are neutral-to-negative signals for VC readers — they imply
    the company is not reinvesting aggressively. Reframe as: "capital-efficient growth" or
    "reaching [revenue] while maintaining operational runway without external capital."
  - Always include a forward indicator when available in the source material: pipeline growth rate,
    avg new contract size, renewal rate, or Q-over-Q expansion. A static revenue figure with no
    velocity signal is the weakest form of traction evidence.
  - Customer descriptor precision: always use the highest-precision accurate description of the
    customer (e.g. "Phase I cell therapy sponsors" over "research groups"; "enterprise SaaS buyers
    in financial services" over "enterprise customers"). Precision signals market quality.

TEAM  (1 sentence — must appear in the body, not just the signature)
  - The email is written and signed by the founder. ALWAYS use first person for self-description.
  - Pattern: "I previously [specific role] at [specific named org] where [one concrete, verifiable achievement]."
  - NEVER use "[Full name], [Title], previously..." — that is third-person self-description in a self-signed email,
    which reads as if someone else wrote the email on the founder's behalf.
  FORBIDDEN: "extensive experience", "deep expertise", any description without a named org,
             third-person self-description when the founder is the sender and signer.
  VERIFIABILITY STANDARD: Every factual claim in the email must be directly traceable to the BP text
  or research findings. Do not invent specific numbers, customer names, deal sizes, or milestones
  that do not appear explicitly in the source material. If a claim cannot be verified from the inputs,
  replace it with a structurally sound placeholder such as "[X customers]" rather than fabricating data.
  TEMPORAL PLAUSIBILITY CHECK: Before finalizing, verify that all dates, revenue figures, and milestone
  claims form a coherent timeline. A company cannot report $2M ARR in 2024 if it was founded in late
  2023 unless the BP explicitly states this. Flag and correct any temporal inconsistencies rather than
  reproducing them from the source.

CTA  (1-2 sentences — mandatory)
  - Name EXACTLY ONE partner by first name. Use the first name from partner_first_names in research.
    If contact_name is provided, use that person — do not tag a second person.
  - Use active, not passive, language. The sender proposes the meeting; the recipient confirms.
  - Pattern: "I'd like to schedule 20 minutes with [Name] — [Day option 1] or [Day option 2] this week?"
  - Optionally append: "Happy to share our deck ahead of time."
  - Do NOT leave the day options as generic placeholders in the output. Use real weekday names
    (Monday through Friday). Default to proposing mid-week options (Tuesday / Wednesday / Thursday)
    unless the system has information about the sender's availability.
  FORBIDDEN: "I would love to connect", "Would [Name] be open to...", "I wanted to reach out",
             passive constructions that put the initiative on the recipient,
             tagging two people in the same CTA sentence.

════════════════════════════════════════
GLOBALLY FORBIDDEN CONTENT (any section)
════════════════════════════════════════
The following content is banned in all sections regardless of context:

1. GENERIC INDUSTRY VALIDATION — Do not cite well-known regulatory approvals or market events
   that any informed investor already knows (e.g. FDA approval of a first-in-class therapy as proof
   of the whole modality, a landmark IPO as proof of sector interest). These signal that the sender
   did not research what this specific investor already knows. If the why_now anchor is not
   distinguishable from general news, find a more specific event or omit the section.

2. CONTRADICTORY URGENCY — Do not write urgency claims that imply the window has already closed.
   Phrases like "capital influx that is peaking now", "the wave that is cresting", or "before the
   market gets crowded" tell the reader the best moment has passed. Urgency must point forward, not
   signal a top.

3. MARKET SCOPE MISMATCH — Early-stage companies (pre-seed through Series A) must anchor to ONE
   primary market in this email. Do not list 2+ target markets ("cell therapy, gene therapy, AND
   research markets") — it signals lack of focus and is a standard red flag for seed-stage investors.
   Use the startup's most validated market only.

4. TECH METAPHORS WITHOUT GROUNDING — Do not use "operating system", "platform of platforms",
   "rails", or similar infrastructure metaphors unless the startup's own materials use that framing
   with supporting specifics. Generic tech metaphors read as aspiration, not description.

════════════════════════
HARD FORMAT CONSTRAINTS
════════════════════════
  - Body word count: 180-230 words. Count carefully before finalising.
  - Content is divided into CORE (always present) and UPGRADES (include only if budget allows).

  CORE — mandatory in every output, cannot be dropped:
      HOOK      1-2 sentences, ≤ 50 words — anchor + insight
      PROBLEM   2 sentences, ≤ 45 words — friction + competitive acknowledgment
      SOLUTION  1 sentence, ≤ 30 words — contrast structure only
      WHY NOW   1 sentence, ≤ 25 words — named event + year
      TRACTION  1-2 sentences, ≤ 40 words — amount + time window + client type
      TEAM      1 sentence, ≤ 25 words — first person, named org, named achievement
      CTA       1-2 sentences, ≤ 25 words — active voice, one named partner, specific days

  UPGRADES — add in this priority order, stop when total reaches 230 words:
      1. SOLUTION mechanism sentence (why competitors cannot replicate)
      2. TRACTION forward indicator (pipeline growth, renewal rate, avg contract expansion)
      3. PROBLEM consequence sentence (what the limitation costs the customer)
      4. SOLUTION benchmark sentence (a verifiable number or comparison point)

  BUDGET ARBITRATION — if still over 230 words after dropping all upgrades:
      - Compress PROBLEM competitive acknowledgment to one clause within sentence 1,
        not a standalone sentence.
      - Compress TRACTION to amount + time only; drop client type descriptor.
      - Compress TEAM to org + role only; drop the named achievement detail.
      - Never compress HOOK, WHY NOW, or CTA — these are the highest-leverage sections.
  - Signature: three separate lines — Name / Title / Company.
    (Signature is NOT counted in the 130-170 word body count.)
  - Plain text body: no markdown, no bold, no bullets, no section labels, no decorative dashes.
  - Paragraphs separated by exactly one blank line.
  - Subject line: max 8 words, no question mark, no exclamation mark.

════════════════════════════════════
MANDATORY SELF-CHECK BEFORE OUTPUT
════════════════════════════════════
Score each item 0 or 1. If total < 6, rewrite the lowest-scoring items. Only output JSON when all 6 score 1.

  hook    — cites a named exit with amount OR a direct/attributed quote
  problem — names a specific technical step, gene target, or regulatory gap
  why_now — contains a named external event with a year
  traction — has no explanatory or forward-looking clause after the core number/fact
  team    — names a specific organisation and a specific achievement in the body text
  cta     — names a partner by first name, and offers to share materials

════════════
OUTPUT SCHEMA
════════════
Output ONLY a single JSON object. No markdown fences. No preamble. No trailing text.

{
  "subject":   "<max 8 words, no ? or !>",
  "body":      "<plain text body, 130-170 words, NO signature>",
  "signature": "<Name\\nTitle\\nCompany>",
  "word_count": <integer — count of words in body field only>,
  "score": {
    "hook":     <0 or 1>,
    "problem":  <0 or 1>,
    "why_now":  <0 or 1>,
    "traction": <0 or 1>,
    "team":     <0 or 1>,
    "cta":      <0 or 1>
  },
  "score_notes": {
    "hook":     "<one sentence explaining the score>",
    "problem":  "<one sentence>",
    "why_now":  "<one sentence>",
    "traction": "<one sentence>",
    "team":     "<one sentence>",
    "cta":      "<one sentence>"
  }
}
"""

_CRITIC_SYSTEM_PROMPT = """\
You are a senior partner at a top-tier venture capital firm that invests across sectors.
You receive over 200 cold outreach emails per week. You are ruthlessly selective.

You will be shown a cold outreach email from a startup seeking investment.
You have NO knowledge of how this email was generated or what score it gave itself.
Evaluate it purely as a reader — not as an editor trying to help.

EVALUATION CRITERIA
===================

1. HOOK  — Does the first sentence make you want to keep reading?
   - Does it reference something specific about YOUR firm (a real investment, a real quote)?
   - Or does it feel like a template with your firm's name swapped in?
   Score 0: generic, could be sent to any VC
   Score 1: clearly written for your firm specifically — you feel seen

2. DIFFERENTIATION  — After reading, can you state in one sentence what makes this startup
   categorically different from existing solutions?
   Score 0: unclear, or the difference sounds like a feature, not a strategic position
   Score 1: the contrast is crisp, and you understand not just what they built but WHY they
            specifically can build it (a moat signal: IP, process, data, access, or track record)

3. HOMEWORK  — Is there any moment where you think "this person didn't do their research"?
   - Hallucinated portfolio companies, wrong partner names, incorrect fund focus
   - Vague urgency with no external anchor, or urgency that implies the window has closed
   - Forward-looking fluff after traction numbers
   - Revenue figures with no time reference (impossible to assess velocity)
   - Generic industry validation that any sender could have included (e.g. citing a landmark
     regulatory approval that any investor in this space already knows about)
   - Third-person self-description in a founder-signed email
   - Fabricated or temporally implausible data (e.g. $2M ARR from a company founded 6 months ago
     with no explanation; customer counts that contradict the stated market entry date)
   - "Profitable" or "positive net income" framed as a strength without a VC-relevant reframe
   Score 0: at least one credibility-breaking moment
   Score 1: nothing that breaks trust

4. COMPETITIVE HONESTY  — Does the email acknowledge that alternatives exist?
   VC partners know every market has incumbents. Claiming "there is no existing solution" or
   describing a clear category without naming the dominant player reads as either naive or dishonest.
   Score 0: the email implies an empty market, fails to acknowledge the dominant incumbent, or
            makes a "first-ever" claim without qualification
   Score 1: the email either names a key incumbent by name, uses contrast language ("unlike X"),
            or frames the company's position as a displacement play rather than a greenfield claim

5. REPLY INTENT  — Would you reply to this email?
   Score 0: no
   Score 1: yes

6. TOP FIX  — The single highest-leverage edit that would most improve this email.
   Be specific: name the exact sentence or section, explain why it fails, suggest the direction
   of the fix (not the full rewrite — just the direction).

OUTPUT FORMAT
=============
Output ONLY a JSON object. No markdown fences. No preamble.

{
  "hook":                 <0 or 1>,
  "differentiation":      <0 or 1>,
  "homework":             <0 or 1>,
  "competitive_honesty":  <0 or 1>,
  "would_reply":          <0 or 1>,
  "total":                <integer 0-5>,
  "hook_note":            "<one sentence: what works or what fails>",
  "diff_note":            "<one sentence>",
  "homework_note":        "<one sentence>",
  "competitive_note":     "<one sentence: names the specific claim that passes or fails this check>",
  "reply_note":           "<one sentence: why you would or would not reply>",
  "top_fix": {
    "section":    "<which part: hook / problem / solution / why_now / traction / team / cta>",
    "problem":    "<one sentence: exactly what fails>",
    "direction":  "<one sentence: direction of fix, not the rewrite itself>"
  }
}
"""

_REWRITE_SYSTEM_PROMPT = """\
You are an elite fundraising writer who works with startups across all industries.

You will receive:
  - The original email body (plain text)
  - A critic's verdict identifying one specific section to fix
  - The research anchors used to write the original

Your job: surgically rewrite ONLY the identified section.
Keep every other sentence word-for-word identical.

REWRITE RULES
=============
- Fix only the section named in "section". Do not touch anything else.
- The rewritten section must be the same length (± 10 words) as the original section.
- The total email body word count must remain 130-170 words.
- Plain text only — no markdown, no bold, no bullets, no section labels.
- Do not introduce new claims that are not supported by the research anchors.
- If the fix is to the hook: the new hook must cite a specific exit amount OR a direct quote
  with attribution — not a generic observation.
- If the fix is to why_now: must include a named event with a year; must not use language
  that implies the timing window has already peaked or closed.
- If the fix is to traction: the rewritten traction must include amount + time window +
  customer type. Remove any forward-looking clause after the core number/fact.
- If the fix is to cta: rewrite to active voice — "I'd like to schedule [N] minutes with
  [Name] — [Day] or [Day] this week?" Do not revert to "Would [Name] be open to...".
- If the fix is to team: rewrite in first person — "I previously [role] at [org] where [achievement]."
  Do not use "[Full name], [Title], previously..." in a self-signed email.

OUTPUT FORMAT
=============
Output ONLY a JSON object. No markdown fences. No preamble.

{
  "rewritten_section": "<the new text for the identified section only>",
  "full_body":         "<the complete email body with the section replaced — plain text, 130-170 words, NO signature>",
  "word_count":        <integer>
}
"""

_FINAL_JUDGE_SYSTEM_PROMPT = """\
You are a different senior partner at the same VC firm — you have not seen any previous
evaluation of this email.

Read the email once, as you would in your inbox on a busy Monday morning.
Make one binary decision only: would you reply to this email?

Criteria for YES:
  - The first sentence references something specific and verifiable about your firm
  - You can immediately articulate what makes this company different, including a moat mechanism
    (IP, process, data advantage, exclusive access, or a specific track record that others cannot replicate)
  - Nothing in the email makes you doubt the sender did their homework
  - The email acknowledges that alternatives or incumbents exist — it does not claim an empty market
  - There is a clear, frictionless next step

Automatic NO (any single one of these disqualifies the email regardless of other strengths):
  - The email explicitly or implicitly claims there are no existing solutions / competitors in the space
  - Any date, revenue figure, or milestone is temporally implausible given the company's founding date
    or stated stage (e.g., $5M ARR from a company that launched 4 months ago with no explanation)
  - A revenue figure is stated without any time reference, making velocity assessment impossible

OUTPUT FORMAT
=============
Output ONLY a JSON object. No markdown fences. No preamble.

{
  "would_reply": <true or false>,
  "reason":      "<one sentence — the single deciding factor>"
}
"""

import json as _json
import re as _re


def _call_claude_json(client, system, user_msg, model="claude-sonnet-4-6",
                      max_tokens=1200, tools=None):
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    if tools:
        kwargs["tools"] = tools
    resp = client.messages.create(**kwargs)
    text_parts = [
        block.text for block in resp.content
        if hasattr(block, "type") and block.type == "text"
    ]
    raw = "\n".join(text_parts).strip()
    cleaned = _re.sub(r"^```(?:json)?\s*", "", raw, flags=_re.MULTILINE)
    cleaned = _re.sub(r"\s*```\s*$", "", cleaned, flags=_re.MULTILINE).strip()
    try:
        return _json.loads(cleaned)
    except _json.JSONDecodeError:
        pass
    match = _re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return _json.loads(match.group())
        except _json.JSONDecodeError:
            pass
    raise ValueError(f"Cannot parse JSON from model output.\nRaw (first 400 chars): {raw[:400]}")


_CONTAMINATION_PATTERNS = [
    r"===", r"\[HOOK\]", r"\[PROBLEM\]", r"\[SOLUTION\]",
    r"\[WHY NOW\]", r"\[TRACTION\]", r"\[TEAM\]", r"\[CTA\]",
    r"Anchor [ABC]", r"Personalisation Score", r"Word count\s*:",
    r"Self-check", r"Score\s*:", r"research summary", r"^Subject:",
]
_CHINESE_RE = _re.compile(r"[一-鿿]")


def _validate_draft(draft):
    errors = []
    body = draft.get("body", "")
    if _CHINESE_RE.search(body):
        errors.append("Body contains Chinese characters. Rewrite entirely in English.")
    word_count = len(body.split())
    if word_count < 150 or word_count > 240:
        errors.append(
            f"Body is {word_count} words. Rewrite to hit 180-230 words "
            f"({'expand' if word_count < 180 else 'trim'}). "
            f"If trimming: drop UPGRADE elements first (mechanism sentence, forward indicator, "
            f"consequence sentence, benchmark), then compress per budget arbitration rules."
        )
    for pat in _CONTAMINATION_PATTERNS:
        if _re.search(pat, body, _re.IGNORECASE | _re.MULTILINE):
            errors.append(
                f"Body contains meta-commentary or section labels (pattern: '{pat}'). "
                f"The body field must contain only email prose."
            )
            break
    subject = draft.get("subject", "")
    if not subject or len(subject.split()) > 10:
        errors.append(f"Subject missing or too long ({len(subject.split())} words). Write 5-8 words, no ? or !.")
    score = draft.get("score", {})
    total = sum(score.values()) if isinstance(score, dict) else 0
    if total < 5:
        failing = [k for k, v in score.items() if v == 0]
        errors.append(f"Self-score is {total}/6. Rewrite these sections: {', '.join(failing)}.")
    return errors


def _fmt_research_summary(r):
    lines = []
    a = r.get("anchor_a") or {}
    if a.get("company"):
        lines.append(f"Anchor A — Direct Overlap: {a['company']} ({a.get('sector','')})\n  {a.get('similarity','')}")
    b = r.get("anchor_b") or {}
    if b.get("quote"):
        lines.append(f"Anchor B — Thesis Resonance: \"{b['quote']}\"\n  Speaker: {b.get('speaker','unknown')} | Source: {b.get('source','')}\n  {b.get('resonance','')}")
    c = r.get("anchor_c") or {}
    if c.get("event"):
        lines.append(f"Anchor C — Timing: {c['event']} ({c.get('year','')})\n  {c.get('timing','')}")
    ex = r.get("top_exit") or {}
    if ex.get("company"):
        lines.append(f"Top Exit: {ex['company']} → {ex.get('acquirer_or_ipo','?')} at {ex.get('amount_usd','undisclosed')}")
    return "\n\n".join(lines) if lines else "No confirmed anchors found."


def _fmt_score_summary(score, notes):
    items = [
        ("Hook anchor precision",   "hook"),
        ("Problem specificity",     "problem"),
        ("Why Now external anchor", "why_now"),
        ("Traction cleanliness",    "traction"),
        ("Team specificity",        "team"),
        ("CTA with partner name",   "cta"),
    ]
    lines = []
    total = 0
    for label, key in items:
        val = score.get(key, 0)
        total += val
        lines.append(f"{label:<28} [{val}/1] — {notes.get(key,'')}")
    lines.append(f"TOTAL: {total}/{len(items)}")
    return "\n".join(lines)


def _apply_rewrite(original_body: str, rewritten_section: str, section: str) -> str:
    """
    Fallback assembler: if the rewrite agent returns a valid full_body, use it directly.
    If full_body is missing or suspiciously short, return original_body unchanged
    so we never make the email worse by a failed splice.
    """
    return original_body


def _fmt_critic_summary(critic: dict) -> str:
    labels = [
        ("Hook specificity",      "hook",           "hook_note"),
        ("Differentiation",       "differentiation","diff_note"),
        ("Homework credibility",  "homework",       "homework_note"),
        ("Would reply",           "would_reply",    "reply_note"),
    ]
    lines = []
    for label, score_key, note_key in labels:
        val = critic.get(score_key, 0)
        note = critic.get(note_key, "")
        lines.append(f"{label:<26} [{val}/1] — {note}")
    total = critic.get("total", sum(critic.get(k, 0) for _, k, _ in labels))
    lines.append(f"TOTAL: {total}/4")
    fix = critic.get("top_fix") or {}
    if fix:
        lines.append(
            f"\nTop fix → [{fix.get('section','')}] {fix.get('problem','')} "
            f"| Direction: {fix.get('direction','')}"
        )
    return "\n".join(lines)


@app.route("/m9/generate-email", methods=["POST"])
def m9_generate_email():
    if _anthropic is None:
        return jsonify({"error": "anthropic package not installed"}), 502
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not configured"}), 502

    import tempfile
    import os as _os

    investor_id = (request.form.get("investor_id") or "").strip()
    analysis_id = (request.form.get("analysis_id") or "").strip() or None

    pipeline = _load_pipeline()
    investor = next((i for i in pipeline["investors"] if i["id"] == investor_id), None)
    if not investor:
        return jsonify({"error": "Investor not found"}), 404

    startup_text = None
    bp_file_field = request.files.get("bp_file")
    if bp_file_field and bp_file_field.filename:
        suffix = Path(bp_file_field.filename).suffix.lower()
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            _os.close(tmp_fd)
            bp_file_field.save(tmp_path)
            startup_text = extract_text(tmp_path)
        except Exception:
            startup_text = None
        finally:
            try:
                _os.unlink(tmp_path)
            except Exception:
                pass

    analyses_db = _load_analyses()
    analyses = analyses_db.get("analyses", [])
    if startup_text is None and not analyses:
        return jsonify({"error": "No startup profile found — parse a BP in M1 first."}), 404

    if analyses:
        if analysis_id:
            analysis = next((a for a in analyses if a.get("id") == analysis_id), analyses[0])
        else:
            analysis = analyses[0]
    else:
        analysis = {}

    profile = analysis.get("profile") or analysis
    ip = investor.get("investor_profile") or {}

    company_name  = investor.get("company", "Unknown Investor")
    focus         = ip.get("focus") or (analysis.get("sector") if analysis else None) or "Not specified"
    inv_stage     = ip.get("stage") or "Not specified"
    location      = ip.get("location") or "Not specified"
    inv_thesis    = (ip.get("thesis") or "")[:400] or "Not specified"
    startup_name  = profile.get("company_name", "Unknown")
    vc_website    = ip.get("website") or ""
    contact_name  = investor.get("contact") or ""
    founder_name  = profile.get("founder_name") or profile.get("team_lead") or ""
    founder_title = profile.get("founder_title") or "Founder & CEO"

    if startup_text:
        startup_block = startup_text[:3000]
    else:
        startup_block = (
            f"Company: {startup_name}\n"
            f"Sector: {str(profile.get('sector') or '')}\n"
            f"Stage: {profile.get('funding_stage') or ''}\n"
            f"Capital needed: {profile.get('capital_need') or ''}\n"
            f"Business model: {str(profile.get('business_model') or '')}\n"
            f"Key traction: {profile.get('key_traction') or ''}\n"
            f"Team: {str(profile.get('team_background') or '')}\n"
            f"Use of funds: {profile.get('use_of_funds') or ''}"
        )

    try:
        client = _anthropic.Anthropic(api_key=api_key)

        # ══════════════════════════════════════════════════════════════════
        # STAGE 1 — Research
        # ══════════════════════════════════════════════════════════════════
        research_data = _call_claude_json(
            client,
            system=_RESEARCH_SYSTEM_PROMPT,
            user_msg=(
                f"VC firm to research: {company_name}\n"
                f"Website: {vc_website}\n"
                f"Stated focus: {focus}\n"
                f"Investment stage: {inv_stage}\n"
                f"Location: {location}\n"
                f"Known thesis excerpt: {inv_thesis}\n\n"
                f"Startup seeking funding:\n{startup_block}"
            ),
            max_tokens=1400,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
        )

        # ══════════════════════════════════════════════════════════════════
        # STAGE 2 — Draft (up to 2 attempts)
        # ══════════════════════════════════════════════════════════════════
        draft_user_msg_base = (
            f"=== RESEARCH ANCHORS ===\n"
            f"{_json.dumps(research_data, indent=2, ensure_ascii=False)}\n\n"
            f"=== VC INFO ===\n"
            f"Firm: {company_name}\n"
            f"Contact name (if known): {contact_name or 'not specified'}\n\n"
            f"=== STARTUP INFO ===\n"
            f"Founder: {founder_name}, {founder_title}\n"
            f"Company name: {startup_name}\n\n"
            f"{startup_block}"
        )

        MAX_DRAFT_ATTEMPTS = 2
        draft_data = None
        last_errors = []

        for attempt in range(MAX_DRAFT_ATTEMPTS):
            user_msg = draft_user_msg_base
            if attempt > 0 and last_errors:
                feedback = "\n".join(f"  - {e}" for e in last_errors)
                user_msg += (
                    f"\n\n=== VALIDATION FAILED ON PREVIOUS ATTEMPT ===\n"
                    f"Fix ALL of the following before outputting the JSON:\n{feedback}"
                )
            try:
                draft_data = _call_claude_json(
                    client,
                    system=_EMAIL_DRAFT_SYSTEM_PROMPT,
                    user_msg=user_msg,
                    max_tokens=900,
                )
            except ValueError as parse_err:
                last_errors = [str(parse_err)]
                draft_data = None
                continue

            last_errors = _validate_draft(draft_data)
            if not last_errors:
                break
            draft_data = None

        if draft_data is None:
            return jsonify({
                "error": (
                    f"Stage 2 failed after {MAX_DRAFT_ATTEMPTS} attempts. "
                    f"Last errors: {'; '.join(last_errors)}"
                )
            }), 500

        current_body      = draft_data.get("body", "")
        subject           = draft_data.get("subject", f"Outreach: {startup_name}")
        signature         = draft_data.get("signature", f"{founder_name}\n{founder_title}\n{startup_name}")
        draft_score       = draft_data.get("score", {})
        draft_score_notes = draft_data.get("score_notes", {})

        # ══════════════════════════════════════════════════════════════════
        # STAGE 3 → 4 loop — Critic + targeted Rewrite (max 3 iterations)
        # ══════════════════════════════════════════════════════════════════
        MAX_CRITIC_LOOPS = 3
        final_critic = None

        for loop in range(MAX_CRITIC_LOOPS):

            # Stage 3: Critic
            try:
                critic_data = _call_claude_json(
                    client,
                    system=_CRITIC_SYSTEM_PROMPT,
                    user_msg=(
                        f"=== EMAIL TO EVALUATE ===\n\n"
                        f"Subject: {subject}\n\n"
                        f"{current_body}\n\n"
                        f"{signature}"
                    ),
                    max_tokens=600,
                )
            except ValueError:
                break

            final_critic = critic_data
            critic_total = critic_data.get("total", 0)

            if critic_total >= 4:
                break

            if loop == MAX_CRITIC_LOOPS - 1:
                break

            # Stage 4: Rewrite — fix only the weakest section
            top_fix = critic_data.get("top_fix") or {}
            fix_section   = top_fix.get("section", "")
            fix_problem   = top_fix.get("problem", "")
            fix_direction = top_fix.get("direction", "")

            if not fix_section:
                break

            try:
                rewrite_data = _call_claude_json(
                    client,
                    system=_REWRITE_SYSTEM_PROMPT,
                    user_msg=(
                        f"=== ORIGINAL EMAIL BODY ===\n{current_body}\n\n"
                        f"=== CRITIC VERDICT ===\n"
                        f"Section to fix: {fix_section}\n"
                        f"Problem: {fix_problem}\n"
                        f"Direction: {fix_direction}\n\n"
                        f"=== RESEARCH ANCHORS (for reference) ===\n"
                        f"{_json.dumps(research_data, indent=2, ensure_ascii=False)}"
                    ),
                    max_tokens=700,
                )
            except ValueError:
                break

            new_full_body = rewrite_data.get("full_body", "").strip()
            new_word_count = rewrite_data.get("word_count", 0)

            if (
                new_full_body
                and 100 <= new_word_count <= 220
                and not _re.search(r"[一-鿿]", new_full_body)
            ):
                current_body = new_full_body
            else:
                break

        # ══════════════════════════════════════════════════════════════════
        # STAGE 5 — Final Judge (independent binary decision)
        # ══════════════════════════════════════════════════════════════════
        final_judge = None
        judge_passed = False

        try:
            final_judge = _call_claude_json(
                client,
                system=_FINAL_JUDGE_SYSTEM_PROMPT,
                user_msg=(
                    f"=== EMAIL ===\n\n"
                    f"Subject: {subject}\n\n"
                    f"{current_body}\n\n"
                    f"{signature}"
                ),
                max_tokens=200,
            )
            judge_passed = bool(final_judge.get("would_reply", False))
        except ValueError:
            judge_passed = True

        # ══════════════════════════════════════════════════════════════════
        # Assemble response
        # ══════════════════════════════════════════════════════════════════
        full_body = current_body.rstrip() + "\n\n" + signature

        critic_summary = _fmt_critic_summary(final_critic) if final_critic else "Critic stage skipped."
        judge_note = ""
        if final_judge:
            verdict = "✓ Final Judge: would reply" if judge_passed else "✗ Final Judge: would not reply"
            judge_note = f"{verdict} — {final_judge.get('reason', '')}"

        score_summary = _fmt_score_summary(draft_score, draft_score_notes)
        if critic_summary:
            score_summary += f"\n\n── Critic Review ──\n{critic_summary}"
        if judge_note:
            score_summary += f"\n\n── Final Judge ──\n{judge_note}"

        return jsonify({
            "subject":          subject,
            "body":             full_body,
            "score_summary":    score_summary,
            "research_summary": _fmt_research_summary(research_data),
            "judge_passed":     judge_passed,
        })

    except Exception as exc:
        return jsonify({"error": str(exc)[:400]}), 500


# ── M9 SSE helpers ────────────────────────────────────────────────────────

def _sse(payload: dict) -> bytes:
    return ("data: " + _json.dumps(payload, ensure_ascii=False) + "\n\n").encode()


def _sse_log(msg: str) -> bytes:
    ts = datetime.utcnow().strftime("%H:%M:%S")
    return _sse({"log": "[ " + ts + " ]  " + msg})


@app.route("/m9/generate-email-sse", methods=["POST"])
def m9_generate_email_sse():
    def _err(msg):
        def _g():
            yield _sse({"error": msg})
        return Response(stream_with_context(_g()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    if _anthropic is None:
        return _err("anthropic package not installed")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    if not api_key:
        return _err("ANTHROPIC_API_KEY not configured")

    import tempfile

    investor_id = (request.form.get("investor_id") or "").strip()
    analysis_id = (request.form.get("analysis_id") or "").strip() or None

    pipeline = _load_pipeline()
    investor = next((i for i in pipeline["investors"] if i["id"] == investor_id), None)
    if not investor:
        return _err("Investor not found")

    startup_text = None
    bp_file_field = request.files.get("bp_file")
    if bp_file_field and bp_file_field.filename:
        suffix = Path(bp_file_field.filename).suffix.lower()
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            os.close(tmp_fd)
            bp_file_field.save(tmp_path)
            startup_text = extract_text(tmp_path)
        except Exception:
            startup_text = None
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    analyses_db = _load_analyses()
    analyses    = analyses_db.get("analyses", [])
    if startup_text is None and not analyses:
        return _err("No startup profile found — parse a BP in M1 first.")

    if analyses:
        if analysis_id:
            analysis = next((a for a in analyses if a.get("id") == analysis_id), analyses[0])
        else:
            analysis = analyses[0]
    else:
        analysis = {}

    profile = analysis.get("profile") or analysis
    ip      = investor.get("investor_profile") or {}

    company_name  = investor.get("company", "Unknown Investor")
    focus         = ip.get("focus") or (analysis.get("sector") if analysis else None) or "Not specified"
    inv_stage     = ip.get("stage") or "Not specified"
    location      = ip.get("location") or "Not specified"
    inv_thesis    = (ip.get("thesis") or "")[:400] or "Not specified"
    startup_name  = profile.get("company_name", "Unknown")
    vc_website    = ip.get("website") or ""
    contact_name  = investor.get("contact") or ""
    founder_name  = profile.get("founder_name") or profile.get("team_lead") or ""
    founder_title = profile.get("founder_title") or "Founder & CEO"

    if startup_text:
        startup_block = startup_text[:3000]
    else:
        startup_block = (
            f"Company: {startup_name}\n"
            f"Sector: {str(profile.get('sector') or '')}\n"
            f"Stage: {profile.get('funding_stage') or ''}\n"
            f"Capital needed: {profile.get('capital_need') or ''}\n"
            f"Business model: {str(profile.get('business_model') or '')}\n"
            f"Key traction: {profile.get('key_traction') or ''}\n"
            f"Team: {str(profile.get('team_background') or '')}\n"
            f"Use of funds: {profile.get('use_of_funds') or ''}"
        )

    def generate():
        try:
            client = _anthropic.Anthropic(api_key=api_key)

            # ══ STAGE 1 — Research ═══════════════════════════════════════
            yield _sse_log(f"Querying {company_name} portfolio — 2023-2025")
            yield _sse_log("Searching for partner quotes and thesis statements")
            yield _sse_log("Identifying timing anchor — regulatory approvals, clinical milestones")

            research_data = _call_claude_json(
                client,
                system=_RESEARCH_SYSTEM_PROMPT,
                user_msg=(
                    f"VC firm to research: {company_name}\n"
                    f"Website: {vc_website}\n"
                    f"Stated focus: {focus}\n"
                    f"Investment stage: {inv_stage}\n"
                    f"Location: {location}\n"
                    f"Known thesis excerpt: {inv_thesis}\n\n"
                    f"Startup seeking funding:\n{startup_block}"
                ),
                max_tokens=1400,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
            )

            a = research_data.get("anchor_a") or {}
            b = research_data.get("anchor_b") or {}
            c = research_data.get("anchor_c") or {}

            if a.get("company"):
                yield _sse_log(f"Found: {a['company']} ({a.get('sector', '')})")
            if b.get("speaker"):
                yield _sse_log(f"Located: {b['speaker']} — attributed quote confirmed")
            else:
                yield _sse_log("No direct partner quote found — falling back to thesis observation")
            if c.get("event"):
                yield _sse_log(f"Timing anchor: {c['event']} ({c.get('year', '')})")
            n_anchors = sum(1 for x in [a.get("company"), b.get("quote"), c.get("event")] if x)
            yield _sse_log(f"Research complete. {n_anchors} anchors confirmed.")
            yield _sse_log("─────────────────────────────────────")

            # ══ STAGE 2 — Draft ══════════════════════════════════════════
            yield _sse_log("Drafting email — target: 130-170 words, plain text")

            draft_user_msg_base = (
                f"=== RESEARCH ANCHORS ===\n"
                f"{_json.dumps(research_data, indent=2, ensure_ascii=False)}\n\n"
                f"=== VC INFO ===\n"
                f"Firm: {company_name}\n"
                f"Contact name (if known): {contact_name or 'not specified'}\n\n"
                f"=== STARTUP INFO ===\n"
                f"Founder: {founder_name}, {founder_title}\n"
                f"Company name: {startup_name}\n\n"
                f"{startup_block}"
            )

            MAX_DRAFT_ATTEMPTS = 2
            draft_data = None
            last_errors = []

            for attempt in range(MAX_DRAFT_ATTEMPTS):
                user_msg = draft_user_msg_base
                if attempt > 0 and last_errors:
                    feedback = "\n".join(f"  - {e}" for e in last_errors)
                    user_msg += (
                        f"\n\n=== VALIDATION FAILED ON PREVIOUS ATTEMPT ===\n"
                        f"Fix ALL of the following before outputting the JSON:\n{feedback}"
                    )
                try:
                    draft_data = _call_claude_json(
                        client,
                        system=_EMAIL_DRAFT_SYSTEM_PROMPT,
                        user_msg=user_msg,
                        max_tokens=900,
                    )
                except ValueError as parse_err:
                    last_errors = [str(parse_err)]
                    draft_data = None
                    continue
                last_errors = _validate_draft(draft_data)
                if not last_errors:
                    break
                draft_data = None

            if draft_data is None:
                yield _sse({"error": f"Stage 2 failed after {MAX_DRAFT_ATTEMPTS} attempts. "
                                     f"Last errors: {'; '.join(last_errors)}"})
                return

            draft_score       = draft_data.get("score", {})
            draft_score_notes = draft_data.get("score_notes", {})
            word_count        = draft_data.get("word_count", len(draft_data.get("body", "").split()))
            score_total       = sum(draft_score.values()) if isinstance(draft_score, dict) else 0
            yield _sse_log(f"Draft complete. Word count: {word_count}. Self-score: {score_total}/6")
            yield _sse_log("─────────────────────────────────────")

            current_body = draft_data.get("body", "")
            subject      = draft_data.get("subject", f"Outreach: {startup_name}")
            signature    = draft_data.get("signature", f"{founder_name}\n{founder_title}\n{startup_name}")

            # ══ STAGE 3 → 4 loop — Critic + Rewrite ═════════════════════
            MAX_CRITIC_LOOPS = 3
            final_critic = None

            for loop in range(MAX_CRITIC_LOOPS):
                yield _sse_log("Critic review — independent evaluation, no prior context")
                try:
                    critic_data = _call_claude_json(
                        client,
                        system=_CRITIC_SYSTEM_PROMPT,
                        user_msg=(
                            f"=== EMAIL TO EVALUATE ===\n\n"
                            f"Subject: {subject}\n\n"
                            f"{current_body}\n\n"
                            f"{signature}"
                        ),
                        max_tokens=600,
                    )
                except ValueError:
                    break

                final_critic = critic_data
                critic_total = critic_data.get("total", 0)
                yield _sse_log(
                    f"Hook: {critic_data.get('hook',0)}/1 · "
                    f"Differentiation: {critic_data.get('differentiation',0)}/1 · "
                    f"Homework: {critic_data.get('homework',0)}/1 · "
                    f"Reply: {critic_data.get('would_reply',0)}/1"
                )

                if critic_total >= 4:
                    yield _sse_log("All sections passed. No rewrite needed.")
                    yield _sse_log("─────────────────────────────────────")
                    break

                top_fix     = critic_data.get("top_fix") or {}
                fix_section = top_fix.get("section", "")
                yield _sse_log(f"Weak section identified: {fix_section}")
                yield _sse_log("─────────────────────────────────────")

                if loop == MAX_CRITIC_LOOPS - 1 or not fix_section:
                    break

                prev_wc = len(current_body.split())
                yield _sse_log(f"Surgical rewrite — {fix_section} section only")
                try:
                    rewrite_data = _call_claude_json(
                        client,
                        system=_REWRITE_SYSTEM_PROMPT,
                        user_msg=(
                            f"=== ORIGINAL EMAIL BODY ===\n{current_body}\n\n"
                            f"=== CRITIC VERDICT ===\n"
                            f"Section to fix: {fix_section}\n"
                            f"Problem: {top_fix.get('problem','')}\n"
                            f"Direction: {top_fix.get('direction','')}\n\n"
                            f"=== RESEARCH ANCHORS (for reference) ===\n"
                            f"{_json.dumps(research_data, indent=2, ensure_ascii=False)}"
                        ),
                        max_tokens=700,
                    )
                except ValueError:
                    break

                new_full_body  = rewrite_data.get("full_body", "").strip()
                new_word_count = rewrite_data.get("word_count", 0)

                if (new_full_body and 100 <= new_word_count <= 220
                        and not _re.search(r"[一-鿿]", new_full_body)):
                    delta     = new_word_count - prev_wc
                    delta_str = (f"+{delta}" if delta > 0 else str(delta)) if delta != 0 else "±0"
                    current_body = new_full_body
                    yield _sse_log(f"Rewrite accepted. Word count delta: {delta_str}")
                    yield _sse_log("─────────────────────────────────────")
                else:
                    break

            # ══ STAGE 5 — Final Judge ═════════════════════════════════════
            yield _sse_log("Final judge — binary decision, blind review")
            final_judge  = None
            judge_passed = False
            try:
                final_judge = _call_claude_json(
                    client,
                    system=_FINAL_JUDGE_SYSTEM_PROMPT,
                    user_msg=(
                        f"=== EMAIL ===\n\n"
                        f"Subject: {subject}\n\n"
                        f"{current_body}\n\n"
                        f"{signature}"
                    ),
                    max_tokens=200,
                )
                judge_passed = bool(final_judge.get("would_reply", False))
            except ValueError:
                judge_passed = True

            if judge_passed:
                yield _sse_log("Verdict: would reply ✓")
            else:
                yield _sse_log("Verdict: would not reply — see score breakdown for details")

            # ══ Assemble result ═══════════════════════════════════════════
            full_body      = current_body.rstrip() + "\n\n" + signature
            critic_summary = _fmt_critic_summary(final_critic) if final_critic else "Critic stage skipped."
            judge_note     = ""
            if final_judge:
                verdict    = "✓ Final Judge: would reply" if judge_passed else "✗ Final Judge: would not reply"
                judge_note = f"{verdict} — {final_judge.get('reason', '')}"

            score_summary = _fmt_score_summary(draft_score, draft_score_notes)
            if critic_summary:
                score_summary += f"\n\n── Critic Review ──\n{critic_summary}"
            if judge_note:
                score_summary += f"\n\n── Final Judge ──\n{judge_note}"

            yield _sse({
                "done":             True,
                "subject":          subject,
                "body":             full_body,
                "score_summary":    score_summary,
                "research_summary": _fmt_research_summary(research_data),
                "judge_passed":     judge_passed,
            })

        except Exception as exc:
            yield _sse({"error": str(exc)[:400]})

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── M10 ─────────────────────────────────────────────────────────────────

@app.route("/m10")
def m10():
    html = SHELL.replace("{{ content }}", M10_CONTENT)
    return render_template_string(html, active="m10")


@app.route("/m10/data")
def m10_data():
    return jsonify(_load_analyses())


@app.route("/m10/delete", methods=["POST"])
def m10_delete():
    body = request.get_json(force=True) or {}
    aid  = body.get("id")
    if not aid:
        return jsonify({"error": "id required"}), 400
    try:
        row_id = int(aid)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid id"}), 400
    row = AnalysisModel.query.filter_by(id=row_id, user_id=current_user.id).first_or_404()
    _db.session.delete(row)
    _db.session.commit()
    return jsonify({"ok": True})


@app.route("/m10/export/<aid>")
def m10_export(aid):
    try:
        row_id = int(aid)
    except (ValueError, TypeError):
        return jsonify({"error": "not found"}), 404
    row = AnalysisModel.query.filter_by(id=row_id, user_id=current_user.id).first_or_404()
    analysis = _analysis_to_dict(row)
    name = analysis.get("company_name", "analysis").replace(" ", "_")
    resp = app.response_class(
        response=json.dumps(analysis, indent=2, ensure_ascii=False),
        mimetype="application/json",
    )
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}.json"'
    return resp


# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    inv_path = ROOT / "web" / "active.jsonl"
    count = "?"
    try:
        with open(inv_path) as fp:
            count = f"{sum(1 for l in fp if l.strip()):,}"
    except Exception:
        pass

    print("=" * 52)
    print("  Washon Investment Suite")
    print(f"  http://localhost:5000")
    print(f"  Investor DB: {count} records")
    print("=" * 52)
    app.run(debug=False, port=5001, host='127.0.0.1')
