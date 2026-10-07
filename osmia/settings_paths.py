"""Where Osmia keeps its data, readable before Django's settings load."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def data_dir():
    return Path(os.environ.get('OSMIA_DATA_DIR') or BASE_DIR / 'data')
