"""Constants of the fp2_companion integration."""

DOMAIN = "fp2_companion"

# The options, which up to version 1.4.1 were the AppDaemon app's yaml.
CONF_DEVICES = "devices"
CONF_EXCLUDE = "exclude"
CONF_BACKUPS_KEEP = "backups_keep"
CONF_TOKEN_CHECK_TIME = "token_check_time"
CONF_APP_IDENTITIES = "app_identities"

DEFAULT_BACKUPS_KEEP = 50
DEFAULT_TOKEN_CHECK_TIME = "04:10:00"

# The card publishes its commands under this prefix, so it is not an option.
BASE_TOPIC = "fp2companion"

# The session, the backups and the published zone selects, under the config
# folder. The session holds the Aqara token, so it sits with Home Assistant's
# own credentials in .storage, which every backup includes (decision 26).
STATE_DIR = ".storage/fp2_companion"

# Where the AppDaemon app kept the same files up to version 1.4.1. The first
# setup copies them, so nobody signs in again and the backups stay.
APPDAEMON_STATE_DIR = "/share/appdaemon/fp2_companion"

# Where the AppDaemon app may be, under the config folder: HACS named the
# folder after the repository, fp2-companion, and a copy by hand kept the
# repository's apps/fp2_companion. While one is there, the integration does
# not start: the app may be running on the same session and the same topics.
APPDAEMON_APP_DIRS = ("appdaemon/apps/fp2-companion", "appdaemon/apps/fp2_companion")
ISSUE_APPDAEMON_APP = "appdaemon_app_installed"
