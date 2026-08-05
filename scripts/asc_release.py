#!/usr/bin/env python3
"""
App Store Connect release driver.

Runs from CI so releases never depend on a local .p8 file. Credentials come
from environment variables (GitHub secrets in the workflow).

Actions (ASC_ACTION):
    status   Show the latest builds and existing App Store versions.
    prepare  Create ASC_VERSION, attach the newest VALID build, set release
             notes, set export compliance, and configure phased release.
    submit   Submit the prepared version to App Review.

Environment:
    ASC_KEY_P8     Contents of the AuthKey_XXXXXXXXXX.p8 file
    ASC_KEY_ID     10-character Key ID
    ASC_ISSUER_ID  Issuer UUID
    ASC_VERSION    Marketing version for 'prepare' / 'submit'
    ASC_WHATS_NEW  Release notes (en-US) for 'prepare'
    ASC_PHASED     "true" to enable phased release (default: disabled)

Export compliance: builds are declared usesNonExemptEncryption=false, which
is correct while the app uses only standard HTTPS (URLSession, Firebase,
StoreKit). Revisit if the app ever ships its own cryptography.
"""

import base64
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("asc_release")

APP_ID = "6776392285"
BASE = "https://api.appstoreconnect.apple.com"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _token() -> str:
    p8 = os.environ["ASC_KEY_P8"]
    key = serialization.load_pem_private_key(p8.encode(), password=None)
    now = int(time.time())
    header = {"alg": "ES256", "kid": os.environ["ASC_KEY_ID"], "typ": "JWT"}
    payload = {
        "iss": os.environ["ASC_ISSUER_ID"],
        "iat": now,
        "exp": now + 1200,
        "aud": "appstoreconnect-v1",
    }
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    return f"{signing_input}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def call(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw.decode(errors="replace")


def newest_valid_build():
    s, b = call(
        "GET",
        f"/v1/builds?filter[app]={APP_ID}&sort=-uploadedDate&limit=10"
        "&fields[builds]=version,processingState,uploadedDate",
    )
    if s != 200:
        raise SystemExit(f"Could not list builds: {s} {b}")
    for d in b["data"]:
        if d["attributes"]["processingState"] == "VALID":
            return d
    raise SystemExit("No VALID build found — Apple may still be processing the upload.")


def find_version(version_string: str):
    s, b = call(
        "GET",
        f"/v1/apps/{APP_ID}/appStoreVersions?limit=20"
        "&fields[appStoreVersions]=versionString,appStoreState",
    )
    if s != 200:
        raise SystemExit(f"Could not list versions: {s} {b}")
    for d in b["data"]:
        if d["attributes"]["versionString"] == version_string:
            return d
    return None


def action_status():
    s, b = call(
        "GET",
        f"/v1/builds?filter[app]={APP_ID}&sort=-uploadedDate&limit=5"
        "&fields[builds]=version,processingState,uploadedDate",
    )
    log.info("Recent builds:")
    for d in b["data"]:
        a = d["attributes"]
        log.info("  build %-4s %-12s uploaded %s", a["version"], a["processingState"], a["uploadedDate"])

    s, b = call(
        "GET",
        f"/v1/apps/{APP_ID}/appStoreVersions?limit=5"
        "&fields[appStoreVersions]=versionString,appStoreState",
    )
    log.info("App Store versions:")
    for d in b["data"]:
        a = d["attributes"]
        log.info("  %-8s %s", a["versionString"], a["appStoreState"])


