"""Use the portable socket event loop in controlled Windows tests."""

import asyncio
import sys


def pytest_sessionstart(session):
    if sys.platform == "win32":
        # The Proactor loop can stall when cancelling a real TLS handshake.
        # Keep the actual TCP/TLS/deadline assertions; do not skip these tests.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
