# fp2_companion Design

This page records how fp2_companion is built and why. The decisions are numbered
because comments in the code refer to them by number. The protocol itself is
in [PROTOCOL.md](PROTOCOL.md).

## Goal

An Aqara FP2 reports presence to Home Assistant locally, over HomeKit, but
it has no local channel for its configuration. Its sensitivity, its zones
and its AI settings can only be changed through the Aqara cloud, which
normally means the Aqara phone app. fp2_companion makes all of that available in
Home Assistant: as entities, and as a card that edits the zone map and shows
the live targets on it. Presence itself keeps flowing over HomeKit and does
not depend on the cloud.

## Decisions

1. The app is called `fp2_companion`. Its MQTT topics start with `fp2companion/`,
   and its card is `fp2-companion-card`.
2. The app derives the cloud device id from the serial number HomeKit
   reports, so `54EF44A1B2C3` becomes `lumi1.54ef44a1b2c3`. It refuses no
   device.
3. MQTT discovery cannot attach entities to the HomeKit device, because that
   device has no connection to match. The app therefore publishes a device
   of its own in the same area, and its entity ids carry the HomeKit
   prefix, for example `select.presence_sensor_fp2_b2c3_sensitivity`.
4. The cloud session belongs to the app, with its own stable PhoneId. The
   password is used once at sign-in and never stored.
5. The token is refreshed when fewer than three days remain before it
   expires. A code 108 triggers one refresh and one retry. If that fails,
   the app reports it in Home Assistant and stops calling the cloud.
6. The app does not poll. It reads from the cloud at startup, right after
   every write, and when Refresh is pressed.
7. A zone backup is written whenever a read returns zones that differ from
   the last backup, and before every zone write and every restore.
8. The settings exposed are the global sensitivity, the sensitivity of each
   detection zone and the approach distance.
9. The card is a full zone editor: it paints blocks, renames, sets the
   sensitivity, creates and deletes zones.
10. Zone create and delete use the calls the official app makes.
    `tools/zone_test.py` creates and deletes a small test zone, so that zone
    writes can be proven on an account before the app is trusted with real
    zones.
11. A restore is chosen on the card from the list of backups, and the card
    shows the chosen backup's map before anything is written.
12. The app identity (`Appid` and `Appkey`) is built in, one pair per
    region. A pair in the add-on's `secrets.yaml` is optional and is tried
    after the region's own.
13. The app exposes the AI person detection mode as a switch and AI learning
    as a button. Learning runs for about 60 seconds.
14. While the AI mode is on, the device reads sensitivity as High and
    ignores sensitivity writes. The card then disables the global
    sensitivity control, and the app refuses sensitivity writes instead of
    sending them.
15. A new user signs in from the card, with no command line. The card shows
    an email, password and country form when there is no session. When
    signed in it shows the account partly hidden, a Change login that opens
    the same form, and a Log out that needs a second press. A changed login
    replaces the session only when the new login succeeds.
16. The password never leaves the browser in clear. The card computes its
    MD5 and encrypts that with Aqara's public RSA key, exactly as the
    official app does, and sends only the result. That passes Home
    Assistant's event bus and the MQTT broker, which a clear password must
    not. It is enough to sign in to Aqara once but does not reveal the
    password, and the app keeps only the token.
17. One app serves every FP2. It finds them in Home Assistant as HomeKit
    devices of model `PS-S02D` or `PS-T02E`, at startup and on Refresh. A yaml list of
    serials covers an FP2 that is not paired over HomeKit, and a yaml
    `exclude` drops one. One session serves them all, because a refresh
    revokes the previous token and two sessions on one file would cut each
    other off. Each device has its own values, zones, backups, topics
    (`fp2companion/<serial>/...`) and status. The entity prefix comes from the
    device's original name, which is what its HomeKit entity ids were built
    from, so a device renamed in Home Assistant still matches them.
18. The card picks the device from a dropdown and remembers the choice in
    the browser. `device:` in the card config pins one device and hides the
    dropdown.
19. The sign-in form asks for the country, as the Aqara app does. The
    country selects the region, and the region selects the host and the app
    identity pair. A code 106 ("invalid sign") makes the app try the other
    configured pairs. Country, region, host and pair are stored in the
    session. There is no default country: the form offers the one the
    browser's language names, if any, and otherwise waits for a choice.
20. The built-in pairs are the Aqara Home app's own public values, the same
    for every user and already published in Aqara-LANLink. They are stored
    as base64 of the reversed string so that generic secret scanners do not
    report a public value. That is obfuscation, not protection.
21. The card shows the live targets as dots on the zone map, as the Aqara
    app does. The app holds the cloud socket only while a card holds a
    lease, which the card renews every 30 seconds while its map is on
    screen. While a device is watched, the app also writes the device page
    heartbeat every 60 seconds, because without it the FP2 sends no
    positions. Each push goes unretained to `fp2companion/<serial>/live`
    through AppDaemon's MQTT plugin, and the card listens through Home
    Assistant's `mqtt/subscribe`. Nothing reaches the recorder, but the
    live view needs an admin user.
