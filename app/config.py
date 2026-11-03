"""Runtime configuration, read from environment variables (see .env.example)."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: str
    admin_token: str
    upi_id: str
    upi_payee_name: str
    platform_fee_inr: int


def get_settings() -> Settings:
    """Read settings at call time so tests can override env vars."""
    return Settings(
        db_path=os.getenv("BIKEPOOL_DB", str(Path("data") / "bikepool.db")),
        # No default on purpose: admin endpoints stay locked until a token is set.
        admin_token=os.getenv("ADMIN_TOKEN", ""),
        upi_id=os.getenv("PLATFORM_UPI_ID", "bikepool@upi"),
        upi_payee_name=os.getenv("PLATFORM_PAYEE_NAME", "BikePool"),
        platform_fee_inr=int(os.getenv("PLATFORM_FEE_INR", "199")),
    )
