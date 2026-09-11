"""Real HTTP API test suite.

The suite is opt-in through ``RUN_API_HTTP=1``. Keeping the opt-in boundary
explicit prevents the normal unit/TestClient suite from starting containers or
network servers as a side effect.
"""
