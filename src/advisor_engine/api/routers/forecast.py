"""The firm's financial forecast, from the forecast engine service.

The forecast engine (GaltonKS/forecast-engine) computes every figure; these routes pass
requests to it for the signed-in adviser's firm (`forecast_client`) and return its
answers unchanged. Uploads and runs are audited like other engine runs.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..deps import Ctx, ctx, log
from ..forecast_client import ForecastClient, ForecastRefused, ForecastUnavailable

router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # the forecast engine's own default limit


class ForecastOverrides(BaseModel):
    revenue_base: float | None = None
    revenue_growth: float | None = None
    gross_margin: float | None = Field(default=None, ge=-1, le=1)
    opex_base: float | None = Field(default=None, ge=0)
    opex_growth: float | None = None
    other_net: float | None = None


class ForecastRunBody(BaseModel):
    """The forecast engine's request, checked here too so a bad one never leaves the API."""
    horizon_months: int = Field(default=12, ge=1, le=120)
    overrides: ForecastOverrides = ForecastOverrides()
    method: Literal["auto", "driver", "seasonal_naive", "ets", "arima", "prophet"] = "auto"
    seasonal: bool | None = None


def get_forecast(request: Request) -> ForecastClient:
    return request.app.state.forecast


def _call(fn, *args):
    try:
        return fn(*args)
    except ForecastUnavailable as e:
        raise HTTPException(e.status, e.message) from None
    except ForecastRefused as e:
        raise HTTPException(e.status, e.detail) from None


@router.get("/forecast")
def forecast_status(c: Ctx = Depends(ctx), fc: ForecastClient = Depends(get_forecast)) -> dict:
    """Whether the forecast engine is connected, and its version."""
    if not fc.configured:
        return {"configured": False, "reachable": False, "engine_version": None}
    try:
        h = fc.health()
    except (ForecastUnavailable, ForecastRefused):
        return {"configured": True, "reachable": False, "engine_version": None}
    return {"configured": True, "reachable": True, "engine_version": h.get("engine_version")}


@router.post("/forecast/actuals")
async def forecast_upload(file: UploadFile = File(...), c: Ctx = Depends(ctx),
                          fc: ForecastClient = Depends(get_forecast)) -> dict:
    """Upload monthly actuals (period, account, category, amount). Re-uploading a month
    replaces that account's amount, so corrections are safe."""
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (10 MB at most).")
    summary = _call(fc.upload, c.adv["firm_id"], file.filename or "actuals.csv", data)
    log(c, "forecast_upload", rows_received=summary.get("rows_received"),
        months_in_history=summary.get("months_in_history"))
    return summary


@router.get("/forecast/actuals/monthly")
def forecast_monthly(c: Ctx = Depends(ctx), fc: ForecastClient = Depends(get_forecast)) -> list[dict]:
    return _call(fc.monthly, c.adv["firm_id"])


@router.post("/forecast/runs", status_code=201)
def forecast_run(body: ForecastRunBody, c: Ctx = Depends(ctx),
                 fc: ForecastClient = Depends(get_forecast)) -> dict:
    """Run and save a forecast. Overrides model a scenario; the engine computes it."""
    req = body.model_dump(exclude_none=False)
    req["overrides"] = body.overrides.model_dump(exclude_none=True)
    result = _call(fc.run, c.adv["firm_id"], req)
    log(c, "forecast_run", run_id=result.get("id"), horizon_months=body.horizon_months,
        method=body.method, overrides=req["overrides"],
        engine={"forecast_engine_version": result.get("engine_version")})
    return result


@router.get("/forecast/runs")
def forecast_runs(c: Ctx = Depends(ctx), fc: ForecastClient = Depends(get_forecast)) -> list[dict]:
    return _call(fc.list_runs, c.adv["firm_id"])


@router.get("/forecast/runs/{run_id}")
def forecast_get_run(run_id: str, c: Ctx = Depends(ctx),
                     fc: ForecastClient = Depends(get_forecast)) -> dict:
    return _call(fc.get_run, c.adv["firm_id"], run_id)
