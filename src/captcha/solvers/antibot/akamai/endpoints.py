"""Akamai Bot Manager wire constants.

Two sensor flows share a backend but not a body:

    classic   POST {"sensor_data": "<opaque>"} to the tenant's sensor script URL
              (older tenants: form-encoded sensor_data=...)
    SBSD      POST a JSON envelope to a per-challenge /sec/cp_challenge/sbsd path

Both are answered with cookies, not a token: bm_sz is the seed, _abck carries
the graded state. The sensor script URL is per-tenant and rotates with every
release, so it is discovered from the challenge page or carried in the task
payload -- never hardcoded.
"""

from __future__ import annotations

#: Field name of the classic POST, in both body encodings.
SENSOR_FORM_KEY: str = "sensor_data"
SENSOR_JSON_KEY: str = "sensor_data"

#: Content types the two classic encodings use.
SENSOR_JSON_TYPE: str = "application/json"
SENSOR_FORM_TYPE: str = "application/x-www-form-urlencoded"

#: SBSD challenge endpoint family. The trailing segments are per-deploy, so a
#: discovered URL always wins over these.
SBSD_PATH_HINT: str = "/sec/cp_challenge/sbsd"
VERIFY_PATH: str = "/sec/cp_challenge/verify"

#: Status Akamai uses to announce an un-cleared sec-cpt challenge.
STATUS_PRECONDITION: int = 428

#: A session may need more than one sensor POST: initial collection, then the
#: behavioural one. Public write-ups settle on "commonly up to three".
MAX_SENSOR_POSTS: int = 3

#: Cookies worth carrying into the answer, most useful first.
AKAMAI_COOKIES_OF_INTEREST: tuple[str, ...] = ("_abck", "bm_sz", "bm_sv", "ak_bmsc", "bm_mi", "sbsd", "sec_cpt")

#: Cookie that decides the task, and the seed cookie the encoder wants.
ABCK_COOKIE: str = "_abck"
SEED_COOKIE: str = "bm_sz"
SBSD_COOKIE: str = "sbsd"
SEC_CPT_COOKIE: str = "sec_cpt"