22. The card ships inside the app folder, because HACS downloads an
    AppDaemon repository's app folder and nothing else, and a second
    repository would let the card and the app drift apart. At startup the
    app copies the card into Home Assistant's `www/fp2-companion/` and
    points one dashboard resource at it, with the card's version and a
    digest of the file in `?v=`, so a browser loads every new card. It
    changes the resource over Home Assistant's websocket API through the
    Supervisor with the add-on's `SUPERVISOR_TOKEN`, a documented add-on
    interface, rather than through AppDaemon's internal websocket. A
    resource for the card that a person added by hand is updated, never
    doubled, and further ones are reported, never deleted.
23. The app makes sure the card's commands reach it. It cannot read the
    MQTT plugin's `client_topics`, and subscribing regardless doubles every
    command for a list that already covers it: Mosquitto 2.1.2 delivers a
    message twice to a client subscribed to both `#` and
    `fp2companion/cmd/#` (measured on 2026-11-01). So it publishes a probe
    on each command topic, subscribes through the plugin's `mqtt/subscribe`
    service only to a topic whose probe did not come back, and probes
    again. A topic still silent is a warning in the log.

## Modules

The pure modules sit beside the app, and the app file holds only the Home
Assistant side.

- `aqara_cloud.py` is the cloud client and imports nothing from AppDaemon.
  It owns the session file and replaces it atomically (a temporary file,
  mode 0600, then a rename). It signs requests, handles code 108 with one
  refresh and one retry, and refuses to write any resource or value outside
  its whitelist. The HTTP transport is injected, so the tests need no
  network.
- `regions.py` maps a country to its region, its host and its built-in app
  identity.
- `zone_model.py` is pure. It converts between block ids and grid cells,
  validates zones, compares zone lists as sets, plans a restore, and names
  and reads the backup files.
- `live_track.py` holds the push socket for the live positions on its own
  thread and event loop. It handles the leases, subscribes and renews every
  watched device, writes the heartbeat, reconnects with a backoff, and
  parses each push into targets with their place on the grid.
- `card_install.py` copies the card into Home Assistant's `www` folder and
  plans and makes the change to its dashboard resource (decision 22). The
  plan is pure and the websocket client is injected, so the tests need no
  Home Assistant.
- `command_topics.py` probes the MQTT command topics and subscribes to the
  silent ones (decision 23).
- `fp2_companion.py` is the AppDaemon app: discovery, commands, reads, writes
  and their readback, backups, entities and status.
- `tools/` holds command line helpers that run inside the AppDaemon
  container: `login.py` (sign-in without the card), `zone_test.py` (decision
  10) and `live_probe.py` (checks the live protocol without the app).

## Entities

For an FP2 whose HomeKit entities start with `presence_sensor_fp2_b2c3`:

| Entity | Purpose |
|---|---|
| `select.presence_sensor_fp2_b2c3_sensitivity` | Global sensitivity: Low, Medium or High |
| `select.presence_sensor_fp2_b2c3_approach_distance` | Far, Middle or Close |
| `select.presence_sensor_fp2_b2c3_<zone>_sensitivity` | One per detection zone |
| `switch.presence_sensor_fp2_b2c3_ai_detection` | AI person detection |
| `button.presence_sensor_fp2_b2c3_ai_learning` | Starts AI learning |
| `button.presence_sensor_fp2_b2c3_refresh` | Reads everything from the cloud again |
| `sensor.presence_sensor_fp2_b2c3_zones` | Zone count, with the zones and the backups as attributes |
| `sensor.presence_sensor_fp2_b2c3_backup_preview` | The backup the card is previewing |
| `sensor.presence_sensor_fp2_b2c3_cloud_status` | A status code, a readable message and the session |
| `sensor.fp2_companion_devices` | Every FP2 the app serves, and the countries for the sign-in |

A per-zone select is added or retired when a zone is created or deleted.

## Writes and Readback

Every write is followed by a read of the same value, and a readback that
disagrees is reported with its own status code (`write_not_applied`). A zone
write reads the list fresh, backs it up, sends one zone item with every
field the cloud returned for it, and then reads back by `rangeId`. A change
that does not show in the readback sets `zone_write_failed`. Zones are
matched by `rangeId` everywhere and never by name.

The interference zone can be redrawn, but creating or deleting one is
refused, and so is a restore that would need either.

## Restore

The newest backup is not necessarily the right one: a backup is written
whenever the zones change, so after an unwanted change the newest backup
holds that unwanted state. The card therefore lists the backups with their
time and reason, previews the chosen one on the map, and restores only on a
second press. A restore rewrites in place each zone of the backup whose
`rangeId` still exists and differs, creates the ones that are gone (they come
back under new ids the server assigns), and deletes the zones the backup
does not have. Zones that already match are left alone.

## Known Limits

- The live dots use the wall mount transform. A ceiling mount and the Aqara
  app's reversed mounting setting place the points differently and have not
  been checked.
- The live view subscribes with the view version `1.8` from the Aqara app's
  cache. If Aqara raises it, the subscription may need the new value.
- Whether the AI mode also masks the per-zone sensitivity has not been
  checked.
- A new zone appears in HomeKit as an extra occupancy sensor of the FP2. Its
  Home Assistant entity can be left behind when the zone is deleted.
