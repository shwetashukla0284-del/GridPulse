from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MeterReading:
    meter_id: str
    timestamp: datetime
    demand_kw: float
    solar_kw: float
    is_fault: bool = False

    @property
    def net_grid_kw(self) -> float:
        """Return positive power when importing and negative power when exporting."""
        return self.demand_kw - self.solar_kw
