"""Local operator forms for the fixed controlled-action simulator.

Per-process form token + exact loopback Host + same-origin/fetch checks protect
these new mutation routes against cross-site requests. This is not a login or
host-compromise defense. Existing legacy routes are outside this change.
"""
import hmac
import secrets
import sqlite3
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from verity_core.trust import decision
from . import config, decision_runs

TOKEN = secrets.token_urlsafe(32)
RUNS = None


def runs():
    global RUNS
    if RUNS is None:
        RUNS = decision_runs.RunStore()
    return RUNS


def local_request(request):
    if request.url.hostname not in ('127.0.0.1', 'localhost'):
        raise HTTPException(403, 'Governed decisions require a loopback host.')
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Cross-site decision request refused.')
    origin = request.headers.get('origin')
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != request.url.scheme or parsed.netloc != request.url.netloc or parsed.path or parsed.query or parsed.fragment:
            raise HTTPException(403, 'Same-origin operator request required.')


async def operator_form(request):
    local_request(request)
    # Form size is constrained by Content-Length plus actual bounded streaming,
    # rather than trusting a client-supplied length or parsing arbitrary files.
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 8192:
            raise HTTPException(413, 'Decision form too large.')
    from urllib.parse import parse_qsl
    if request.headers.get('content-type', '').split(';')[0] != 'application/x-www-form-urlencoded':
        raise HTTPException(415, 'A URL-encoded operator form is required.')
    try:
        pairs = parse_qsl(data.decode('utf-8'), keep_blank_values=True, strict_parsing=True, max_num_fields=10)
    except (ValueError, UnicodeError):
        raise HTTPException(400, 'Invalid operator form.') from None
    form = dict(pairs)
    if len(form) != len(pairs):
        raise HTTPException(400, 'Duplicate form fields refused.')
    if not hmac.compare_digest(form.get('operator_token', '').encode(), TOKEN.encode()):
        raise HTTPException(403, 'Operator form expired or missing. Reload the run.')
    return form


def install(app, render, base_ctx, jobs):
    router = APIRouter()

    def page(request, template, **values):
        local_request(request)
        ctx = base_ctx(request, 'governance')
        ctx.update(operator_token=TOKEN, **values)
        response = render(template, ctx)
        response.headers["Cache-Control"] = "no-store"
        return response

    def get(rid):
        try:
            return runs().get(rid)
        except decision_runs.NotFound:
            raise HTTPException(404, 'Decision run not found.') from None
        except (ValueError, TypeError, AttributeError, sqlite3.Error, OSError):
            raise HTTPException(503, 'Run integrity or storage could not be verified. No action allowed.') from None

    @router.get('/decisions')
    def index(request: Request):
        try:
            views = runs().recent()
        except (sqlite3.Error, OSError):
            raise HTTPException(503, 'Decision storage is unavailable.') from None
        return page(request, 'decisions.html', runs=views)

    @router.post('/decisions/demo')
    async def create_demo(request: Request):
        form = await operator_form(request)
        try:
            view = runs().create(decision_runs.synthetic_manifest(form.get('policy_mode', 'require_approval')))
        except ValueError:
            raise HTTPException(422, 'Unknown simulation policy.') from None
        except (sqlite3.Error, OSError):
            raise HTTPException(503, 'Decision could not be durably created.') from None
        return RedirectResponse('/decisions/' + view['id'], status_code=303)

    @router.get('/decisions/from-report/{token}')
    def from_report(request: Request, token: str):
        job = jobs.get(token)
        if not job or 'decision_participant' not in job:
            raise HTTPException(404, 'Successful single-analysis snapshot no longer available.')
        return page(request, 'decision_intake.html', report_id=token)

    @router.post('/decisions/from-report/{token}')
    async def create_from_report(request: Request, token: str):
        form = await operator_form(request)
        job = jobs.get(token)
        if not job or 'decision_participant' not in job:
            raise HTTPException(404, 'Successful single-analysis snapshot no longer available.')
        try:
            view = runs().create(decision_runs.analysis_manifest(job, form.get('policy_mode', 'require_approval')))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(422, 'Analysis cannot be preserved as a decision run.') from None
        except (sqlite3.Error, OSError):
            raise HTTPException(503, 'Decision could not be durably created.') from None
        return RedirectResponse('/decisions/' + view['id'], status_code=303)

    @router.get('/decisions/{rid}')
    def detail(request: Request, rid: str):
        return page(request, 'decision.html', run=get(rid))

    @router.get('/decisions/{rid}/export.json')
    def export_run(request: Request, rid: str):
        local_request(request)
        view = get(rid)
        return JSONResponse(decision.export(view['record']), headers={
            'Content-Disposition': f'attachment; filename="{view["id"]}.decision.json"',
            'Cache-Control': 'no-store'})

    @router.get('/decisions/{rid}/checkpoint.json')
    def export_checkpoint(request: Request, rid: str):
        local_request(request)
        view = get(rid)
        return JSONResponse(decision.checkpoint(view['record']), headers={
            'Content-Disposition': f'attachment; filename="{view["id"]}.checkpoint.json"',
            'Cache-Control': 'no-store'})

    async def mutate(request, rid, operation):
        form = await operator_form(request)
        allowed_fields = {'operator_token','expected_head','decision','reason'} if operation == 'authorize' else {'operator_token','expected_head'}
        if set(form)-allowed_fields:raise HTTPException(422, 'Unexpected action parameters refused.')
        get(rid)  # verify before processing mutation; store re-verifies under lock
        try:
            if operation == 'authorize':
                view = runs().authorize(rid, form.get('decision'), form.get('reason'),
                                        config.OPERATOR, form.get('expected_head'))
                return RedirectResponse('/decisions/' + view['id'], status_code=303)
            view, allowed = runs().execute(rid, config.OPERATOR, form.get('expected_head'))
            response = page(request, 'decision.html', run=view,
                            message='Simulation completed.' if allowed else 'Execution denied by the backend. The attempt was recorded.')
            response.status_code = 200 if allowed else 403
            return response
        except decision_runs.Conflict as e:
            raise HTTPException(409, str(e)) from None
        except (ValueError, TypeError):
            raise HTTPException(422, 'Invalid decision input or integrity failure. No action allowed.') from None
        except (sqlite3.Error, OSError):
            raise HTTPException(503, 'Decision could not be durably recorded. No simulation committed.') from None

    @router.post('/decisions/{rid}/authorization')
    async def authorize(request: Request, rid: str):
        return await mutate(request, rid, 'authorize')

    @router.post('/decisions/{rid}/execute')
    async def execute(request: Request, rid: str):
        return await mutate(request, rid, 'execute')

    app.include_router(router)
