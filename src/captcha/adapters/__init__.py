"""Port implementations — one folder per technology.

Anything that talks to the outside world lives here: redis, postgres, curl,
v8, s3, playwright. Adapters translate foreign exceptions into domain errors.
"""
