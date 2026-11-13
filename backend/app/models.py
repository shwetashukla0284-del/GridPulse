from datetime import datetime
from math import isclose

from pydantic import BaseModel, Field, model_validator


class ReadingMessage(BaseModel):
    meter_id: str = Field(min_length=1)
    timestamp: datetime
    demand_kw: float = Field(ge=0, allow_inf_nan=False)
    solar_kw: float = Field(ge=0, allow_inf_nan=False)
    net_grid_kw: float = Field(allow_inf_nan=False)
    is_fault: bool

    @model_validator(mode="after")
    def validate_reading(self) -> "ReadingMessage":
        if self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must include a timezone offset")
        if not isclose(
            self.net_grid_kw,
            self.demand_kw - self.solar_kw,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError("net_grid_kw must equal demand_kw minus solar_kw")
        return self


class HistoryBucket(BaseModel):
    """System-wide (all meters summed) power averaged over one time bucket."""

    bucket_start: datetime
    avg_demand_kw: float
    avg_solar_kw: float
    avg_net_grid_kw: float
    max_net_grid_kw: float
    reading_count: int
    fault_count: int


class AnomalyAlert(BaseModel):
    """A demand reading the detector judged unusual for its meter."""

    meter_id: str = Field(min_length=1)
    timestamp: datetime
    demand_kw: float = Field(allow_inf_nan=False)
    baseline_mean_kw: float = Field(allow_inf_nan=False)
    baseline_std_kw: float = Field(gt=0, allow_inf_nan=False)
    z_score: float = Field(allow_inf_nan=False)


class KpiSummary(BaseModel):
    """Energy and load indicators for one time range (energy in kWh)."""

    start: datetime
    end: datetime
    meter_count: int
    reading_count: int
    fault_count: int
    demand_kwh: float
    solar_kwh: float
    import_kwh: float
    export_kwh: float
    solar_coverage_pct: float | None
    peak_net_grid_kw: float | None
    peak_at: datetime | None
