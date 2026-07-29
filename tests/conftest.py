"""
tests/conftest.py
~~~~~~~~~~~~~~~~~
Shared pytest fixtures and markers for agent-memory-sdk tests.
"""



def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: mark test as requiring a live Db2 instance"
    )
