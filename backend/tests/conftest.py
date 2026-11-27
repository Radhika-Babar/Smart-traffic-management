import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.database import Database
from app.main import create_app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """A real app on a throwaway SQLite file, with local-fallback transport."""
    folder = tmp_path_factory.mktemp("api")
    url = f"sqlite:///{(folder / 'test.db').as_posix()}"
    settings = Settings(
        _env_file=None, database_url=url, mqtt_enabled=False, seed_history_days=4, simulation_seed=5,
        simulation_autostart=False,
    )
    application = create_app(settings, Database(url))
    with TestClient(application) as test_client:
        test_client.runtime = application.state.runtime
        test_client.runtime.predictions.model_path = folder / "models.joblib"
        yield test_client
