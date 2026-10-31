"""Create the fp2_companion cloud session with a one-time login.

The usual way to sign in is the card, which sends the password already
encrypted (docs/DESIGN.md, decisions 15 and 16). This tool is
the command line fallback and does the same login.

The app logs in only when someone signs in on the card. Otherwise it keeps
its session alive by refreshing the token, which needs no password, so the
password is used once and stored nowhere. Run this again only if the session is lost: a refresh that failed,
or a session file that was deleted. A second run reuses the PhoneId already in
the session file, so the account does not collect a new parallel session each
time.

The app identity is the region's built-in pair, and a pair in the add-on's
`/config/secrets.yaml` (`aqara_app_id`, `aqara_app_key`) is tried after it
when there is one. The account and the password arrive on stdin, one per
line, so neither appears in a process list or a shell history. Run it inside
the AppDaemon container (`$CONTAINER`), typing both at the prompts, or feed
them from a password manager:

    docker exec -it $CONTAINER \\
        python3 /config/apps/fp2_companion/tools/login.py --did <did> --country GR

After the login it reads the sensitivity of `--did` once, which proves the new
session works. It prints only codes, the expiry date and that value, never a
credential.
"""
import argparse
import getpass
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from aqara_cloud import (  # noqa: E402
    RID_SENSITIVITY, AqaraCloud, AqaraError, SessionStore)

SECRETS_FILE = "/config/secrets.yaml"
SESSION_FILE = "/share/appdaemon/fp2_companion/session.json"
IDENTITY_KEYS = ("aqara_app_id", "aqara_app_key")


def load_identity(path):
    """The optional pair in secrets.yaml, or (None, None)."""
    import yaml

    try:
        with open(path) as handle:
            secrets = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        return None, None
    if not all(secrets.get(k) for k in IDENTITY_KEYS):
        return None, None
    return str(secrets["aqara_app_id"]), str(secrets["aqara_app_key"])


def read_credentials():
    if sys.stdin.isatty():
        return input("account: ").strip(), getpass.getpass("password: ")
    lines = sys.stdin.read().splitlines()
    if len(lines) < 2 or not lines[0].strip() or not lines[1]:
        raise SystemExit("stdin must carry the account and the password, "
                         "one per line")
    return lines[0].strip(), lines[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--secrets", default=SECRETS_FILE)
    parser.add_argument("--session", default=SESSION_FILE)
    parser.add_argument("--did", required=True,
                        help="the FP2 whose sensitivity is read to verify")
    parser.add_argument("--country", required=True,
                        help="ISO country of the account, as in the Aqara "
                             "app, for example GR or US")
    args = parser.parse_args()

    appid, appkey = load_identity(args.secrets)
    account, password = read_credentials()
    cloud = AqaraCloud(SessionStore(args.session), appid, appkey, args.did)
    report = {}
    try:
        out = cloud.login(account, password, country=args.country)
        del password
        report["login"] = "ok"
        report["phone_id_reused"] = out["phone_id_reused"]
        if out["expires_at"]:
            report["expires"] = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(out["expires_at"]))
        values = cloud.query_rids([RID_SENSITIVITY])
        report["verify"] = "ok"
        report["sensitivity"] = values.get(RID_SENSITIVITY)
    except AqaraError as exc:
        report["error"] = {"endpoint": exc.endpoint, "code": exc.code,
                           "message": exc.message}
    print(json.dumps(report, indent=2))
    return 0 if report.get("verify") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
