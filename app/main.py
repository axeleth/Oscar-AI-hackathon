"""FastAPI app: task runner, approval queue, audit log. Run: uvicorn app.main:app --reload"""
import csv
import io
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from . import agent, audit, config, policy
from .tools import REGISTRY

templates = Jinja2Templates(directory=str(config.ROOT / "app" / "templates"))
templates.env.globals["fmt_money"] = policy.fmt_money


def _config_error() -> str | None:
    try:
        config.assert_test_mode()
    except RuntimeError as e:
        return str(e)
    if not config.ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY is not set (see .env.example)"
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.config_error = _config_error()
    yield


app = FastAPI(title="Oscar", lifespan=lifespan)


def _ctx(request: Request, **kw) -> dict:
    p, version = policy.load_policy()
    runs = sorted(agent.RUNS.values(), key=lambda r: r.created, reverse=True)
    return {"request": request, "config_error": request.app.state.config_error, "policy": p,
            "policy_version": version, "runs": runs, "model": config.MODEL, **kw}


def _run_version(run) -> str:
    return f"{run.status}:{len(run.transcript)}:{len(run.pending)}" if run else "none"


@app.get("/", response_class=HTMLResponse)
def index(request: Request, run: str | None = None):
    current = agent.RUNS.get(run) if run else None
    return templates.TemplateResponse(request, "index.html", _ctx(request, current=current, v=_run_version(current),
                                                                  pending=agent.pending_approvals()))


@app.post("/runs")
def create_run(request: Request, task: str = Form(...)):
    if request.app.state.config_error:
        return RedirectResponse("/", status_code=303)
    run = agent.start_run(task.strip())
    return RedirectResponse(f"/?run={run.id}", status_code=303)


# ---- htmx partials (polled) --------------------------------------------------
# Polled boxes send ?v=<version they show>; 204 means "unchanged", so htmx skips the swap
# (no flicker, scroll position and text selection survive).

@app.get("/partials/run/{run_id}", response_class=HTMLResponse)
def run_partial(request: Request, run_id: str, v: str = ""):
    run = agent.RUNS.get(run_id)
    version = _run_version(run)
    if v == version:
        return Response(status_code=204)
    return templates.TemplateResponse(request, "partials/run.html", _ctx(request, current=run, v=version))


@app.get("/partials/runs", response_class=HTMLResponse)
def runs_partial(request: Request, current: str | None = None):
    return templates.TemplateResponse(request, "partials/runs.html",
                                      _ctx(request, current=agent.RUNS.get(current) if current else None))


@app.get("/partials/approvals", response_class=HTMLResponse)
def approvals_partial(request: Request):
    return templates.TemplateResponse(request, "partials/approvals.html",
                                      _ctx(request, pending=agent.pending_approvals()))


@app.get("/partials/audit", response_class=HTMLResponse)
def audit_partial(request: Request, run_id: str | None = None, v: str = ""):
    rows = audit.query(run_id=run_id or None, limit=150)
    ok, n, bad = audit.verify_chain()
    version = f"{rows[0]['id'] if rows else 0}:{n}:{ok}"
    if v == version:
        return Response(status_code=204)
    for r in rows:
        r["rules_list"] = json.loads(r["rules"]) if r.get("rules") else []
    return templates.TemplateResponse(request, "partials/audit.html",
                                      _ctx(request, rows=rows, chain_ok=ok, chain_n=n, chain_bad=bad, run_id=run_id,
                                           v=version))


# ---- actions ------------------------------------------------------------------

@app.post("/approvals/{run_id}/{tool_use_id}", response_class=HTMLResponse)
def decide(request: Request, run_id: str, tool_use_id: str, decision: str = Form(...),
           approver: str = Form("human"), comment: str = Form("")):
    if run_id in agent.RUNS:
        agent.resolve_approval(run_id, tool_use_id, decision == "approve", approver, comment)
    return approvals_partial(request)


# ---- JSON / exports -------------------------------------------------------------

@app.get("/api/runs/{run_id}")
def run_json(run_id: str):
    r = agent.RUNS.get(run_id)
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"id": r.id, "task": r.task, "status": r.status, "final": r.final, "transcript": r.transcript,
            "pending": [{"tool_use_id": p.tool_use_id, "tool": p.tool, "args": p.args,
                         "reasons": p.decision.reasons} for p in r.pending.values()]}


@app.get("/api/audit/verify")
def verify():
    ok, n, bad = audit.verify_chain()
    return {"ok": ok, "rows_checked": n, "first_bad_id": bad}


@app.get("/api/tools")
def list_tools():
    return [{"name": t.name, "category": t.category, "money": t.money, "description": t.description}
            for t in REGISTRY.values()]


@app.get("/audit.csv")
def audit_csv(run_id: str | None = None):
    rows = list(reversed(audit.query(run_id=run_id, limit=100000)))
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    name = f"oscar-audit-{run_id or 'all'}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={name}"})
