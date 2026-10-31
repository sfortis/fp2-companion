# FP2 Companion

Configure Aqara FP2 presence sensors from Home Assistant: sensitivity,
approach distance, AI person detection, and a full zone editor with backups,
plus the live targets on the zone map, the way the Aqara app shows them.

The FP2 reports presence to Home Assistant locally over HomeKit, but it has
no local channel for its settings, which normally means the Aqara phone app.
FP2 Companion is an [AppDaemon](https://appdaemon.readthedocs.io/) app and a
Lovelace card that make those settings available in Home Assistant through
the same cloud API the Aqara app uses. Presence keeps flowing over HomeKit
and does not depend on the cloud.

> **Unofficial.** This project is not affiliated with Aqara or Lumi. It uses
> a private API that Aqara does not document or support, and that can change
> or stop working at any time. Use it at your own risk.

## Features

- **Settings as entities:** sensitivity, approach distance, the sensitivity
  of each detection zone, the AI person detection switch and the AI learning
  button, all found automatically for every FP2 paired over HomeKit.
- **Zone editor card:** paint blocks on the 16 by 20 grid, rename zones, set
  their sensitivity, create and delete detection zones, and redraw the
  interference zone.
- **Backups:** the zones are backed up whenever they change and before every
  write. Any backup can be previewed on the map and restored.
- **Live view:** the people and interference sources the FP2 is tracking,
  drawn as dots on the zone map, while the card is on screen.
- **Sign-in from the card:** email, password and country, the same as the
  Aqara app. The password is encrypted in the browser and never stored.
- **No polling:** the app reads the cloud at startup, after every write and
  on Refresh. The live view runs only while someone is looking at it.

## Requirements

- Home Assistant with the FP2 paired through **HomeKit Device** (the
  `homekit_controller` integration). The app finds FP2s by their HomeKit
  model, `PS-S02D`.
- The **AppDaemon** add-on (4.5 or later), with its MQTT plugin connected to
  the same broker as Home Assistant's MQTT integration.
- An **Aqara account** that owns the FP2s.
- An **admin** Home Assistant user for the live view, because it listens
  through Home Assistant's own MQTT subscription, which is open to admins
  only. Everything else works for any user.

## Installation

### 1. The app

Copy `apps/fp2_companion/` into AppDaemon's apps folder, so that it ends up as
`/config/apps/fp2_companion/` inside the add-on (on Home Assistant OS that is
`/addon_configs/a0d7b954_appdaemon/apps/fp2_companion/`). The app's settings are
in `fp2_companion.yaml` beside it, and the defaults suit most homes.

### 2. The app identity (usually nothing to do)

Every request is signed with the Aqara Home app's own identity, an app id
and an app key that are the same for every user of that app. They are built
in, one pair per region, in `regions.py`, taken from the open-source
[Aqara-LANLink](https://github.com/absent42/Aqara-LANLink) integration. On
2026-10-31 Aqara's servers accepted the pairs for EU, US, RU, KR and AU,
which cover every region that shares their servers.

The China region (CN) rejected its pair. An account there needs a working
pair of its own in the add-on's `secrets.yaml`:

```yaml
aqara_app_id: "..."
aqara_app_key: "..."
```

Then uncomment `app_id` and `app_key` in `fp2_companion.yaml`. A configured
pair, and any listed as `app_identities`, is tried after the region's own,
and a sign-in keeps the first pair the region accepts.

### 3. The MQTT command topics

The card sends its commands to the app over MQTT. AppDaemon's MQTT plugin
subscribes only to the topics in its `client_topics` list, so add both of
these to the MQTT plugin in `appdaemon.yaml`:

```yaml
plugins:
  MQTT:
    type: mqtt
    namespace: mqtt
    # client_host, client_user, client_password: as for your broker
    client_topics:
      - fp2companion/cmd/#
      - fp2companion/+/cmd/#
```

Restart the add-on afterwards. The plugin reads this list, and
`secrets.yaml`, only at startup, so a missing topic makes every command on
the card do nothing without any error.

### 4. The card

Copy `card/fp2-companion-card.js` to `/config/www/` and add it as a dashboard
resource (Settings, Dashboards, Resources):

```
/local/fp2-companion-card.js?v=1.8.0
```

with the type **JavaScript module**. Raise the `?v=` after every update of
the file, because browsers cache it hard. Then add the card to a dashboard:

```yaml
type: custom:fp2-companion-card
```

| Option | Default | Meaning |
|---|---|---|
| `title` | `Presence sensor` | The card's title |
| `device` | none | Pins the card to one FP2, by its serial, entity prefix, device id or name, and hides the device picker |
| `devices_entity` | `sensor.fp2_companion_devices` | The app's own sensor |
| `command_root` | `fp2companion` | The app's MQTT prefix |
| `entities` | `{}` | Overrides for entity ids renamed in the registry, on a pinned card |
| `text` | English | Every word on the card, for a translation |

### 5. Sign in

Open the card and sign in with your Aqara account and its country. The app
creates its own session and keeps it alive by itself; the token is renewed
before it expires, and the password is not needed again.
`apps/fp2_companion/tools/login.py` does the same sign-in from the command line.

## Using It

The card has four tabs.

- **Zones** shows the map, the live dots and the legend. Press a zone to
  pick it out on the map, or **Edit** to change it. Editing paints blocks by
  tapping or dragging. **Delete** needs a second press.
- **Settings** has AI person detection, sensitivity, approach distance and
  AI learning. While AI person detection is on, the sensor sets its own
  sensitivity and ignores changes to it, so the control is disabled.
- **Backups** lists the zone backups. Choosing one previews it on the map,
  and **Restore** needs a second press.
- **Account** shows the Aqara account, until when the session is valid,
  **Change login** and **Log out**, which needs a second press.

The cloud icon at the top shows the app's status: green with a check when
all is well, amber with an exclamation mark for a warning, and red with a
cross when there is no session. The line under it explains anything that is
not green.

## Live View

The live view follows what the Aqara app does on its device page. While the
card's map is on screen, the card holds a lease that it renews every 30
seconds. While any lease is held, the app keeps one websocket to the Aqara
cloud, subscribes to each watched FP2, and writes the FP2's "device page
open" heartbeat every 60 seconds, which is what makes the FP2 send its
targets at all. Each update (about one every 0.75 seconds) goes to the
unretained MQTT topic `fp2companion/<serial>/live`, and the card listens to it
directly, so nothing is written to Home Assistant's recorder.

When the card is scrolled away, its tab is hidden, the dashboard view
changes or the screen goes off, the card gives its lease back and the app
closes the socket. A browser that disappears without a word is dropped
after 75 seconds.

The dots use the placement for a wall-mounted FP2. A ceiling mount has not
been checked.

## Security and Privacy

- The password is turned into its MD5 and encrypted with Aqara's public RSA
  key in the browser, as the Aqara app does, and only that block crosses
  Home Assistant and MQTT. The app uses it once and keeps only the token.
- The session lives in `/share/appdaemon/fp2_companion/session.json` with mode
  0600. Nothing in the app logs a token, a user id or the app key.
- The app writes only a fixed list of settings and values, and refuses
  anything else before it reaches the network.
- Zone backups are in `/share/appdaemon/fp2_companion/backups/<device id>/`.

## Entities

For an FP2 whose HomeKit entities start with `presence_sensor_fp2_b2c3`, the
app creates `select.presence_sensor_fp2_b2c3_sensitivity`,
`..._approach_distance`, one `..._<zone>_sensitivity` per detection zone,
`switch...._ai_detection`, `button...._ai_learning`, `button...._refresh`,
`sensor...._zones`, `sensor...._backup_preview` and
`sensor...._cloud_status`, on a device of its own in the FP2's area. It also
creates `sensor.fp2_companion_devices`, which lists every FP2 it serves.

## Development

```
python3 -m pytest tests
NODE_PATH="$(npm root -g)" node card/test/fp2-companion-smoke.js
```

The Python tests need only `pytest`. The card test needs `jsdom` installed
globally, and Python's `cryptography` for its RSA check.
`card/test/fp2-companion-render.js` renders the card from a live Home Assistant
into an HTML file, for a look in a headless browser. `scripts/deploy.sh`
deploys to a Home Assistant OS host over SSH.

[docs/DESIGN.md](docs/DESIGN.md) explains how the app is built and why, and
[docs/PROTOCOL.md](docs/PROTOCOL.md) describes the cloud API it uses.

## Credits

The region to host table and the first map of the API came from
[Aqara-LANLink](https://github.com/absent42/Aqara-LANLink) by Dan Gibson
(MIT).

## License

MIT, see [LICENSE](LICENSE).
