"""Azure Functions entry point (Python v2 programming model): trigger declarations only; the logic is in
shell_azure.handlers. The deployed package's function_app.py imports `app` from here (deploy/function_app.py).

  api              HTTP  /api/{*route}   Flask JSON API (Easy Auth + in-app token check, app.api.auth)
  admin            HTTP  /ops/{job}      function key; excluded from Easy Auth (works before any app user exists)
  validate_worker  queue validate-jobs   one VALIDATE request per message
  generate_worker  queue generate-jobs   one GENERATE request per message
  recovery         timer every 15 minutes
  retention        timer 02:00 IST (20:30 UTC); switched off: nothing is removed (owner 2026-11-06)

Unchanged from the reference app: the same functions, routes, queues and schedules, so the Function App's settings,
Easy Auth exclusions and alerts keep working. host.json sets routePrefix "": the API's catch-all would otherwise also
swallow the admin route (/ops, not /admin: /admin/... is reserved for the Functions host).
"""

import json
import logging

import azure.functions as func

from shell_azure import handlers

app = func.FunctionApp()


@app.function_name("api")
@app.route(route="api/{*route}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], auth_level=func.AuthLevel.ANONYMOUS)
def api(req: func.HttpRequest, context: func.Context) -> func.HttpResponse:
    response: func.HttpResponse = func.WsgiMiddleware(handlers.flask_app().wsgi_app).handle(req, context)  # type: ignore[no-untyped-call]
    return response


@app.function_name("admin")
@app.route(route="ops/{job}", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def admin(req: func.HttpRequest) -> func.HttpResponse:
    """POST /ops/<job>?code=<function key>, JSON body for users / views / retention,
    e.g. {"args": ["add", "asha.k", "--email", "asha.k@company.in", "--name", "Asha K", "--admin"]}."""
    try:
        body = req.get_json() if req.get_body() else {}
        result = handlers.jobs({**(body if isinstance(body, dict) else {}), "job": req.route_params.get("job")})
        status = 200
    except ValueError as exc:
        result, status = {"error": str(exc)}, 400
    return func.HttpResponse(json.dumps(result, default=str), status_code=status, mimetype="application/json")


@app.function_name("validate_worker")
@app.queue_trigger(arg_name="msg", queue_name="validate-jobs", connection="AzureWebJobsStorage")
def validate_worker(msg: func.QueueMessage) -> None:
    logging.info("validate_worker: %s", handlers.worker(msg.get_body().decode()))


@app.function_name("generate_worker")
@app.queue_trigger(arg_name="msg", queue_name="generate-jobs", connection="AzureWebJobsStorage")
def generate_worker(msg: func.QueueMessage) -> None:
    logging.info("generate_worker: %s", handlers.worker(msg.get_body().decode()))


@app.function_name("recovery")
@app.timer_trigger(arg_name="timer", schedule="0 */15 * * * *", run_on_startup=False)
def recovery(timer: func.TimerRequest) -> None:
    logging.info("recovery: %s", handlers.jobs({"job": "recovery"}, actor="recovery"))


@app.function_name("retention")
@app.timer_trigger(arg_name="timer", schedule="0 30 20 * * *", run_on_startup=False)  # UTC = 02:00 IST
def retention(timer: func.TimerRequest) -> None:
    # Off (owner decision 2026-11-06): input files, outputs and database data are all kept indefinitely until a
    # retention policy is agreed. The timer stays declared so the Function App's functions and alerts are unchanged;
    # the purge can still be run by hand (`ops.ps1 retention --dry-run` first).
    logging.info("retention: off, nothing removed (keep everything until a retention policy is agreed)")