def action_prepare():
    version_string = os.environ.get("ASC_VERSION", "").strip()
    if not version_string:
        raise SystemExit("ASC_VERSION is required for 'prepare'")

    build = newest_valid_build()
    log.info("Using build %s (%s)", build["attributes"]["version"], build["id"])

    existing = find_version(version_string)
    if existing:
        vid = existing["id"]
        log.info("Version %s already exists (%s), reusing", version_string, existing["attributes"]["appStoreState"])
    else:
        s, b = call(
            "POST",
            "/v1/appStoreVersions",
            {
                "data": {
                    "type": "appStoreVersions",
                    "attributes": {
                        "platform": "IOS",
                        "versionString": version_string,
                        "releaseType": "AFTER_APPROVAL",
                    },
                    "relationships": {"app": {"data": {"type": "apps", "id": APP_ID}}},
                }
            },
        )
        if s != 201:
            raise SystemExit(f"Could not create version: {s} {b}")
        vid = b["data"]["id"]
        log.info("Created version %s (%s)", version_string, vid)

    # Export compliance must be answered before the build can be reviewed.
    s, b = call(
        "PATCH",
        f"/v1/builds/{build['id']}",
        {"data": {"type": "builds", "id": build["id"], "attributes": {"usesNonExemptEncryption": False}}},
    )
    log.info("Export compliance set: %s", s)

    s, b = call(
        "PATCH",
        f"/v1/appStoreVersions/{vid}/relationships/build",
        {"data": {"type": "builds", "id": build["id"]}},
    )
    log.info("Build attached: %s", s)

    whats_new = os.environ.get("ASC_WHATS_NEW", "").strip()
    if whats_new:
        s, b = call("GET", f"/v1/appStoreVersions/{vid}/appStoreVersionLocalizations")
        loc = next(d["id"] for d in b["data"] if d["attributes"]["locale"] == "en-US")
        s, b = call(
            "PATCH",
            f"/v1/appStoreVersionLocalizations/{loc}",
            {
                "data": {
                    "type": "appStoreVersionLocalizations",
                    "id": loc,
                    "attributes": {"whatsNew": whats_new},
                }
            },
        )
        log.info("Release notes set: %s", s)

    phased = os.environ.get("ASC_PHASED", "false").lower() == "true"
    s, b = call(
        "POST",
        "/v1/appStoreVersionPhasedReleases",
        {
            "data": {
                "type": "appStoreVersionPhasedReleases",
                "attributes": {"phasedReleaseState": "ACTIVE" if phased else "INACTIVE"},
                "relationships": {
                    "appStoreVersion": {"data": {"type": "appStoreVersions", "id": vid}}
                },
            }
        },
    )
    log.info("Phased release %s: %s", "enabled" if phased else "disabled", s)
    log.info("Prepared version id: %s", vid)


def action_submit():
    version_string = os.environ.get("ASC_VERSION", "").strip()
    if not version_string:
        raise SystemExit("ASC_VERSION is required for 'submit'")
    version = find_version(version_string)
    if not version:
        raise SystemExit(f"Version {version_string} not found — run 'prepare' first")
    vid = version["id"]

    s, b = call(
        "GET",
        f"/v1/apps/{APP_ID}/reviewSubmissions"
        "?filter[state]=READY_FOR_REVIEW,WAITING_FOR_REVIEW,IN_REVIEW,UNRESOLVED_ISSUES&limit=5",
    )
    open_subs = b.get("data", []) if s == 200 else []
    if open_subs:
        sub = open_subs[0]["id"]
        log.info("Reusing open submission %s (%s)", sub, open_subs[0]["attributes"]["state"])
    else:
        s, b = call(
            "POST",
            "/v1/reviewSubmissions",
            {
                "data": {
                    "type": "reviewSubmissions",
                    "attributes": {"platform": "IOS"},
                    "relationships": {"app": {"data": {"type": "apps", "id": APP_ID}}},
                }
            },
        )
        if s != 201:
            raise SystemExit(f"Could not create submission: {s} {b}")
        sub = b["data"]["id"]

    s, b = call(
        "POST",
        "/v1/reviewSubmissionItems",
        {
            "data": {
                "type": "reviewSubmissionItems",
                "relationships": {
                    "reviewSubmission": {"data": {"type": "reviewSubmissions", "id": sub}},
                    "appStoreVersion": {"data": {"type": "appStoreVersions", "id": vid}},
                },
            }
        },
    )
    if s != 201:
        raise SystemExit(f"Could not add version to submission: {s} {json.dumps(b)[:1500]}")

    s, b = call(
        "PATCH",
        f"/v1/reviewSubmissions/{sub}",
        {"data": {"type": "reviewSubmissions", "id": sub, "attributes": {"submitted": True}}},
    )
    if s != 200:
        raise SystemExit(f"Could not submit: {s} {b}")
    log.info("Submitted — state: %s", b["data"]["attributes"]["state"])


def main() -> None:
    for var in ("ASC_KEY_P8", "ASC_KEY_ID", "ASC_ISSUER_ID"):
        if not os.environ.get(var):
            raise SystemExit(f"Missing required secret: {var}")

    action = os.environ.get("ASC_ACTION", "status")
    {"status": action_status, "prepare": action_prepare, "submit": action_submit}[action]()


if __name__ == "__main__":
    main()
