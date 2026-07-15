"""Shared test fixtures and helpers for the WC3toSC2 converter test suite."""
import os
import sys
import pytest

# Ensure the project root is on sys.path for imports
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# Path to the Naaru example
NAARU_DIR = os.path.join(ROOT, "examples", "Naaru")
NAARU_MDX = os.path.join(NAARU_DIR, "Naaru.mdx")
NAARU_BLP = os.path.join(NAARU_DIR, "Naaru.blp")


@pytest.fixture(scope="session")
def naaru_mdx_path():
    """Path to the Naaru .mdx example model."""
    assert os.path.exists(NAARU_MDX), f"Naaru.mdx not found at {NAARU_MDX}"
    return NAARU_MDX


@pytest.fixture(scope="session")
def naaru_blp_path():
    """Path to the Naaru .blp example texture."""
    assert os.path.exists(NAARU_BLP), f"Naaru.blp not found at {NAARU_BLP}"
    return NAARU_BLP


@pytest.fixture(scope="session")
def tmp_output_dir(tmp_path_factory):
    """Session-scoped temporary directory for test output files."""
    return tmp_path_factory.mktemp("output")
