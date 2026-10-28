import os
import tempfile
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mktemp(suffix=".db")
os.environ["SHELFFLOW_MODE"] = "simulation"
import pytest
from backend.db import Base, engine
from backend.seed import seed, TEST_PRODUCTS

@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    seed(TEST_PRODUCTS)
    yield
