"""Constants of the fp2_companion integration."""

DOMAIN = "fp2_companion"

# The options, which up to version 1.4.1 were the AppDaemon app's yaml.
CONF_DEVICES = "devices"
CONF_EXCLUDE = "exclude"
CONF_BACKUPS_KEEP = "backups_keep"
CONF_TOKEN_CHECK_TIME = "token_check_time"
CONF_APP_IDENTITIES = "app_identities"
# Only in the options form: empties the stored pairs, and is not stored.
CONF_CLEAR_APP_IDENTITIES = "clear_app_identities"

DEFAULT_BACKUPS_KEEP = 50
DEFAULT_TOKEN_CHECK_TIME = "04:10:00"

# The MQTT topic prefix of versions up to 1.4.x, whose entities were MQTT
# discovery entities. Only the migration to native entities (migrate.py)
# still uses it, to clear what those versions left retained on the broker.
LEGACY_BASE_TOPIC = "fp2companion"
# Every unique id this integration has used starts with this.
UNIQUE_ID_PREFIX = "fp2_companion_"
# The app's own sensor, which the card reads by this entity id.
HUB_UNIQUE_ID = "fp2_companion_devices"
HUB_ENTITY_ID = "sensor.fp2_companion_devices"

# The session and the backups, under the config
# folder. The session holds the Aqara token, so it sits with Home Assistant's
# own credentials in .storage, which every backup includes (decision 26).
STATE_DIR = ".storage/fp2_companion"

# Where the AppDaemon app kept the same files up to version 1.4.1. The first
# setup copies them, so nobody signs in again and the backups stay.
APPDAEMON_STATE_DIR = "/share/appdaemon/fp2_companion"
# Written in the state folder once that copy has gone through.
COPIED_MARKER = "copied_from_appdaemon"

# Where the AppDaemon app may be, under the config folder: HACS named the
# folder after the repository, fp2-companion, and a copy by hand kept the
# repository's apps/fp2_companion. While one is there, the integration does
# not start: the app may be running on the same session.
APPDAEMON_APP_DIRS = ("appdaemon/apps/fp2-companion", "appdaemon/apps/fp2_companion")
ISSUE_APPDAEMON_APP = "appdaemon_app_installed"
