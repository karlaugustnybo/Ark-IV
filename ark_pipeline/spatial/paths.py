"""Shared selection of installed global boundaries and the bundled preview."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADM2_ROOT = ROOT / "data/boundaries/adm2"


def municipality_geometry_path() -> Path:
    selected = os.environ.get('GLOBAL_ADM2_ROOT')
    current = Path(selected or ADM2_ROOT) / "current/municipality.geojson"
    return current if selected or current.is_file() else ROOT / "app/static/data/boundaries/municipality.geojson"
