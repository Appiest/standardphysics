import pytest
from standardphysics_pipeline import tuned_labeller


@pytest.fixture(autouse=True)
def no_paid_labeller(monkeypatch):
    """A LABEL_STATE in the developer's shell or .env must not send test scans to the paid Fireworks pool."""
    monkeypatch.delenv(tuned_labeller.STATE_ENV, raising=False)
