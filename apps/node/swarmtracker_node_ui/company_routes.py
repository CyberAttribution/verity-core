"""Read-only company research trace; no implied operator approval."""
from fastapi import Request
from . import decision_routes, store
from .company_work import CompanyWorkStore


def database():
    return CompanyWorkStore(store.DB_PATH.with_name("company-work.db"))


def install(app,render,base_ctx):
    @app.get("/company-work")
    def page(request:Request):
        decision_routes.local_request(request)
        ctx=base_ctx(request,"company_work")
        ctx["company_work_records"]=database().recent()
        response=render("company_work.html",ctx)
        response.headers["Cache-Control"]="no-store"
        return response
