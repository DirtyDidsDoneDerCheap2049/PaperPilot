import pytest

def pytest_configure(config):
    (config.rootpath / 'work' / 'testing').mkdir(parents=True, exist_ok=True)

@pytest.fixture(autouse=True)
def legacy_storage_for_regression(monkeypatch,request):
    """Existing offline fixtures exercise SQLite; MySQL tests opt in explicitly."""
    if 'mysql' not in request.node.keywords:
        monkeypatch.setenv('READER_STORAGE','sqlite')
