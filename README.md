# Aqara FP2 Companion

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-integration-41BDF5?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![HACS](https://img.shields.io/badge/HACS-integration-41BDF5)](https://hacs.xyz/)

A Home Assistant integration for the Aqara FP2 presence sensor. It brings the
settings that otherwise live only in the Aqara phone app into Home
Assistant: sensitivity, approach distance, AI person detection, and a full
zone editor with backups, plus the live targets on the zone map, the way the
Aqara app shows them. It is an unofficial project and is not made by or
affiliated with Aqara.

The FP2 reports presence to Home Assistant locally over HomeKit, but it has
no local channel for its settings, which normally means the Aqara phone app.
Aqara FP2 Companion is a Home Assistant integration and a
[Lovelace card](https://github.com/sfortis/fp2-companion-card) that make
those settings available in Home Assistant through the same cloud API the
Aqara app uses. Presence keeps flowing over HomeKit and does not depend on
the cloud.

<p align="center">
  <img src="https://raw.githubusercontent.com/sfortis/fp2-companion/main/docs/images/zones.png" width="380" alt="The Zones tab: the zone map with live targets and the zone list">
  <img src="https://raw.githubusercontent.com/sfortis/fp2-companion/main/docs/images/settings.png" width="380" alt="The Settings tab: AI person detection, AI learning, sensitivity and approach distance">
</p>

> **Unofficial.** This project is not affiliated with Aqara or Lumi. It uses
> a private API that Aqara does not document or support, and that can change
> or stop working at any time. Use it at your own risk.

## Requirements

- Home Assistant 2025.2 or later, with the FP2 paired through **HomeKit
  Device** (the `homekit_controller` integration).
- An **Aqara account** that owns the FP2s, and its password.
- The [FP2 Companion Card](https://github.com/sfortis/fp2-companion-card),
  version 2.0.0 or later, installed from HACS. The card is the zone editor
  and the live view.
- An **admin** Home Assistant user for the zone editor, backup restores and
  the live view. Any user can read the card and change the settings that are
  entities.

## Supported Devices

The integration serves every FP2 that HomeKit Device has paired, found by
its HomeKit model: `PS-S02D`, `PS-S02E` or `PS-T02E`. An FP2 that is not
paired over HomeKit can be added by its serial in the options. Other Aqara
presence sensors, such as the FP1E (`PS-S03D`) and the FP300 (`PS-S04D`),
are not supported and are skipped without a word. A model of the FP2
families that is not on the list is skipped with a warning in the log, which
asks for an issue with the model.

## Features

- **Settings as entities:** sensitivity, approach distance, the sensitivity
  of each detection zone, the AI person detection switch and the AI learning
  button, for every FP2.
- **Zone editor card:** paint blocks on the 16 by 20 grid, rename zones, set
  their type and sensitivity, create and delete detection zones, redraw the
  interference zone, the entrance zone and the room's edge, and change a
  zone's attribute between detection, entrance and edge.
- **Backups:** the zones are backed up whenever they change and before every
  write. Any backup can be previewed on the map and restored.
- **Live view:** the people and interference sources the FP2 is tracking,
  drawn as dots on the zone map, while the card is on screen.
- **Sign-in in Home Assistant:** the account, the password and the country
  are asked when the integration is added, and again only when Aqara ends
  the session.

## Use Cases

- Lower the sensitivity of a zone by a radiator or a fan that the FP2 keeps
  reporting, without opening the Aqara app.
- Redraw the zones after moving furniture, and restore the previous layout
  in one step if the new one detects worse.
- Watch the live dots while you walk the room, to see where the FP2 places
  you and where its zones should end.
- Switch AI person detection from an automation, for example off while
  nobody is home.

## Installation

**With HACS.** Search for **Aqara FP2 Companion** in HACS (the Marketplace
since Home Assistant 2026.11) and download it. If the search does not find
it yet, add `https://github.com/sfortis/fp2-companion` as a custom
repository of the type **Integration** instead. Restart Home Assistant.

**By hand.** Copy `custom_components/fp2_companion/` into the
`custom_components` folder of Home Assistant's configuration folder and
restart Home Assistant.

Then add the integration in **Settings > Devices & services > Add
integration > Aqara FP2 Companion**. The form asks for these fields:

| Field | Meaning |
|---|---|
| Aqara account | The email or phone number you sign in to the Aqara app with. |
| Password | Used once to sign in. Only the session is kept, never the password. |
| Country or region | The one chosen in the Aqara app. It decides which Aqara servers hold the account. |
| Custom Aqara app credentials | Only for an account in the China region, as `app_id:app_key`. Leave it empty otherwise. |

The integration signs in before it creates the entry, so a wrong password or
an unreachable cloud is reported in the form. It then finds the FP2s by
itself, within a few seconds.

Install the card from HACS as its own
[README](https://github.com/sfortis/fp2-companion-card) describes, and add
it to a dashboard:

```yaml
type: custom:fp2-companion-card
```

## Configuration

The integration's **Configure** button opens these options:

| Option | Default | Meaning |
|---|---|---|
| Add FP2s manually by serial | none | FP2s that are not paired through HomeKit, one serial per field, like `54EF44A1B2C3`. |
| Exclude FP2s by serial | none | FP2s the integration ignores, even if HomeKit finds them. |
| Zone backups to keep per device | 50 | Older backups are deleted beyond this number. |
| Token refresh schedule | 04:10 | The Aqara token is checked every day at this time and refreshed if it expires within 3 days. |
| Custom Aqara app credentials | none | Extra `app_id:app_key` pairs. Saved pairs are not shown; leave the field empty to keep them. |
| Delete saved credentials | off | Removes all saved app credentials. |

To sign in again or with another account, choose **Reconfigure** from the
integration's menu.

## Entities

For an FP2 whose HomeKit entities start with `presence_sensor_fp2_b2c3`, the
integration creates these entities on a device named after the FP2 with
"Settings" added, in the FP2's area:

| Entity | What it does |
|---|---|
| `select.presence_sensor_fp2_b2c3_sensitivity` | The FP2's sensitivity: Low, Medium or High. |
| `select.presence_sensor_fp2_b2c3_approach_distance` | How close a person must come before an approach is reported: Far, Middle or Close. |
| `select.presence_sensor_fp2_b2c3_<zone>_sensitivity` | One per detection zone, named after the zone. |
| `switch.presence_sensor_fp2_b2c3_ai_detection` | AI person detection. While it is on, the FP2 sets its own sensitivity. |
| `button.presence_sensor_fp2_b2c3_ai_learning` | Starts the one-minute AI learning run of the Aqara app. |
| `button.presence_sensor_fp2_b2c3_refresh` | Reads the settings and the zones again. |
| `sensor.presence_sensor_fp2_b2c3_zones` | The number of zones; its attributes carry the zones and the newest backups for the card. |
| `sensor.presence_sensor_fp2_b2c3_cloud_status` | What the last cloud call came to, as a code: `ok`, `no_session`, `cloud_error`, `refused` and others. |

It also creates `sensor.fp2_companion_devices`, which lists every FP2 it
serves. The settings are unavailable while the cloud cannot read or write
them; the status, the zones and Refresh stay available.

## How Data Is Updated

The integration never polls the cloud. It reads the settings and the zones
when it starts, a few seconds after every write to check that the write
landed, and when Refresh is pressed. A change made in the Aqara app
therefore shows only after Refresh or a restart. The Aqara token is checked
once a day at the time set in the options, and it is renewed when fewer than
three days remain. The live positions come over a push socket that is open
only while a card shows the map, and they are never stored.

## Examples

Notify when the cloud status of an FP2 is anything but `ok` for ten
minutes:

```yaml
automation:
  - alias: "FP2 settings need attention"
    triggers:
      - trigger: state
        entity_id: sensor.presence_sensor_fp2_b2c3_cloud_status
        to: ~
        for: "00:10:00"
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.state != 'ok' }}"
    actions:
      - action: notify.notify
        data:
          message: >
            The FP2's settings report
            {{ states('sensor.presence_sensor_fp2_b2c3_cloud_status') }}.
```

Lower the sensitivity of the couch zone at night:

```yaml
automation:
  - alias: "Couch zone at night"
    triggers:
      - trigger: time
        at: "23:30:00"
    actions:
      - action: select.select_option
        target:
          entity_id: select.presence_sensor_fp2_b2c3_couch_sensitivity
        data:
          option: Low
```

## Moving From Version 1.4.x

Versions up to 1.4.x created MQTT entities, and up to 1.4.1 ran as an
AppDaemon app. The first start of version 2.0.0 moves every MQTT entity to
the integration with the same entity id and the settings you gave it (name,
icon, area, labels, hidden or disabled), so dashboards, automations,
history and statistics keep working. It removes the old MQTT devices and
clears what the old versions left on the MQTT broker. MQTT is no longer
needed after that.

- The entities sit on new devices, so a device trigger, condition or action
  that names one of the old devices has to be picked again.
- The card must be version 2.0.0 or later, because it now talks to the
  integration over Home Assistant's websocket instead of MQTT.
- Sign in once more when the integration asks for it.

From the AppDaemon app, stop the app first: stop AppDaemon, or remove the
`fp2_companion` entry from its `apps.yaml` and restart it. Then delete the
app's folder, `appdaemon/apps/fp2-companion/` when HACS installed it.
Install the integration as above. Its first start copies the session and
the zone backups from `/share/appdaemon/fp2_companion/` and deletes the old
`session.json` there. While the app's folder is still there, the
integration does not start and raises a repair that says so. It can see the
folder only where HACS put it; a copy made by hand into
`/addon_configs/a0d7b954_appdaemon/apps/` has to be removed by hand.

## Troubleshooting

| What you see | What to do |
|---|---|
| A notification asks you to sign in again | Aqara ended the session. Open it and enter the password. |
| The cloud status is `cloud_error` and the settings are unavailable | The Aqara servers did not answer. Press Refresh once they are reachable; the log says when the cloud is lost and when it answers again. |
| The cloud status is `write_not_applied` after a sensitivity change | AI person detection is on, and the FP2 ignores a written sensitivity while it is on. Turn it off first. |
| The cloud status is `not_on_account` | The FP2 is on another Aqara account. Sign in with that account, or exclude the FP2 in the options. |
| The card says "Live view needs an admin user" | The live view, the zone editor and restores need an admin Home Assistant user. |
| The card shows no live dots after an update | The browser runs the old card from its cache. Reload the page without the cache, or clear the cache of the Home Assistant app. |
| A new FP2 does not appear | It appears within a few seconds once HomeKit Device has paired it. If it is not paired over HomeKit, add its serial in the options. |

**Download diagnostics** on the integration's page gives a file for an
issue. The account, the app credentials, the session and the FP2 serials
are left out of it.

## Known Limitations

- The dots and the FP2 on the map use the placement for a wall-mounted FP2.
  A ceiling mount has not been checked.
- Accounts in the China region (CN) cannot sign in with the built-in Aqara
  app identity. Such an account needs a working pair of its own, as
  `app_id:app_key` in the first form or in the options.
- A zone changes its attribute only from or to detection. To turn an
  entrance zone into the edge, make it a detection zone first.
- The FP2 also tracks targets outside the room, in the edge zone. They show
  as dots on the map, although the FP2 does not count them as presence.
- A change made in the Aqara app shows only after Refresh, because nothing
  polls the cloud.

## Security and Privacy

- The password goes from the form to the integration, which takes its MD5,
  encrypts that with Aqara's public RSA key as the Aqara app does, and sends
  only the encrypted block. The password is never stored or logged.
- The session lives in `.storage/fp2_companion/session.json` in Home
  Assistant's configuration folder, with mode 0600 in a folder with mode
  0700. Nothing in the integration logs a token, a user id or the app key.
- The card's commands and the live view require an admin Home Assistant
  user.
- The integration writes only a fixed list of settings and values, and
  refuses anything else before it reaches the network.
- Zone backups are in `.storage/fp2_companion/backups/<device id>/`.

## Removal

1. In **Settings > Devices & services > Aqara FP2 Companion**, choose
   **Delete** from the menu. The integration signs out of Aqara as it goes,
   so no valid token is left behind.
2. Remove the card from your dashboards, and remove **FP2 Companion Card**
   in HACS.
3. Remove **Aqara FP2 Companion** in HACS, or delete
   `custom_components/fp2_companion/` if you copied it by hand, and restart
   Home Assistant.
4. The zone backups stay in `.storage/fp2_companion/`. Delete that folder
   if you do not need them.

## Development

```
python3 -m pytest tests
python3 -m pytest -c tests_ha/pytest.ini tests_ha
```

`tests` covers the app logic and the cloud client and needs `pytest`,
`pyyaml` and `cryptography`. `tests_ha` runs the integration inside Home
Assistant and needs `pytest-homeassistant-custom-component` (Python 3.14
for current Home Assistant). No test reaches the network. The card and its
tests are in the
[card's repository](https://github.com/sfortis/fp2-companion-card).
`scripts/deploy.sh` deploys the integration to a Home Assistant OS host
over SSH.

[docs/DESIGN.md](docs/DESIGN.md) explains how it is built and why, and
[docs/PROTOCOL.md](docs/PROTOCOL.md) describes the cloud API it uses.

## Credits

The region to host table and the first map of the API came from
[Aqara-LANLink](https://github.com/absent42/Aqara-LANLink) by Dan Gibson
(MIT).

## License

MIT, see [LICENSE](LICENSE).
