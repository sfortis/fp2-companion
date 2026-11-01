/*
 * fp2-companion-card - an Aqara FP2's settings and zones, from the fp2_companion app.
 *
 * Built like ha-duma-card and lg-oled-card: plain HTML and CSS with one
 * inline SVG. The layout is the order someone asks things in front of the
 * sensor:
 *
 *   1. is the cloud link working
 *   2. how sensitive is it, and from how far does it react
 *   3. where are the zones (the map, with the interference zone hatched and
 *      the space outside the room's edge in grey)
 *   4. which backups of the zones exist, and put one back
 *
 * Everything comes from the app's own entities. The settings are changed
 * through the select entities with select.select_option, so the card and the
 * more-info dialog are one control and cannot disagree. Zone changes go to the
 * app's command topic, because a zone is a list of blocks and no entity can
 * carry one.
 *
 * Editing a zone: press its Edit badge in the legend (or press New zone), then
 * tap or drag on the map to add and remove blocks, set the name, the type and
 * the sensitivity, and press Save. Nothing reaches the device before Save. The app backs the zones
 * up before every write and reads them back afterwards, by rangeId, so the
 * pill says whether the change landed. Delete and Restore need a second press
 * (owner's decision, 2026-10-30). The interference zone can be redrawn but not
 * deleted, and no second one is created, because neither move was captured
 * from the official app.
 *
 * The grid is 16 columns by 20 rows and a block id is row * 16 + column + 1,
 * both from 0. The owner compared this orientation with the Aqara app, and a
 * 2 by 2 painted top right in the official app came back as 15, 16, 31 and 32.
 * Where the sensor sits on the grid is not known, so it is not drawn.
 *
 * Every FP2 the app serves is listed on sensor.fp2_companion_devices, and the
 * card picks one from a dropdown at the top, remembering the choice in this
 * browser; `device:` in the card config pins one and hides the dropdown
 * (docs/DESIGN.md, decisions 17 and 18). The entity ids follow from
 * the chosen device's prefix.
 *
 * Signing in happens here too, as onboarding (docs/DESIGN.md,
 * decisions 15 and 16). Without a session the card shows only an email and
 * password form. The password never leaves the browser in clear: the card
 * takes its MD5 and encrypts that with Aqara's public RSA key, exactly as the
 * official app does, and sends only the encrypted block. The app uses it for
 * one login and keeps only the token.
 */

const CARD_VERSION = '1.16.0';

/* A radar fan. Decorative only: it never changes with state. */
const RADAR_ICON = `
  <circle cx="12" cy="19" r="1.4"/>
  <path d="M7.2 14.2a6.8 6.8 0 0 1 9.6 0" fill="none" stroke-width="1.6"/>
  <path d="M4.4 11.4a10.8 10.8 0 0 1 15.2 0" fill="none" stroke-width="1.6"/>
  <path d="M1.6 8.6a14.8 14.8 0 0 1 20.8 0" fill="none" stroke-width="1.6"/>
`;

/* The status icon: Material's cloud, with a mark cut out of it in the card's
 * background colour, one per level (owner, 2026-10-31). */
const CLOUD_PATH = 'M19.35,10.04C18.67,6.59 15.64,4 12,4C9.11,4 6.6,5.64 5.35,8.04'
  + 'C2.34,8.36 0,10.91 0,14A6,6 0 0,0 6,20H19A5,5 0 0,0 24,15C24,12.36 21.95,10.22 19.35,10.04Z';
const CLOUD_MARK = {
  good: 'M8.4 13.4l2.5 2.5 4.8-4.8',
  warn: 'M12 9.6v3.8M12 16.3v.1',
  bad: 'M9.5 10.5l5 5M14.5 10.5l-5 5',
  info: 'M8.6 13.2h.1M12 13.2h.1M15.4 13.2h.1',
};
const PANES = ['zones', 'settings', 'backups', 'account'];

const DEFAULTS = {
  title: 'Presence sensor',

  /* The app's own sensor: the devices it serves, the countries for the
   * sign-in and the session. */
  devices_entity: 'sensor.fp2_companion_devices',

  /* Pins the card to one FP2, by its key (the serial in lower case), its
   * entity prefix, its device id or its name. Empty shows the dropdown. */
  device: null,

  /* The app's command topics: `<root>/<key>/cmd/...` for one device and
   * `<root>/cmd/...` for the app itself (sign-in and sign-out). */
  command_root: 'fp2companion',

  /* Overrides for one entity id each, for a registry rename; the rest
   * follow from the device prefix. */
  entities: {},

  /* Every word on screen comes from here. */
  text: {
    no_status: 'No status from {entity}. Is the fp2_companion app running?',
    no_devices: 'No FP2 from {entity}. Is the fp2_companion app running?',
    device_missing: 'The FP2 {device} is not among the devices the app serves.',
    country: 'Country',
    sensitivity: 'Sensitivity',
    sensitivity_hint: 'How readily a person is detected.',
    sensitivity_by_ai: 'Set by AI person detection while it is on.',
    ai: 'AI person detection',
    ai_hint: 'The sensor decides by itself what is a person.',
    learning: 'AI learning',
    learning_hint: 'Learns the room. Takes about a minute.',
    learning_start: 'Start',
    learning_left: 'Learning {s} s',
    approach: 'Approach distance',
    approach_hint: 'How close someone must come before they count.',
    zones: 'Zones',
    tabs: { zones: 'Zones', settings: 'Settings', backups: 'Backups', account: 'Account' },
    section_account: 'Aqara account',
    section_ai: 'AI',
    section_detection: 'Detection',
    /* The live dots, one per target the FP2 tracks (owner, 2026-10-31). */
    live: {
      starting: 'Connecting to the live view',
      nobody: 'Live · nobody in view',
      one: 'Live · 1 person',
      many: 'Live · {n} people',
      interference: '{n} interference',
      retrying: 'Reconnecting the live view',
      stopped: 'Live view stopped',
      no_session: 'Live view needs a sign-in',
      refused: 'No live view on this account',
      not_admin: 'Live view needs an admin user',
    },
    interference: 'Interference',
    detection: 'Detection',
    edge: 'Edge',
    entrance: 'Entrance',
    type: 'Type',
    type_other: 'Other',
    overlap: 'Blocks in more than one zone: {n}',
    no_zones: 'No zones read yet.',
    legend_hint: 'Press a zone to pick it out on the map, or Edit to change it.',
    edit: 'Edit',
    new_zone: 'New zone',
    new_zone_name: 'Detection Zone {n}',
    edit_hint: 'Tap or drag on the map to add or remove blocks.',
    edge_hint: 'Only the blocks of the edge can change. Tap or drag on the map to add or remove them.',
    name: 'Name',
    save: 'Save',
    cancel: 'Cancel',
    delete: 'Delete',
    confirm: 'Press again',
    sending: 'Sent. The map updates when the device confirms.',
    backups: 'Backups',
    no_backups: 'No backups yet.',
    backup_choose: 'Choose a backup to see it on the map',
    preview_of: 'Backup of {when}',
    loading_preview: 'Loading the backup...',
    restore: 'Restore',
    close: 'Close',
    reason: {
      'first-read': 'First read',
      changed: 'Zones changed',
      'before-write': 'Before a change',
      'before-restore': 'Before a restore',
    },
    refresh: 'Refresh',
    last_read: 'Read {ago}',
    never_read: 'Not read yet',
    token: 'Session valid until {date}',
    sign_in_title: 'Sign in to Aqara',
    sign_in_hint: 'The password is encrypted on this device before it is sent, '
      + 'and only the session is kept.',
    email: 'Email',
    password: 'Password',
    sign_in: 'Sign in',
    choose_country: 'Choose your country',
    signing_in: 'Signing in...',
    signed_in_as: 'Signed in as {account}',
    signed_in: 'Signed in',
    change_login: 'Change login',
    log_out: 'Log out',
    /* One per status code the app publishes. The card decides on the code and
     * never on the app's message text. */
    status: {
      starting: 'Starting',
      ok: 'Cloud OK',
      device_unresolved: 'Device not found',
      no_session: 'Not signed in',
      cloud_error: 'Cloud error',
      write_not_applied: 'Write ignored',
      zone_write_failed: 'Zone change failed',
      login_failed: 'Sign-in failed',
      refused: 'Refused',
      not_on_account: 'Not on this account',
    },
  },
};

/* The status codes that need someone to act, against the ones that only
 * report. Kept beside the text so a new code gets both or neither. */
const STATUS_LEVEL = {
  ok: 'good',
  starting: 'info',
  refused: 'warn',
  write_not_applied: 'warn',
  zone_write_failed: 'warn',
  login_failed: 'bad',
  not_on_account: 'warn',
  cloud_error: 'warn',
  device_unresolved: 'bad',
  no_session: 'bad',
};

const SENSITIVITY = { 1: 'Low', 2: 'Medium', 3: 'High' };

/* Zone colours by position in the list, not by the app's colorId: two of the
 * live zones share colorId 0, and the map has to tell them apart. Mid-tone
 * hues so each reads against both theme backgrounds. */
const ZONE_COLOURS = ['#42a5f5', '#ffa726', '#66bb6a', '#ab47bc', '#ef5350',
  '#26c6da', '#d4e157', '#8d6e63', '#ec407a', '#7e57c2'];
const NEW_ZONE_COLOUR = '#ffee58';

const REGION_DETECTION = 0;
const REGION_INTERFERENCE = 1;
/* An entrance and exit zone, reported on a PS-T02E in PR #1 (2026-10-31).
 * No write of it was captured from the Aqara app, so the app never writes
 * it and the card offers no Edit for it. */
const REGION_ENTRANCE = 2;
/* The room's edge: every block OUTSIDE the room, where the FP2 does not look.
 * Read off the live Mancave FP2 on 2026-10-31, whose zone "Edge" covers 288
 * of 320 blocks and leaves out the 4 by 8 room that holds its other zones.
 * It is shown grey. Only its blocks can be redrawn, which is all the Aqara
 * app lets a person change on it (captured 2026-10-31); it has no name,
 * type or sensitivity to edit and is never created or deleted. */
const REGION_EDGE = 3;
const EDGE_COLOUR = 'var(--disabled-text-color, #9e9e9e)';
/* The type of a zone with none, as the server names it (zone_model.TAG_OTHER_ID). */
const TAG_OTHER = 'custom_Other';
const DEFAULT_GRID = { columns: 16, rows: 20 };
const CELL = 10;
/* How long an armed Delete or Restore waits for its second press. */
const ARM_MS = 4000;
/* A card renews its lease on the live positions this often; the app holds a
 * lease for 75 s, so one renewal can be missed. */
const LIVE_RENEW_MS = 30000;
/* Pushes come about every 0.75 s; this long without one greys the dots. */
const LIVE_STALE_MS = 5000;
const LIVE_DOT_R = 3.2;
/* How long "Sent" stays up if no new zone list arrives at all. */
const SENDING_MS = 20000;

const STYLE = `
:host { display: block; }
/* A display rule outranks the hidden attribute, so the banner, a flex box,
 * stayed on screen as an empty bar until this was added. */
[hidden] { display: none !important; }
ha-card { overflow: hidden; }
.body { padding: 12px 14px 14px; }

.head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.head svg { width: 22px; height: 22px; flex: 0 0 auto;
            stroke: var(--secondary-text-color); fill: var(--secondary-text-color); }
.head .title { font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
.head .spacer { flex: 1; }
.devsel { font: inherit; font-size: 13px; width: 100%; padding: 6px 8px;
          margin: 0 0 8px; box-sizing: border-box;
          border-radius: 6px; border: 1px solid var(--divider-color);
          background: var(--secondary-background-color);
          color: var(--primary-text-color); }
.cloud { display: flex; cursor: pointer; color: var(--secondary-text-color); }
.head .cloud svg { width: 28px; height: 28px; stroke: none; fill: none; }
.cloud .shape { fill: currentColor; }
.cloud .mark { fill: none; stroke: var(--card-background-color, var(--ha-card-background, #fff));
               stroke-width: 2.1; stroke-linecap: round; stroke-linejoin: round; }
.cloud.good { color: var(--success-color, #4caf50); }
.cloud.warn { color: var(--warning-color, #ffa726); }
.cloud.bad { color: var(--error-color, #f44336); }
.panetabs { display: flex; gap: 4px; margin: 6px 0 2px;
            border-bottom: 1px solid var(--divider-color); }
.panetabs button { border: none; background: none; padding: 6px 10px; border-radius: 0;
                   font-size: 13px; color: var(--secondary-text-color);
                   border-bottom: 2px solid transparent; margin-bottom: -1px; }
.panetabs button.on { color: var(--primary-text-color);
                      border-bottom-color: var(--primary-color); }
.message { font-size: 12px; color: var(--secondary-text-color); margin: 2px 0 6px; }
.message.warn { color: var(--warning-color, #ffa726); }
.message.bad { color: var(--error-color, #f44336); }

button { font: inherit; cursor: pointer; border-radius: 8px;
         border: 1px solid var(--divider-color);
         background: var(--secondary-background-color);
         color: var(--primary-text-color); }
button:disabled { opacity: .45; cursor: default; }
button:focus { outline: none; }
button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 1px; }
button.primary { background: var(--primary-color); border-color: var(--primary-color);
                 color: var(--text-primary-color, #fff); }
button.arm { background: var(--error-color, #f44336);
             border-color: var(--error-color, #f44336); color: #fff; }

.setting { display: flex; align-items: center; justify-content: space-between;
           gap: 10px; margin-top: 10px; flex-wrap: wrap; }
.setting .lhs { min-width: 0; flex: 1 1 140px; }
.setting .name { font-size: 13px; color: var(--primary-text-color); }
.setting .sub { font-size: 11px; color: var(--secondary-text-color); }
.seg { display: flex; flex: 0 0 auto; }
.seg button { padding: 6px 12px; font-size: 13px; border-radius: 0;
              margin-left: -1px; min-width: 64px; }
.seg button:first-child { border-radius: 8px 0 0 8px; margin-left: 0; }
.seg button:last-child { border-radius: 0 8px 8px 0; }
.seg button.on { background: var(--primary-color);
                 border-color: var(--primary-color);
                 color: var(--text-primary-color, #fff); position: relative; }
.seg button.pending { opacity: .6; }
.seg.off { opacity: .45; pointer-events: none; }
.toggle { width: 42px; height: 24px; border-radius: 12px; position: relative;
          border: 1px solid var(--divider-color); padding: 0;
          background: var(--secondary-background-color); flex: 0 0 auto; }
.toggle .knob { position: absolute; top: 2px; left: 2px; width: 18px;
                height: 18px; border-radius: 50%;
                background: var(--secondary-text-color);
                transition: left .12s ease, background .12s ease; }
.toggle.on { border-color: var(--primary-color); }
.toggle.on .knob { left: 21px; background: var(--primary-color); }
.toggle.pending { opacity: .6; }
.setting .act { padding: 6px 14px; font-size: 13px; min-width: 96px; }

.sect { margin-top: 16px; }
.sect .cap { font-size: 12px; font-weight: 500; color: var(--primary-text-color);
             margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }
.sect .cap .count { font-size: 11px; color: var(--secondary-text-color);
                    font-weight: 400; }
.sect .cap .spacer { flex: 1; }
.sect .cap button { padding: 4px 10px; font-size: 12px; font-weight: 400; }
/* A titled group of settings, ruled off from the one above it. */
.group { margin-top: 14px; }
.group:first-child { margin-top: 8px; }
.group .ghead { font-size: 11px; font-weight: 600; letter-spacing: .06em;
                text-transform: uppercase; color: var(--secondary-text-color);
                padding-bottom: 4px; border-bottom: 1px solid var(--divider-color); }
.group .setting:first-of-type { margin-top: 8px; }
.empty { font-size: 12px; color: var(--secondary-text-color); padding: 6px 0; }

/* The map is portrait, 16 by 20, so on a wide card it is capped and centred
 * rather than stretched into something taller than the screen. */
.map { display: block; width: 100%; max-width: 340px; margin: 0 auto;
       height: auto; }
.map.editing { touch-action: none; cursor: crosshair; }
.map .grid { stroke: var(--divider-color); stroke-width: .5; fill: none;
             pointer-events: none; }
.map .sensor { pointer-events: none; }
.map .sensor .ring { fill: var(--primary-color); opacity: .14; }
.map .sensor .core { fill: var(--primary-color); }
.map .frame { stroke: var(--secondary-text-color); stroke-width: 1; fill: none;
              opacity: .6; pointer-events: none; }
.map .cell { stroke: none; pointer-events: none; }
.map .cell.dim { opacity: .18; }
.map .hit { fill: transparent; }
.map .hit.on { stroke: var(--primary-text-color); stroke-width: .6; }
.map .label { font-size: 5px; font-weight: 600; text-anchor: middle;
              dominant-baseline: middle; fill: var(--primary-text-color);
              paint-order: stroke; stroke: var(--card-background-color, #1c1c1c);
              stroke-width: 2; pointer-events: none; }
.map .overlap { fill: none; stroke: var(--primary-text-color); stroke-width: 1;
                stroke-dasharray: 2 1.5; pointer-events: none; }
.map .live { pointer-events: none; }
.map .live circle { transition: cx .6s linear, cy .6s linear, opacity .3s; }
.map .live .person { fill: #ff5252; stroke: #fff; stroke-width: 1; }
.map .live .ghost { fill: none; stroke: var(--secondary-text-color);
  stroke-width: 1; stroke-dasharray: 1.6 1.2; }
/* A person pulses like the live line's dot: a halo under the dot that grows
 * and fades. It scales about its own centre, so it follows the dot as the
 * dot glides. */
.map .live .halo { fill: #ff5252; stroke: none; transform-box: fill-box;
                   transform-origin: center; animation: dothalo 1.6s ease-out infinite; }
@keyframes dothalo {
  0% { transform: scale(1); opacity: .55; }
  80%, 100% { transform: scale(2.8); opacity: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .map .live .halo, .livebar.on .dot { animation: none; }
  .map .live .halo { opacity: 0; }
}
.livebar { display: flex; align-items: center; justify-content: center; gap: 7px;
            font-size: 12px; color: var(--secondary-text-color); margin: 8px 0 2px; }
.livebar .dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto;
                background: var(--secondary-text-color); opacity: .6; }
.livebar.on { color: var(--primary-text-color); }
.livebar.on .dot { background: var(--success-color, #4caf50); opacity: 1;
                   animation: livepulse 1.6s ease-out infinite; }
.livebar.warn .dot { background: var(--warning-color, #ffa726); opacity: 1; }
@keyframes livepulse {
  0% { box-shadow: 0 0 0 0 rgba(76,175,80,.55); }
  70% { box-shadow: 0 0 0 7px rgba(76,175,80,0); }
  100% { box-shadow: 0 0 0 0 rgba(76,175,80,0); }
}

.banner { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
          margin-bottom: 8px; padding: 6px 8px; border-radius: 8px;
          background: var(--secondary-background-color); }
.banner .what { flex: 1 1 140px; font-size: 12px; color: var(--primary-text-color); }
.banner button { padding: 6px 12px; font-size: 13px; }

.legend { display: flex; flex-direction: column; gap: 2px; margin-top: 8px; }
/* Every row, the header included, shares one column template, so the type
 * and the sensitivity line up whatever a zone has (owner, 2026-10-30): the
 * interference zone leaves its sensitivity cell empty instead of shifting. */
.legend .row { display: grid; align-items: center; column-gap: 8px;
               grid-template-columns: 12px minmax(0, 1fr) 96px 62px 40px;
               padding: 4px 6px; border-radius: 6px; cursor: pointer; }
.legend .row.head { cursor: default; padding-top: 0; padding-bottom: 0; }
.legend .row.head div { font-size: 10px; color: var(--secondary-text-color);
                        text-transform: uppercase; letter-spacing: .04em; }
.legend .row.on { background: var(--secondary-background-color); }
.legend .sw { width: 12px; height: 12px; border-radius: 3px; }
.legend .nm { font-size: 13px; color: var(--primary-text-color); min-width: 0;
              overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.legend .meta { font-size: 11px; color: var(--secondary-text-color); min-width: 0;
                overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.legend .badge { font-size: 11px; line-height: 1; padding: 4px 0;
                 border-radius: 10px; width: 40px; }
.note { font-size: 11px; color: var(--secondary-text-color); margin-top: 4px; }

.editor { margin-top: 10px; padding: 10px; border-radius: 8px;
          border: 1px solid var(--divider-color); }
.editor .field { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
                 margin-bottom: 8px; }
.editor label { font-size: 12px; color: var(--secondary-text-color);
                min-width: 72px; }
.editor input, .editor select { flex: 1 1 140px; min-width: 0; font: inherit; font-size: 13px;
                padding: 6px 8px; border-radius: 6px;
                border: 1px solid var(--divider-color);
                background: var(--card-background-color, transparent);
                color: var(--primary-text-color); }
.editor .btns { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.editor .btns button { padding: 7px 14px; font-size: 13px; }
.editor .btns .spacer { flex: 1; }

/* The backups are one line: a list of many entries took more of the card
 * than the map, and a backup is looked at rarely (owner, 2026-10-30). */
.backups select { width: 100%; box-sizing: border-box; font: inherit;
                  font-size: 13px; padding: 7px 8px; border-radius: 8px;
                  border: 1px solid var(--divider-color);
                  background: var(--secondary-background-color);
                  color: var(--primary-text-color); }

.foot { display: flex; align-items: center; gap: 10px; margin-top: 14px;
        flex-wrap: wrap; }
.foot .info { flex: 1 1 160px; font-size: 11px; color: var(--secondary-text-color); }
.account { margin-top: 8px; font-size: 12px; color: var(--secondary-text-color); }
.account .who { font-size: 13px; color: var(--primary-text-color); overflow: hidden;
                text-overflow: ellipsis; white-space: nowrap; }
.account .sess { font-size: 11px; margin-top: 2px; }
.account .abtns { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.account button { padding: 5px 12px; font-size: 12px; }
.signin { margin-top: 10px; padding: 12px; border-radius: 8px;
          border: 1px solid var(--divider-color); }
.signin .ttl { font-size: 14px; font-weight: 500; color: var(--primary-text-color);
               margin-bottom: 4px; }
.signin .sub { font-size: 11px; color: var(--secondary-text-color);
               margin-bottom: 10px; }
.signin label { display: block; font-size: 12px; color: var(--secondary-text-color);
                margin: 8px 0 3px; }
.signin select { width: 100%; box-sizing: border-box; font: inherit;
                 font-size: 14px; padding: 8px; border-radius: 6px;
                 border: 1px solid var(--divider-color);
                 background: var(--card-background-color, transparent);
                 color: var(--primary-text-color); }
.signin input { width: 100%; box-sizing: border-box; font: inherit; font-size: 14px;
                padding: 8px; border-radius: 6px; border: 1px solid var(--divider-color);
                background: var(--card-background-color, transparent);
                color: var(--primary-text-color); }
.signin .btns { display: flex; gap: 8px; margin-top: 12px; }
.signin .btns button { padding: 8px 16px; font-size: 13px; }
.foot button { padding: 7px 14px; font-size: 13px; }
.err { padding: 16px; color: var(--error-color, #f44336); font-size: 13px; }
`;

/* ---------- helpers ---------- */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svgEl(tag, attributes) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  Object.keys(attributes || {}).forEach((k) => node.setAttribute(k, attributes[k]));
  return node;
}

function deepDefaults(user, defaults) {
  const out = Array.isArray(defaults) ? [] : {};
  Object.keys(defaults).forEach((key) => {
    const d = defaults[key];
    const u = user ? user[key] : undefined;
    if (d && typeof d === 'object' && !Array.isArray(d)) {
      out[key] = deepDefaults(u, d);
    } else {
      out[key] = u === undefined ? d : u;
    }
  });
  if (user) Object.keys(user).forEach((k) => { if (!(k in out)) out[k] = user[k]; });
  return out;
}

function tpl(text, vars) {
  return String(text || '').replace(/\{(\w+)\}/g, (m, k) => (
    vars[k] === undefined || vars[k] === null ? m : String(vars[k])
  ));
}

/* "2026-10-30T09:01:22" as the app publishes it, in local time. */
function parseStamp(text) {
  if (!text) return null;
  const t = Date.parse(text);
  return Number.isFinite(t) ? t : null;
}

function ago(ms, now) {
  if (ms === null) return null;
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

function shortTime(epochSeconds) {
  if (epochSeconds === null || epochSeconds === undefined) return '';
  const d = new Date(epochSeconds * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getDate())}/${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/* Each card entity as (domain, suffix after the device prefix): the ids the
 * app publishes with default_entity_id. */
const ENTITY_SUFFIX = {
  status: ['sensor', 'cloud_status'],
  zones: ['sensor', 'zones'],
  preview: ['sensor', 'backup_preview'],
  sensitivity: ['select', 'sensitivity'],
  approach: ['select', 'approach_distance'],
  refresh: ['button', 'refresh'],
  ai: ['switch', 'ai_detection'],
  learning: ['button', 'ai_learning'],
};
const DEVICE_STORE = 'fp2-companion-card.device';

function storedDevice() {
  try { return window.localStorage.getItem(DEVICE_STORE); } catch (e) { return null; }
}

function storeDevice(key) {
  try { window.localStorage.setItem(DEVICE_STORE, key); } catch (e) { /* private mode */ }
}

/* The column titles above the legend, in the same grid as its rows. */
/* The country to offer first: the session's, else the first of the browser's
 * languages that names a country on the list (el-GR is Greece, en-US the
 * United States), else none, so the person chooses (owner, 2026-10-31). */
function defaultCountry(sessionCountry, codes, languages) {
  if (sessionCountry && codes.includes(sessionCountry)) return sessionCountry;
  for (const lang of languages || []) {
    const region = String(lang).split('-')[1];
    if (region && codes.includes(region.toUpperCase())) return region.toUpperCase();
  }
  return '';
}

function legendHeader(text) {
  const row = el('div', 'row head');
  row.appendChild(el('div', ''));
  row.appendChild(el('div', ''));
  row.appendChild(el('div', '', text.type));
  row.appendChild(el('div', '', text.sensitivity));
  row.appendChild(el('div', ''));
  return row;
}

/* A zone's type as the Aqara app shows it: its tag name ("Sofa Area"), or
 * Interference for the interference zone, which has none. */
function zoneType(zone, text) {
  if (zone.regionType === REGION_INTERFERENCE) return text.interference;
  if (zone.regionType === REGION_EDGE) return text.edge;
  if (zone.regionType === REGION_ENTRANCE) return text.entrance;
  return zone.tagName || text.detection;
}

/* Only a detection zone has a sensitivity worth showing. */
function zoneSensitivity(zone) {
  return zone.regionType === REGION_DETECTION ? (SENSITIVITY[zone.sensitivity] || '') : '';
}

/* Block id to cell, both from 0. The same arithmetic as zone_model.cell(). */
function blockCell(block, columns) {
  return { row: Math.floor((block - 1) / columns), col: (block - 1) % columns };
}

/* ---------- the password, encrypted in the browser ---------- */

/* Aqara's public 1024-bit login key, the one the official app and
 * tools/login.py use (aqara_cloud.LOGIN_PUBKEY_PEM), as modulus and exponent. */
const AQARA_RSA_N = BigInt('0x86e3ab25079ef4d77249b3856f8f9715c8ca51f5bf81d85f9825'
  + '4eaa8411186e212621d5a914fa4eb818a40ecd8570f4b5f4c896ab522b9b126d908086baba88'
  + '99152de253faf3c2169449aa1df4b14917f6f9a1f4707f15599d8e6999f90d64881c83c11769'
  + '3133bd6af2cb66a18895d2866cad9cc11ec32e0700382d077107');
const AQARA_RSA_E = BigInt(65537);

/* MD5 over the UTF-8 bytes of a string, as lowercase hex: what Python's
 * hashlib.md5(password.encode()).hexdigest() gives. WebCrypto has no MD5. */
function md5Hex(text) {
  const bytes = new TextEncoder().encode(text);
  const K = [];
  for (let i = 0; i < 64; i += 1) K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32) >>> 0;
  const S = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
  const len = bytes.length;
  const padded = new Uint8Array((((len + 8) >> 6) + 1) * 64);
  padded.set(bytes);
  padded[len] = 0x80;
  const bits = len * 8;
  for (let i = 0; i < 8; i += 1) padded[padded.length - 8 + i] = Math.floor(bits / 2 ** (8 * i)) & 0xff;
  let a0 = 0x67452301; let b0 = 0xefcdab89; let c0 = 0x98badcfe; let d0 = 0x10325476;
  for (let off = 0; off < padded.length; off += 64) {
    const M = [];
    for (let i = 0; i < 16; i += 1) {
      M[i] = padded[off + i * 4] | (padded[off + i * 4 + 1] << 8)
        | (padded[off + i * 4 + 2] << 16) | (padded[off + i * 4 + 3] << 24);
    }
    let A = a0; let B = b0; let C = c0; let D = d0;
    for (let i = 0; i < 64; i += 1) {
      let F; let g;
      if (i < 16) { F = (B & C) | (~B & D); g = i; }
      else if (i < 32) { F = (D & B) | (~D & C); g = (5 * i + 1) % 16; }
      else if (i < 48) { F = B ^ C ^ D; g = (3 * i + 5) % 16; }
      else { F = C ^ (B | ~D); g = (7 * i) % 16; }
      const s = S[(i >> 4) * 4 + (i % 4)];
      const sum = (A + F + K[i] + M[g]) >>> 0;
      A = D; D = C; C = B;
      B = (B + ((sum << s) | (sum >>> (32 - s)))) >>> 0;
    }
    a0 = (a0 + A) >>> 0; b0 = (b0 + B) >>> 0; c0 = (c0 + C) >>> 0; d0 = (d0 + D) >>> 0;
  }
  return [a0, b0, c0, d0].map((w) => [0, 8, 16, 24]
    .map((sh) => ((w >>> sh) & 0xff).toString(16).padStart(2, '0')).join('')).join('');
}

function modPow(base, exp, mod) {
  let result = BigInt(1);
  let b = base % mod;
  let e = exp;
  while (e > BigInt(0)) {
    if (e & BigInt(1)) result = (result * b) % mod;
    e >>= BigInt(1);
    b = (b * b) % mod;
  }
  return result;
}

/* RSAES-PKCS1-v1_5 encryption, base64: the same block Python's
 * cryptography makes with padding.PKCS1v15(). WebCrypto offers only OAEP.
 * `randomBytes(n)` returns n random bytes; zeros are drawn again, because
 * the padding string must not contain any. */
function rsaPkcs1v15(message, n = AQARA_RSA_N, e = AQARA_RSA_E,
  randomBytes = (k) => crypto.getRandomValues(new Uint8Array(k))) {
  const k = Math.ceil(n.toString(16).length / 2);
  const msg = new TextEncoder().encode(message);
  if (msg.length > k - 11) throw new Error('message too long for the key');
  const ps = [];
  while (ps.length < k - 3 - msg.length) {
    randomBytes(k).forEach((x) => { if (x && ps.length < k - 3 - msg.length) ps.push(x); });
  }
  const em = [0, 2, ...ps, 0, ...msg];
  const m = BigInt(`0x${em.map((x) => x.toString(16).padStart(2, '0')).join('')}`);
  const hex = modPow(m, e, n).toString(16).padStart(k * 2, '0');
  let bin = '';
  for (let i = 0; i < hex.length; i += 2) bin += String.fromCharCode(parseInt(hex.slice(i, i + 2), 16));
  return btoa(bin);
}

/* What the app's login command takes in place of the password. */
function encryptPassword(password) {
  return rsaPkcs1v15(md5Hex(password));
}

/* A press that acts only on the second press within ARM_MS. The house
 * pattern from lg-oled-card: no dialog, the button itself asks. */
function armedButton(label, confirmLabel, action) {
  const b = el('button', '', label);
  let timer = null;
  b.addEventListener('click', () => {
    if (!b.classList.contains('arm')) {
      b.classList.add('arm');
      b.textContent = confirmLabel;
      timer = setTimeout(() => {
        b.classList.remove('arm');
        b.textContent = label;
      }, ARM_MS);
      return;
    }
    clearTimeout(timer);
    /* Disarmed at once, so a third press cannot send the command twice. */
    b.classList.remove('arm');
    b.textContent = label;
    action();
  });
  return b;
}

class Fp2CompanionCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    /* Optimistic values for the two selects, so a press shows at once instead
     * of waiting for the app's readback a few seconds later. */
    this._pending = {};
    this._seen = {};         // last state object per entity, to skip no-op updates
    /* The zone being edited: { rangeId (null for a new zone), regionType,
     * name, sensitivity, blocks (array, the order the cloud holds them, new
     * blocks appended), original }. Null when nothing is being edited. */
    this._edit = null;
    this._preview = null;    // backup name being previewed
    this._sentAt = null;     // when the last zone command went out
    this._focus = null;      // rangeId picked out from the legend
    this._paint = null;      // 'add' or 'remove' while a drag is painting
    this._sentText = null;   // what the message line says while waiting
    this._changingLogin = false;
    /* The live positions: which device this card holds a lease on, the
     * websocket unsubscribe, the renewal timer and the last message. Every
     * card (every browser tab) leases as its own client. */
    this._liveKey = null;
    this._liveUnsub = null;
    this._liveTimer = null;
    this._liveStale = null;
    this._liveMsg = null;
    this._liveAt = 0;
    this._liveNotAdmin = false;
    this._liveLayer = null;
    this._dots = new Map();
    this._liveClient = Math.random().toString(36).slice(2, 12);
    this._onVisibility = () => this._liveSync();
    this._observer = null;
    this._inView = true;
    /* The pane on show. Kept on the card, so a rebuild or a device switch
     * stays on it. */
    this._pane = 'zones';
  }

  connectedCallback() {
    this._connected = true;
    document.addEventListener('visibilitychange', this._onVisibility);
    /* The lease is held only while some of the card is on screen, so a
     * dashboard left open with the card scrolled away, or on a screen that
     * stays on, does not keep the FP2 streaming (owner, 2026-10-31). Without
     * IntersectionObserver the card counts as on screen. */
    if (typeof IntersectionObserver !== 'undefined' && !this._observer) {
      this._observer = new IntersectionObserver((entries) => {
        const entry = entries[entries.length - 1];
        this._inView = Boolean(entry && entry.isIntersecting);
        this._liveSync();
      });
      this._observer.observe(this);
    }
    this._liveSync();
  }

  disconnectedCallback() {
    this._connected = false;
    document.removeEventListener('visibilitychange', this._onVisibility);
    if (this._observer) this._observer.disconnect();
    this._observer = null;
    this._inView = true;
    this._liveSync();
  }

  static getStubConfig() {
    return { type: 'custom:fp2-companion-card' };
  }

  setConfig(config) {
    this._config = deepDefaults(config, DEFAULTS);
    this._refs = null;
    /* A new config can change the pin and the overrides, so the device and
     * its entity ids are worked out again on the next update. The stored
     * choice is read here once: set hass runs on every state change in the
     * house, and reading localStorage there would cost that many reads. */
    this._device = null;
    this._ents = null;
    this._chosen = this._chosen || storedDevice();
  }

  getCardSize() {
    return 10;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    const cfg = this._config;
    const hub = hass && hass.states && hass.states[cfg.devices_entity];
    const devices = (hub && hub.attributes && hub.attributes.devices) || [];
    if (!devices.length) {
      this._renderError(tpl(cfg.text.no_devices, { entity: cfg.devices_entity }));
      return;
    }
    const device = this._pickDevice(devices);
    if (!device) {
      this._renderError(tpl(cfg.text.device_missing, { device: cfg.device }));
      return;
    }
    if (!this._device || this._device.key !== device.key) this._switchTo(device);
    /* The picker and the country list are built with the card, so a new
     * device, a renamed one or a new country list rebuilds it. */
    const hubKey = JSON.stringify([devices, hub.attributes.countries]);
    if (this._refs && hubKey !== this._hubKey) this._refs = null;
    this._hubKey = hubKey;
    this._hub = hub;
    this._devices = devices;
    this._liveSync();
    const ents = this._ents;
    const status = hass.states[ents.status];
    if (!status) {
      this._renderError(tpl(cfg.text.no_status, { entity: ents.status }));
      return;
    }
    /* set hass runs on every state change in the house. Only this card's
     * entities matter here, so anything else returns at once. */
    const changed = !this._refs || hub !== this._seen.hub
      || Object.keys(ents).some((k) => hass.states[ents[k]] !== this._seen[k]);
    if (!changed) return;
    /* A new zone list, or a new status, answers the last zone command. */
    if (this._sentAt !== null && this._refs
        && (hass.states[ents.zones] !== this._seen.zones
            || hass.states[ents.status] !== this._seen.status)) {
      this._sentAt = null;
    }
    Object.keys(ents).forEach((k) => { this._seen[k] = hass.states[ents[k]]; });
    this._seen.hub = hub;
    if (!this._refs) this._build();
    this._update();
  }

  /* The pinned device, else the one this browser chose last, else the first. */
  _pickDevice(devices) {
    const pin = this._config.device;
    if (pin) {
      const p = String(pin).toLowerCase();
      return devices.find((d) => [d.key, d.prefix, d.did, d.name]
        .some((v) => String(v || '').toLowerCase() === p)) || null;
    }
    /* The choice is kept on the card as well, because a private window or a
     * blocked site storage has no localStorage and would fall back to the
     * first device on every update. */
    const wanted = this._chosen;
    return devices.find((d) => d.key === wanted) || devices[0];
  }

  _switchTo(device) {
    this._device = device;
    const ents = {};
    Object.keys(ENTITY_SUFFIX).forEach((k) => {
      const [domain, suffix] = ENTITY_SUFFIX[k];
      /* An override names one entity, so it holds only on a card pinned to
       * one device; on a card with the picker it would follow every device. */
      const override = this._config.device ? this._config.entities[k] : null;
      ents[k] = override || `${domain}.${device.prefix}_${suffix}`;
    });
    this._ents = ents;
    /* Everything shown belongs to the device, so a switch starts clean. */
    this._refs = null;
    this._seen = {};
    this._pending = {};
    this._edit = null;
    this._preview = null;
    this._sentAt = null;
  }

  _chooseDevice(key) {
    this._chosen = key;
    storeDevice(key);
    const device = (this._devices || []).find((d) => d.key === key);
    if (device && this._hass) {
      this._switchTo(device);
      this.hass = this._hass;
    }
  }

  _renderError(message) {
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>`;
    const card = document.createElement('ha-card');
    card.appendChild(el('div', 'err', message));
    this.shadowRoot.appendChild(card);
    this._refs = null;
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent('hass-more-info', {
      detail: { entityId }, bubbles: true, composed: true,
    }));
  }

  _state(key) {
    const id = this._ents[key];
    return (this._hass && this._hass.states[id]) || null;
  }

  /* A device command; `app` sends to the app itself (sign-in, sign-out).
   * `device` names another device than the one on screen. */
  _command(key, payload, app = false, device = null) {
    if (!this._hass) return;
    const root = this._config.command_root;
    const dkey = device || this._device.key;
    this._hass.callService('mqtt', 'publish', {
      topic: app ? `${root}/cmd/${key}` : `${root}/${dkey}/cmd/${key}`,
      payload: typeof payload === 'string' ? payload : JSON.stringify(payload),
    });
  }

  _selectPane(key) {
    if (!PANES.includes(key)) return;
    this._pane = key;
    this._showPane();
    this._liveSync();
  }

  _showPane() {
    const r = this._refs;
    if (!r || !r.panes) return;
    PANES.forEach((key) => {
      r.panes[key].hidden = key !== this._pane;
      r.tabs[key].className = key === this._pane ? 'on' : '';
    });
  }

  /* ---------- live positions ---------- */

  /* Hold a lease on the device on screen while the card is attached and on
   * screen, the page is visible and there is a session; drop it otherwise. Called from
   * set hass, so it returns at once when nothing changed. */
  _liveSync() {
    const signedIn = Boolean(this._hub && this._hub.attributes
      && this._hub.attributes.signed_in);
    const wanted = this._connected && this._hass && this._device && signedIn
      && document.visibilityState !== 'hidden' && this._inView
      && this._pane === 'zones' && this._preview === null && !this._liveNotAdmin
      ? this._device.key : null;
    if (wanted === this._liveKey) return;
    this._liveStop();
    if (wanted) this._liveStart(wanted);
  }

  _liveStart(key) {
    this._liveKey = key;
    this._liveMsg = null;
    this._liveAt = 0;
    const topic = `${this._config.command_root}/${key}/live`;
    /* The positions are not an entity: the app publishes them straight to the
     * broker and the card listens through Home Assistant's own MQTT
     * subscription, which is open to admin users only. */
    const conn = this._hass.connection;
    if (conn && conn.subscribeMessage) {
      conn.subscribeMessage((ev) => this._onLive(key, ev),
        { type: 'mqtt/subscribe', topic })
        .then((unsub) => {
          if (this._liveKey === key) this._liveUnsub = unsub;
          else unsub();
        })
        .catch(() => {
          if (this._liveKey !== key) return;
          this._liveNotAdmin = true;
          this._liveStop();
          this._drawLive();
        });
    }
    const lease = () => this._command('track',
      { client: this._liveClient, on: true }, false, key);
    lease();
    this._liveTimer = setInterval(lease, LIVE_RENEW_MS);
    this._drawLive();
  }

  _liveStop() {
    const key = this._liveKey;
    if (this._liveTimer) clearInterval(this._liveTimer);
    if (this._liveStale) clearTimeout(this._liveStale);
    this._liveTimer = null;
    this._liveStale = null;
    if (this._liveUnsub) {
      try { this._liveUnsub(); } catch (e) { /* already closed */ }
    }
    this._liveUnsub = null;
    this._liveKey = null;
    this._liveMsg = null;
    if (key) {
      this._command('track', { client: this._liveClient, on: false }, false, key);
    }
    this._drawLive();
  }

  _onLive(key, ev) {
    if (key !== this._liveKey) return;
    let msg;
    try {
      msg = JSON.parse(ev.payload);
    } catch (e) {
      return;
    }
    if (!msg || typeof msg !== 'object') return;
    this._liveMsg = msg;
    this._liveAt = Date.now();
    if (this._liveStale) clearTimeout(this._liveStale);
    this._liveStale = setTimeout(() => this._drawLive(), LIVE_STALE_MS + 50);
    this._drawLive();
  }

  /* The dots and the chip. A dot is kept per target id and moved, so a CSS
   * transition carries it from one push to the next. */
  _drawLive() {
    const r = this._refs;
    const t = this._config.text.live;
    const msg = this._liveMsg;
    /* The FP2 sends no push while it tracks nobody, so "watching" (subscribed,
     * no push yet) and a stream that went quiet both mean an empty room. */
    const stale = msg && msg.state === 'live'
      && Date.now() - this._liveAt > LIVE_STALE_MS;
    const live = msg && (msg.state === 'watching' || msg.state === 'live');
    const targets = live && !stale && Array.isArray(msg.targets)
      ? msg.targets : [];
    if (r && r.liveBar) {
      let text = '';
      let level = '';
      if (this._liveNotAdmin) {
        text = t.not_admin;
        level = 'warn';
      } else if (live) {
        const people = targets.filter((tg) => Number(tg.type) !== 1).length;
        const ghosts = targets.length - people;
        text = people === 0 ? t.nobody : (people === 1 ? t.one : tpl(t.many, { n: people }));
        if (ghosts) text += ` · ${tpl(t.interference, { n: ghosts })}`;
        level = 'on';
      } else if (msg) {
        text = t[msg.state] || '';
        level = ['refused', 'no_session', 'retrying'].includes(msg.state) ? 'warn' : '';
      } else if (this._liveKey) {
        text = t.starting;
      }
      r.liveText.textContent = text;
      r.liveBar.className = `livebar${level ? ` ${level}` : ''}`;
      r.liveBar.hidden = !text || this._preview !== null;
    }
    const layer = this._liveLayer;
    if (!layer || !layer.isConnected) return;
    const grid = (this._za && this._za.grid) || DEFAULT_GRID;
    const seen = new Set();
    targets.forEach((tg) => {
      const col = Number(tg.col);
      const row = Number(tg.row);
      if (!Number.isFinite(col) || !Number.isFinite(row)
          || col < 0 || row < 0 || col > grid.columns || row > grid.rows) return;
      const id = String(tg.id);
      seen.add(id);
      let dot = this._dots.get(id);
      if (!dot) {
        /* The halo goes in first, so it is drawn under the dot. */
        dot = { halo: svgEl('circle', { class: 'halo', r: LIVE_DOT_R }),
          core: svgEl('circle', { r: LIVE_DOT_R }) };
        this._dots.set(id, dot);
        layer.appendChild(dot.halo);
        layer.appendChild(dot.core);
      }
      const ghost = Number(tg.type) === 1;
      dot.core.setAttribute('class', ghost ? 'ghost' : 'person');
      dot.halo.style.display = ghost ? 'none' : '';
      const cx = (col * CELL).toFixed(1);
      const cy = (row * CELL).toFixed(1);
      [dot.halo, dot.core].forEach((c) => {
        c.setAttribute('cx', cx);
        c.setAttribute('cy', cy);
      });
    });
    [...this._dots.keys()].forEach((id) => {
      if (seen.has(id)) return;
      const dot = this._dots.get(id);
      dot.halo.remove();
      dot.core.remove();
      this._dots.delete(id);
    });
  }

  /* ---------- build ---------- */

  _build() {
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>`;
    const card = document.createElement('ha-card');
    const body = el('div', 'body');
    const t = this._config.text;
    const r = {};

    const head = el('div', 'head');
    const icon = svgEl('svg', { viewBox: '0 0 24 24', 'aria-hidden': 'true' });
    icon.innerHTML = RADAR_ICON;
    head.appendChild(icon);
    head.appendChild(el('div', 'title', this._config.title));
    head.appendChild(el('div', 'spacer'));
    /* The cloud says how things stand; the message line under the head says
     * why when it is not green. */
    r.cloud = el('div', 'cloud');
    r.cloud.setAttribute('role', 'img');
    const cloud = svgEl('svg', { viewBox: '0 0 24 24' });
    cloud.appendChild(svgEl('path', { class: 'shape', d: CLOUD_PATH }));
    r.cloudMark = svgEl('path', { class: 'mark', d: '' });
    cloud.appendChild(r.cloudMark);
    r.cloud.appendChild(cloud);
    r.cloud.addEventListener('click', () => this._moreInfo(this._ents.status));
    head.appendChild(r.cloud);
    body.appendChild(head);
    /* The device picker, unless the card is pinned or there is one FP2. It
     * has a row of its own under the head, so the full name and area fit
     * (owner, 2026-10-31). */
    if (!this._config.device && (this._devices || []).length > 1) {
      const pick = el('select', 'devsel');
      pick.setAttribute('aria-label', 'FP2');
      this._devices.forEach((d) => {
        const opt = el('option', '', d.area ? `${d.name} · ${d.area}` : d.name);
        opt.value = d.key;
        if (d.key === this._device.key) opt.selected = true;
        pick.appendChild(opt);
      });
      pick.addEventListener('change', () => this._chooseDevice(pick.value));
      body.appendChild(pick);
    }
    r.message = el('div', 'message', '');
    body.appendChild(r.message);

    r.signin = this._buildSignin();
    /* Signed out, the form stands here in place of the tabs; signed in, a
     * Change login opens it on the Account pane (_update moves it). */
    r.signinSlot = el('div', '');
    r.signinSlot.appendChild(r.signin.panel);
    body.appendChild(r.signinSlot);
    /* Everything below needs a session, so it is one block the sign-in can
     * hide while there is none. */
    const main = el('div', '');
    r.main = main;
    body.appendChild(main);
    /* Three panes: the map, the device's own settings, and the backups. */
    const tabs = el('div', 'panetabs');
    r.tabs = {};
    r.panes = {};
    PANES.forEach((key) => {
      const b = el('button', '', t.tabs[key]);
      b.addEventListener('click', () => this._selectPane(key));
      tabs.appendChild(b);
      r.tabs[key] = b;
      r.panes[key] = el('div', 'pane');
    });
    main.appendChild(tabs);
    PANES.forEach((key) => main.appendChild(r.panes[key]));
    /* Two groups: AI first, because while it is on it sets the
     * sensitivity below it (owner, 2026-10-31). */
    const group = (title) => {
      const g = el('div', 'group');
      g.appendChild(el('div', 'ghead', title));
      r.panes.settings.appendChild(g);
      return g;
    };
    const ai = group(t.section_ai);
    r.ai = this._aiRow(ai);
    r.learning = this._learningRow(ai);
    const detection = group(t.section_detection);
    r.sensitivity = this._setting(detection, t.sensitivity, t.sensitivity_hint, 'sensitivity');
    r.approach = this._setting(detection, t.approach, t.approach_hint, 'approach');

    const zones = el('div', 'sect');
    const cap = el('div', 'cap');
    cap.appendChild(el('span', '', t.zones));
    r.zoneCount = el('span', 'count', '');
    cap.appendChild(r.zoneCount);
    cap.appendChild(el('span', 'spacer'));

    r.newZone = el('button', '', t.new_zone);
    r.newZone.addEventListener('click', () => this._startEdit(null));
    cap.appendChild(r.newZone);
    zones.appendChild(cap);
    r.banner = el('div', 'banner');
    zones.appendChild(r.banner);
    r.mapHolder = el('div', '');
    zones.appendChild(r.mapHolder);
    /* How the live view stands, under the map it is about. */
    r.liveBar = el('div', 'livebar');
    r.liveBar.appendChild(el('span', 'dot'));
    r.liveText = el('span', '', '');
    r.liveBar.appendChild(r.liveText);
    r.liveBar.hidden = true;
    zones.appendChild(r.liveBar);
    r.editor = el('div', 'editor');
    zones.appendChild(r.editor);
    r.legend = el('div', 'legend');
    zones.appendChild(r.legend);
    r.note = el('div', 'note', '');
    zones.appendChild(r.note);
    r.panes.zones.appendChild(zones);

    const backups = el('div', 'sect');
    const bcap = el('div', 'cap');
    bcap.appendChild(el('span', '', t.backups));
    r.backupCount = el('span', 'count', '');
    bcap.appendChild(r.backupCount);
    backups.appendChild(bcap);
    r.backups = el('div', 'backups');
    backups.appendChild(r.backups);
    r.panes.backups.appendChild(backups);

    /* The account has a pane of its own, so it is not under every other one
     * (owner, 2026-10-31). */
    const accountGroup = el('div', 'group');
    accountGroup.appendChild(el('div', 'ghead', t.section_account));
    r.account = el('div', 'account');
    r.who = el('div', 'who', '');
    r.account.appendChild(r.who);
    r.session = el('div', 'sess', '');
    r.account.appendChild(r.session);
    const abtns = el('div', 'abtns');
    r.change = el('button', '', t.change_login);
    r.change.addEventListener('click', () => {
      this._changingLogin = !this._changingLogin;
      this._update();
    });
    abtns.appendChild(r.change);
    abtns.appendChild(armedButton(t.log_out, t.confirm, () => {
      this._changingLogin = false;
      this._command('logout', '', true);
    }));
    r.account.appendChild(abtns);
    accountGroup.appendChild(r.account);
    r.panes.account.appendChild(accountGroup);

    const foot = el('div', 'foot');
    r.info = el('div', 'info', '');
    foot.appendChild(r.info);
    r.refresh = el('button', '', t.refresh);
    r.refresh.addEventListener('click', () => this._press());
    foot.appendChild(r.refresh);
    body.appendChild(foot);

    card.appendChild(body);
    this.shadowRoot.appendChild(card);
    this._refs = r;
    this._showPane();
    this._zonesKey = null;
    this._backupsKey = null;
  }

  /* The sign-in form. A real form element, so Enter submits and a password
   * manager recognises it. */
  _buildSignin() {
    const t = this._config.text;
    const panel = el('form', 'signin');
    panel.appendChild(el('div', 'ttl', t.sign_in_title));
    panel.appendChild(el('div', 'sub', t.sign_in_hint));
    const emailLabel = el('label', '', t.email);
    const email = el('input', '');
    email.type = 'email';
    email.autocomplete = 'username';
    email.setAttribute('autocomplete', 'username');
    emailLabel.appendChild(email);
    panel.appendChild(emailLabel);
    /* The country picks the region and its servers, as in the Aqara app. */
    const countryLabel = el('label', '', t.country);
    const country = el('select', '');
    const hubAttrs = (this._hub && this._hub.attributes) || {};
    const countries = hubAttrs.countries || [];
    const chosen = defaultCountry(hubAttrs.country, countries.map(([code]) => code),
      (typeof navigator !== 'undefined' && navigator.languages) || []);
    const none = el('option', '', t.choose_country);
    none.value = '';
    none.disabled = true;
    country.appendChild(none);
    countries.forEach(([code, name]) => {
      const opt = el('option', '', name);
      opt.value = code;
      country.appendChild(opt);
    });
    country.value = chosen;
    countryLabel.appendChild(country);
    panel.appendChild(countryLabel);
    this._country = country;
    const pwLabel = el('label', '', t.password);
    const password = el('input', '');
    password.type = 'password';
    password.setAttribute('autocomplete', 'current-password');
    pwLabel.appendChild(password);
    panel.appendChild(pwLabel);
    const btns = el('div', 'btns');
    const submit = el('button', 'primary', t.sign_in);
    submit.type = 'submit';
    /* Signing in needs the country, because it picks the servers. */
    const ready = () => { submit.disabled = !country.value; };
    country.addEventListener('change', ready);
    ready();
    btns.appendChild(submit);
    const cancel = el('button', '', t.cancel);
    cancel.type = 'button';
    cancel.addEventListener('click', () => {
      this._changingLogin = false;
      this._update();
    });
    btns.appendChild(cancel);
    panel.appendChild(btns);
    panel.addEventListener('submit', (ev) => {
      ev.preventDefault();
      this._submitLogin(email.value, password.value);
      /* Cleared at once: the field is the only place the clear password
       * ever was. */
      password.value = '';
    });
    return { panel, email, password, submit, cancel };
  }

  _submitLogin(account, password) {
    if (!account.trim() || !password) return;
    if (!this._country || !this._country.value) return;
    let blob;
    try {
      blob = encryptPassword(password);
    } catch (err) {
      return;
    }
    this._command('login', { account: account.trim(), password_rsa: blob,
      country: this._country.value }, true);
    this._changingLogin = false;
    this._sentText = this._config.text.signing_in;
    this._sentAt = Date.now();
    this._update();
  }

  /* One setting: a name, a hint and a segmented control built from the
   * select's own options, so the card never lists a value the app does not. */
  _setting(parent, name, hint, key) {
    const row = el('div', 'setting');
    const lhs = el('div', 'lhs');
    lhs.appendChild(el('div', 'name', name));
    lhs.appendChild(el('div', 'sub', hint));
    lhs.addEventListener('click', () => this._moreInfo(this._ents[key]));
    row.appendChild(lhs);
    const seg = el('div', 'seg');
    row.appendChild(seg);
    parent.appendChild(row);
    return { seg, buttons: {}, options: null, sub: lhs.lastChild, hint };
  }

  /* The AI switch is a switch entity, toggled through Home Assistant so the
   * card and the more-info dialog are the same control. */
  _aiRow(parent) {
    const t = this._config.text;
    const row = el('div', 'setting');
    const lhs = el('div', 'lhs');
    lhs.appendChild(el('div', 'name', t.ai));
    lhs.appendChild(el('div', 'sub', t.ai_hint));
    lhs.addEventListener('click', () => this._moreInfo(this._ents.ai));
    row.appendChild(lhs);
    const toggle = el('button', 'toggle');
    toggle.appendChild(el('span', 'knob'));
    toggle.setAttribute('aria-label', t.ai);
    toggle.addEventListener('click', () => {
      const st = this._state('ai');
      if (!st || !this._hass) return;
      const on = st.state === 'on';
      this._pending.ai = { option: on ? 'off' : 'on', at: Date.now() };
      this._hass.callService('switch', on ? 'turn_off' : 'turn_on',
        { entity_id: this._ents.ai });
      this._update();
    });
    row.appendChild(toggle);
    parent.appendChild(row);
    return { row, toggle };
  }

  _learningRow(parent) {
    const t = this._config.text;
    const row = el('div', 'setting');
    const lhs = el('div', 'lhs');
    lhs.appendChild(el('div', 'name', t.learning));
    lhs.appendChild(el('div', 'sub', t.learning_hint));
    row.appendChild(lhs);
    const button = el('button', 'act', t.learning_start);
    button.addEventListener('click', () => {
      if (!this._hass) return;
      this._hass.callService('button', 'press',
        { entity_id: this._ents.learning });
    });
    row.appendChild(button);
    parent.appendChild(row);
    return { row, button };
  }

  /* The AI row and the learning row, and the countdown. Nothing on the
   * cloud reports learning progress, so the app publishes when the run
   * should end and the card counts down to it, once a second while it runs. */
  _updateAi(sa) {
    const r = this._refs;
    const t = this._config.text;
    const st = this._state('ai');
    r.ai.row.hidden = !st;
    const actual = st ? st.state : null;
    const pending = this._pending.ai;
    if (pending && (pending.option === actual || Date.now() - pending.at > 15000)) {
      delete this._pending.ai;
    }
    const shown = this._pending.ai ? this._pending.ai.option : actual;
    r.ai.toggle.className = `toggle${shown === 'on' ? ' on' : ''}${this._pending.ai ? ' pending' : ''}`;
    r.ai.toggle.disabled = !st || actual === 'unavailable';

    /* With AI on the device ignores sensitivity and reports High. */
    const aiOn = actual === 'on';
    r.sensitivity.seg.className = aiOn ? 'seg off' : 'seg';
    r.sensitivity.sub.textContent = aiOn ? t.sensitivity_by_ai : r.sensitivity.hint;

    r.learning.row.hidden = !this._state('learning');
    const until = parseStamp(sa.learning_until);
    const left = until === null ? 0 : Math.ceil((until - Date.now()) / 1000);
    clearTimeout(this._learningTimer);
    if (left > 0) {
      r.learning.button.disabled = true;
      r.learning.button.textContent = tpl(t.learning_left, { s: left });
      this._learningTimer = setTimeout(() => this._updateAi(sa), 1000);
    } else {
      r.learning.button.disabled = false;
      r.learning.button.textContent = t.learning_start;
    }
  }

  _select(key, option) {
    const entity = this._ents[key];
    if (!this._hass || !entity) return;
    this._pending[key] = { option, at: Date.now() };
    this._hass.callService('select', 'select_option', { entity_id: entity, option });
    this._update();
  }

  _press() {
    const entity = this._ents.refresh;
    if (!this._hass || !entity) return;
    this._hass.callService('button', 'press', { entity_id: entity });
  }

  /* ---------- update ---------- */

  _update() {
    const r = this._refs;
    const t = this._config.text;
    const status = this._state('status');
    const sa = status.attributes || {};
    const code = status.state;
    const level = STATUS_LEVEL[code] || 'warn';
    const label = t.status[code] || code;
    r.cloud.className = `cloud ${level}`;
    r.cloud.title = label;
    r.cloud.setAttribute('aria-label', label);
    r.cloudMark.setAttribute('d', CLOUD_MARK[level] || CLOUD_MARK.warn);
    /* The app's own sentence is shown, never parsed: it explains the code. A
     * refusal adds the reason the app gave, for the same purpose. */
    let message = code === 'ok' ? '' : (sa.message || '');
    if (code === 'refused' && sa.detail && sa.detail.reason) {
      message = `${message} ${sa.detail.reason}`.trim();
    }
    if (this._sentAt !== null) {
      if (Date.now() - this._sentAt > SENDING_MS) this._sentAt = null;
      else message = this._sentText || t.sending;
    }
    r.message.textContent = message;
    r.message.className = `message ${this._sentAt !== null ? 'info' : level}`;
    r.message.hidden = !message;

    /* Signed in is the app's own flag for "the session has a token". The
     * account is only shown: a session made by tools/login.py before
     * 2026-10-30 has a token and no account, and 1.3.0 took that for signed
     * out. An app too old to publish the flag is judged by the status. */
    const signedIn = typeof sa.signed_in === 'boolean'
      ? sa.signed_in && code !== 'no_session' : code !== 'no_session';
    if (!signedIn) this._changingLogin = false;
    r.main.hidden = !signedIn;
    const formHome = signedIn ? r.panes.account : r.signinSlot;
    if (r.signin.panel.parentNode !== formHome) formHome.appendChild(r.signin.panel);
    r.who.textContent = sa.account ? tpl(t.signed_in_as, { account: sa.account }) : t.signed_in;
    r.change.className = this._changingLogin ? 'on' : '';
    r.signin.panel.hidden = signedIn && !this._changingLogin;
    r.signin.cancel.hidden = !signedIn;
    if (r.signin.panel.hidden) r.signin.password.value = '';
    r.refresh.hidden = !signedIn;

    this._updateSetting('sensitivity', r.sensitivity);
    this._updateSetting('approach', r.approach);
    this._updateAi(sa);

    const zonesState = this._state('zones');
    const za = (zonesState && zonesState.attributes) || {};
    this._za = za;
    this._updateZones();
    this._updateBackups(za.backups || []);

    const now = Date.now();
    const read = parseStamp(sa.last_read);
    r.info.textContent = read === null ? t.never_read : tpl(t.last_read, { ago: ago(read, now) });
    const exp = parseStamp(sa.token_expires);
    const d = exp === null ? null : new Date(exp);
    r.session.textContent = d ? tpl(t.token, { date: `${d.getDate()}/${d.getMonth() + 1}` }) : '';
  }

  _updateSetting(key, ref) {
    const st = this._state(key);
    const options = (st && st.attributes && st.attributes.options) || [];
    if (ref.options !== options.join('|')) {
      ref.seg.innerHTML = '';
      ref.buttons = {};
      options.forEach((opt) => {
        const b = el('button', '', opt);
        b.addEventListener('click', () => this._select(key, opt));
        ref.seg.appendChild(b);
        ref.buttons[opt] = b;
      });
      ref.options = options.join('|');
    }
    const actual = st ? st.state : null;
    const pending = this._pending[key];
    /* A pending press stands until the entity reports that value, or for
     * fifteen seconds, which is several readbacks: after that the device's
     * own answer wins, and the status pill says why. */
    if (pending && (pending.option === actual || Date.now() - pending.at > 15000)) {
      delete this._pending[key];
    }
    const shown = this._pending[key] ? this._pending[key].option : actual;
    Object.keys(ref.buttons).forEach((opt) => {
      const on = opt === shown;
      ref.buttons[opt].className = on
        ? (this._pending[key] ? 'on pending' : 'on') : '';
      ref.buttons[opt].disabled = !st || st.state === 'unavailable';
    });
  }

  /* The zone section has three modes: the live zones, one backup previewed,
   * or one zone being edited. Each redraw is keyed on what it shows, so a
   * state change elsewhere in the house does not rebuild the map. */
  _updateZones() {
    const r = this._refs;
    const t = this._config.text;
    const za = this._za || {};
    const zones = Array.isArray(za.zones) ? za.zones : [];
    const grid = za.grid || DEFAULT_GRID;
    const preview = this._previewZones();
    const key = JSON.stringify([zones, grid, this._edit && this._edit.rangeId,
      this._edit !== null, this._preview, preview && preview.name, this._focus]);
    if (key === this._zonesKey) return;
    this._zonesKey = key;
    this._liveSync();

    r.zoneCount.textContent = zones.length ? String(zones.length) : '';
    r.newZone.hidden = this._edit !== null || this._preview !== null || !zones.length;
    r.mapHolder.innerHTML = '';
    r.legend.innerHTML = '';
    r.editor.innerHTML = '';
    r.banner.innerHTML = '';
    r.editor.hidden = this._edit === null;
    r.banner.hidden = this._preview === null;

    this._liveLayer = null;
    if (this._preview !== null) {
      this._drawPreview(preview, grid);
      return;
    }
    if (!zones.length) {
      r.mapHolder.appendChild(el('div', 'empty', t.no_zones));
      r.note.textContent = '';
      return;
    }
    const colours = this._zoneColours(zones);
    const map = this._drawMap(zones, grid, colours, this._edit,
      this._edit === null ? this._focus : null);
    /* The dots go on the zone map only, never on a backup preview, and on
     * top of everything; they take no pointer events, so editing works
     * through them. */
    this._liveLayer = svgEl('g', { class: 'live' });
    this._dots = new Map();
    map.appendChild(this._liveLayer);
    r.mapHolder.appendChild(map);
    this._drawLive();
    if (this._edit !== null) this._drawEditor();

    r.legend.appendChild(legendHeader(t));
    zones.forEach((z) => {
      const editing = this._edit !== null && this._edit.rangeId === z.rangeId;
      const picked = this._edit === null && this._focus === z.rangeId;
      const row = el('div', `row${editing || picked ? ' on' : ''}`);
      const sw = el('div', 'sw');
      sw.style.background = colours.get(z.rangeId);
      row.appendChild(sw);
      row.appendChild(el('div', 'nm', z.name));
      /* The zone's type, which the Aqara app calls its area type, and its
       * sensitivity; the block count meant nothing to anyone (owner,
       * 2026-10-30). */
      row.appendChild(el('div', 'meta type', zoneType(z, t)));
      row.appendChild(el('div', 'meta sens', zoneSensitivity(z)));
      /* Editing has its own button, so a press on the row only picks the zone
       * out on the map and can never start an edit by accident. While one
       * zone is being edited the other badges are hidden, because switching
       * would throw the draft away. */
      const editable = z.regionType !== REGION_ENTRANCE;
      if (this._edit !== null || !editable) row.appendChild(el('div', ''));
      if (this._edit === null) {
        if (editable) {
          const badge = el('button', 'badge', t.edit);
          badge.addEventListener('click', (ev) => {
            ev.stopPropagation();
            this._startEdit(z);
          });
          row.appendChild(badge);
        }
        row.addEventListener('click', () => {
          this._focus = this._focus === z.rangeId ? null : z.rangeId;
          this._updateZones();
        });
      }
      r.legend.appendChild(row);
    });

    const overlaps = this._overlaps(zones);
    const notes = [];
    if (overlaps.size) notes.push(tpl(t.overlap, { n: overlaps.size }));
    if (this._edit === null) notes.push(t.legend_hint);
    r.note.textContent = notes.join(' · ');
  }

  _zoneColours(zones) {
    const colours = new Map();
    let i = 0;
    zones.forEach((z) => {
      if (z.regionType === REGION_INTERFERENCE) {
        colours.set(z.rangeId, 'var(--secondary-text-color)');
      } else if (z.regionType === REGION_EDGE) {
        colours.set(z.rangeId, EDGE_COLOUR);
      } else {
        colours.set(z.rangeId, ZONE_COLOURS[i % ZONE_COLOURS.length]);
        i += 1;
      }
    });
    return colours;
  }

  /* The edge is left out: it is the space around the room, not a zone that
   * can share a block with one. */
  _overlaps(zones) {
    const owners = new Map();
    zones.filter((z) => z.regionType !== REGION_EDGE)
      .forEach((z) => (z.blockList || []).forEach((b) => {
      owners.set(b, (owners.get(b) || 0) + 1);
    }));
    return new Set([...owners].filter(([, n]) => n > 1).map(([b]) => b));
  }

  /* `edit`, when given, is the zone being edited: its saved blocks are not
   * drawn, every other zone is dimmed, and a layer of one tappable cell per
   * block carries the draft. Painting changes those cells in place rather
   * than redrawing, because a redraw in the middle of a drag would destroy
   * the element the pointer is on and end the drag. */
  _drawMap(zones, grid, colours, edit, focus = null) {
    const cols = grid.columns;
    const rows = grid.rows;
    const w = cols * CELL;
    const h = rows * CELL;
    const svg = svgEl('svg', { class: `map${edit ? ' editing' : ''}`,
      viewBox: `-1 -1 ${w + 2} ${h + 2}`, role: 'img',
      'aria-label': this._config.text.zones });

    /* The interference zone is hatched rather than filled, so it reads as
     * "ignored here" and not as one more place a person is looked for. */
    const defs = svgEl('defs', {});
    const hatch = svgEl('pattern', { id: 'hatch', width: 4, height: 4,
      patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' });
    hatch.appendChild(svgEl('rect', { width: 4, height: 4,
      fill: 'var(--secondary-text-color)', opacity: '0.18' }));
    hatch.appendChild(svgEl('line', { x1: 0, y1: 0, x2: 0, y2: 4,
      stroke: 'var(--secondary-text-color)', 'stroke-width': 1.4, opacity: '0.7' }));
    defs.appendChild(hatch);
    svg.appendChild(defs);

    /* The edge first, then interference, so detection zones are drawn over
     * both. */
    const layer = (z) => ({ [REGION_EDGE]: 0, [REGION_INTERFERENCE]: 1 })[z.regionType] ?? 2;
    const ordered = zones.slice().sort((a, b) => layer(a) - layer(b));
    ordered.forEach((z) => {
      if (edit && edit.rangeId === z.rangeId) return;
      const interference = z.regionType === REGION_INTERFERENCE;
      const edge = z.regionType === REGION_EDGE;
      const dim = Boolean(edit) || (focus !== null && focus !== z.rangeId);
      const group = svgEl('g', {});
      (z.blockList || []).forEach((b) => {
        const { row, col } = blockCell(b, cols);
        if (row < 0 || row >= rows) return;
        group.appendChild(svgEl('rect', {
          class: `cell${dim ? ' dim' : ''}`, x: col * CELL, y: row * CELL,
          width: CELL, height: CELL,
          fill: interference ? 'url(#hatch)' : colours.get(z.rangeId),
          'fill-opacity': interference ? 1 : (edge ? 0.35 : 0.55),
        }));
      });
      svg.appendChild(group);
    });

    let lines = '';
    for (let c = 0; c <= cols; c += 1) lines += `M${c * CELL} 0V${h}`;
    for (let rr = 0; rr <= rows; rr += 1) lines += `M0 ${rr * CELL}H${w}`;
    svg.appendChild(svgEl('path', { class: 'grid', d: lines }));
    svg.appendChild(svgEl('rect', { class: 'frame', x: 0, y: 0, width: w, height: h }));
    this._drawSensor(svg, defs, w, h);

    if (edit) {
      this._drawPaintLayer(svg, grid, edit, colours);
      return svg;
    }

    this._overlaps(zones).forEach((b) => {
      const { row, col } = blockCell(b, cols);
      svg.appendChild(svgEl('rect', { class: 'overlap', x: col * CELL + 1,
        y: row * CELL + 1, width: CELL - 2, height: CELL - 2 }));
    });

    /* A detection or entrance zone's name sits at the middle of its blocks. */
    zones.filter((z) => z.regionType === REGION_DETECTION
      || z.regionType === REGION_ENTRANCE).forEach((z) => {
      const cells = (z.blockList || []).map((b) => blockCell(b, cols));
      if (!cells.length) return;
      const cx = cells.reduce((s, c) => s + c.col, 0) / cells.length;
      const cy = cells.reduce((s, c) => s + c.row, 0) / cells.length;
      const label = svgEl('text', { class: 'label',
        x: (cx + 0.5) * CELL, y: (cy + 0.5) * CELL });
      label.textContent = z.name;
      svg.appendChild(label);
    });
    return svg;
  }

  /* The FP2 itself, drawn as the Aqara app draws it: a dot with two rings,
   * cut by the frame. The app sends where it sits, the point x=0, y=0 of the
   * live positions (the middle of the top side for a wall mount). Nothing is
   * drawn when the app does not send it. */
  _drawSensor(svg, defs, w, h) {
    const at = this._za && this._za.sensor;
    const col = Number(at && at.col);
    const row = Number(at && at.row);
    if (!Number.isFinite(col) || !Number.isFinite(row)) return;
    const clip = svgEl('clipPath', { id: 'mapclip' });
    clip.appendChild(svgEl('rect', { x: 0, y: 0, width: w, height: h }));
    defs.appendChild(clip);
    const g = svgEl('g', { class: 'sensor', 'clip-path': 'url(#mapclip)' });
    const cx = col * CELL;
    const cy = row * CELL;
    g.appendChild(svgEl('circle', { class: 'ring', cx, cy, r: CELL }));
    g.appendChild(svgEl('circle', { class: 'ring', cx, cy, r: CELL * 0.6 }));
    g.appendChild(svgEl('circle', { class: 'core', cx, cy, r: CELL * 0.22 }));
    svg.appendChild(g);
  }

  _drawPaintLayer(svg, grid, edit, colours) {
    const cols = grid.columns;
    const colour = edit.regionType === REGION_INTERFERENCE
      ? 'url(#hatch)'
      : (edit.rangeId === null ? NEW_ZONE_COLOUR : colours.get(edit.rangeId));
    const inDraft = new Set(edit.blocks);
    this._hits = new Map();
    for (let b = 1; b <= grid.columns * grid.rows; b += 1) {
      const { row, col } = blockCell(b, cols);
      const hit = svgEl('rect', { class: 'hit', x: col * CELL, y: row * CELL,
        width: CELL, height: CELL, 'data-block': b });
      this._styleHit(hit, inDraft.has(b), colour);
      hit.addEventListener('pointerdown', (e) => {
        e.preventDefault();
        this._paint = edit.blocks.includes(b) ? 'remove' : 'add';
        this._paintBlock(b, colour);
      });
      svg.appendChild(hit);
      this._hits.set(b, hit);
    }
    /* hass-swipe-navigation listens for touches in the bubble phase on the
     * app layout, so a drag that paints would also change the dashboard view.
     * Stopping the touch here, only while editing, keeps it on the map. */
    ['touchstart', 'touchmove', 'touchend'].forEach((type) => {
      svg.addEventListener(type, (e) => e.stopPropagation());
    });
    /* On a touch screen the pointer is captured by the cell it started on,
     * so the cell under the finger is found by position, not by events on
     * the cells themselves. */
    svg.addEventListener('pointermove', (e) => {
      if (!this._paint || !this.shadowRoot.elementFromPoint) return;
      const under = this.shadowRoot.elementFromPoint(e.clientX, e.clientY);
      const b = under && under.getAttribute && Number(under.getAttribute('data-block'));
      if (b) this._paintBlock(b, colour);
    });
    const stop = () => {
      if (this._paint) {
        this._paint = null;
        this._refreshEditor();
      }
    };
    svg.addEventListener('pointerup', stop);
    svg.addEventListener('pointercancel', stop);
    svg.addEventListener('pointerleave', stop);
  }

  _styleHit(hit, on, colour) {
    hit.setAttribute('class', on ? 'hit on' : 'hit');
    hit.style.fill = on ? colour : 'transparent';
    hit.style.fillOpacity = on ? '0.85' : '0';
  }

  _paintBlock(b, colour) {
    const e = this._edit;
    if (!e || !this._paint) return;
    const has = e.blocks.includes(b);
    if (this._paint === 'add' && !has) e.blocks.push(b);
    else if (this._paint === 'remove' && has) e.blocks = e.blocks.filter((x) => x !== b);
    else return;
    const hit = this._hits && this._hits.get(b);
    if (hit) this._styleHit(hit, this._paint === 'add', colour);
    /* The block count and the Save button follow at the end of the stroke,
     * on pointerup, so a long drag does not rewrite them on every cell. */
  }

  /* ---------- editing ---------- */

  _startEdit(zone) {
    const t = this._config.text;
    const zones = (this._za && this._za.zones) || [];
    if (zone) {
      this._edit = { rangeId: zone.rangeId, regionType: zone.regionType,
        name: zone.name, sensitivity: zone.sensitivity, tagId: zone.tagId,
        blocks: (zone.blockList || []).slice(), original: zone };
    } else {
      const n = zones.filter((z) => z.regionType === REGION_DETECTION).length + 1;
      this._edit = { rangeId: null, regionType: REGION_DETECTION, tagId: TAG_OTHER,
        name: tpl(t.new_zone_name, { n }), sensitivity: 1, blocks: [], original: null };
    }
    this._preview = null;
    this._focus = null;
    this._zonesKey = null;
    this._updateZones();
  }

  _cancelEdit() {
    this._edit = null;
    this._paint = null;
    this._zonesKey = null;
    this._updateZones();
  }

  _changed() {
    const e = this._edit;
    if (!e) return false;
    if (!e.original) return true;
    const o = e.original;
    const same = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
    return e.name !== o.name || e.sensitivity !== o.sensitivity
      || e.tagId !== o.tagId || !same(e.blocks, o.blockList || []);
  }

  _drawEditor() {
    const r = this._refs;
    const t = this._config.text;
    const e = this._edit;
    const interference = e.regionType === REGION_INTERFERENCE;
    const edge = e.regionType === REGION_EDGE;

    /* The edge has only its blocks, so it gets no name, type or sensitivity
     * field and no Delete. */
    const nameRow = el('div', 'field');
    nameRow.appendChild(el('label', '', t.name));
    const name = el('input', '');
    name.type = 'text';
    name.maxLength = 32;
    name.autocomplete = 'off';
    name.value = e.name;
    /* Also as the attribute, which is what a serialised render shows. */
    name.setAttribute('value', e.name);
    name.addEventListener('input', () => {
      e.name = name.value;
      this._refreshEditor();
    });
    nameRow.appendChild(name);
    if (!edge) r.editor.appendChild(nameRow);

    if (e.regionType === REGION_DETECTION) r.editor.appendChild(this._typeField());

    if (!interference && !edge) {
      const sensRow = el('div', 'field');
      sensRow.appendChild(el('label', '', t.sensitivity));
      const seg = el('div', 'seg');
      this._editSeg = {};
      Object.keys(SENSITIVITY).forEach((v) => {
        const b = el('button', Number(v) === e.sensitivity ? 'on' : '', SENSITIVITY[v]);
        b.addEventListener('click', () => {
          e.sensitivity = Number(v);
          Object.keys(this._editSeg).forEach((k) => {
            this._editSeg[k].className = Number(k) === e.sensitivity ? 'on' : '';
          });
          this._refreshEditor();
        });
        this._editSeg[v] = b;
        seg.appendChild(b);
      });
      sensRow.appendChild(seg);
      r.editor.appendChild(sensRow);
    }

    this._editInfo = el('div', 'note', '');
    r.editor.appendChild(this._editInfo);

    const btns = el('div', 'btns');
    this._saveBtn = el('button', 'primary', t.save);
    this._saveBtn.addEventListener('click', () => this._saveEdit());
    btns.appendChild(this._saveBtn);
    const cancel = el('button', '', t.cancel);
    cancel.addEventListener('click', () => this._cancelEdit());
    btns.appendChild(cancel);
    btns.appendChild(el('span', 'spacer'));
    if (e.rangeId !== null && !interference && !edge) {
      btns.appendChild(armedButton(t.delete, t.confirm, () => {
        this._command('zone_delete', String(e.rangeId));
        this._sent();
      }));
    }
    r.editor.appendChild(btns);
    this._refreshEditor();
  }

  /* The zone's type, from the catalogue the app reads from the Aqara cloud:
   * Other first, then the rest by name. Picking one also sets the
   * sensitivity the Aqara app suggests for it, which can still be changed
   * before Save (owner, 2026-10-31). A type outside the catalogue stays
   * listed, so opening such a zone does not change it. */
  _typeField() {
    const t = this._config.text;
    const e = this._edit;
    const tags = ((this._za && this._za.tags) || [])
      .filter((tag) => tag && tag.tagId && tag.tagId !== TAG_OTHER)
      .slice().sort((a, b) => a.tagName.localeCompare(b.tagName));
    const options = [{ tagId: TAG_OTHER, tagName: t.type_other }].concat(tags);
    if (e.tagId && !options.some((o) => o.tagId === e.tagId)) {
      const own = e.original && e.original.tagName;
      options.splice(1, 0, { tagId: e.tagId, tagName: own || e.tagId });
    }
    const row = el('div', 'field');
    row.appendChild(el('label', '', t.type));
    const pick = el('select', '');
    options.forEach((o) => {
      const opt = el('option', '', o.tagName);
      opt.value = o.tagId;
      if (o.tagId === e.tagId) {
        opt.selected = true;
        opt.setAttribute('selected', '');
      }
      pick.appendChild(opt);
    });
    pick.addEventListener('change', () => {
      e.tagId = pick.value;
      const tag = tags.find((o) => o.tagId === e.tagId);
      if (tag && SENSITIVITY[tag.sensitivity]) {
        e.sensitivity = tag.sensitivity;
        Object.keys(this._editSeg || {}).forEach((k) => {
          this._editSeg[k].className = Number(k) === e.sensitivity ? 'on' : '';
        });
      }
      this._refreshEditor();
    });
    row.appendChild(pick);
    return row;
  }

  _refreshEditor() {
    const e = this._edit;
    if (!e || !this._editInfo) return;
    const t = this._config.text;
    this._editInfo.textContent = e.regionType === REGION_EDGE ? t.edge_hint : t.edit_hint;
    this._saveBtn.disabled = !e.blocks.length || !e.name.trim() || !this._changed();
  }

  _saveEdit() {
    const e = this._edit;
    if (!e || this._saveBtn.disabled) return;
    const payload = { name: e.name.trim(), blockList: e.blocks.slice() };
    /* The app takes only the blocks of the edge and refuses anything else. */
    if (e.regionType === REGION_EDGE) delete payload.name;
    else if (e.regionType !== REGION_INTERFERENCE) payload.sensitivity = e.sensitivity;
    if (e.regionType === REGION_DETECTION && e.tagId) payload.tagId = e.tagId;
    if (e.rangeId !== null) payload.rangeId = e.rangeId;
    this._command('zone_set', payload);
    this._sent();
  }

  _sent() {
    this._sentText = null;
    this._sentAt = Date.now();
    this._edit = null;
    this._paint = null;
    this._zonesKey = null;
    this._update();
  }

  /* ---------- backups ---------- */

  _previewZones() {
    if (this._preview === null) return null;
    const st = this._state('preview');
    const a = st && st.attributes;
    return a && a.name === this._preview ? a : null;
  }

  _openPreview(name) {
    /* A backup is looked at on the map, which is on the Zones pane. */
    this._pane = 'zones';
    this._showPane();
    this._preview = name;
    this._edit = null;
    this._command('preview', name);
    this._zonesKey = null;
    this._backupsKey = null;
    this._update();
  }

  _closePreview() {
    this._preview = null;
    this._zonesKey = null;
    this._backupsKey = null;
    this._update();
  }

  _drawPreview(preview, grid) {
    const r = this._refs;
    const t = this._config.text;
    if (!preview) {
      r.banner.appendChild(el('div', 'what', t.loading_preview));
      const close = el('button', '', t.close);
      close.addEventListener('click', () => this._closePreview());
      r.banner.appendChild(close);
      r.note.textContent = '';
      return;
    }
    const why = t.reason[preview.reason] || preview.reason || '';
    r.banner.appendChild(el('div', 'what',
      `${tpl(t.preview_of, { when: shortTime(preview.saved_at) })} · ${why}`));
    const name = preview.name;
    r.banner.appendChild(armedButton(t.restore, t.confirm, () => {
      this._command('restore', name);
      this._preview = null;
      this._backupsKey = null;
      this._sent();
    }));
    const close = el('button', '', t.close);
    close.addEventListener('click', () => this._closePreview());
    r.banner.appendChild(close);
    const zones = preview.zones || [];
    r.mapHolder.appendChild(this._drawMap(zones, grid, this._zoneColours(zones), null));
    r.legend.appendChild(legendHeader(t));
    zones.forEach((z) => {
      const row = el('div', 'row');
      const sw = el('div', 'sw');
      sw.style.background = this._zoneColours(zones).get(z.rangeId);
      row.appendChild(sw);
      row.appendChild(el('div', 'nm', z.name));
      row.appendChild(el('div', 'meta type', zoneType(z, t)));
      row.appendChild(el('div', 'meta sens', zoneSensitivity(z)));
      row.appendChild(el('div', ''));
      r.legend.appendChild(row);
    });
    r.note.textContent = '';
  }

  _updateBackups(rows) {
    const r = this._refs;
    const t = this._config.text;
    const key = JSON.stringify([rows, this._preview]);
    if (key === this._backupsKey) return;
    this._backupsKey = key;
    r.backupCount.textContent = rows.length ? String(rows.length) : '';
    r.backups.innerHTML = '';
    if (!rows.length) {
      r.backups.appendChild(el('div', 'empty', t.no_backups));
      return;
    }
    /* One select. Choosing a backup opens its preview on the map, where
     * Restore and Close are; closing the preview puts the select back on
     * its prompt. */
    const pick = el('select', '');
    const prompt = el('option', '', t.backup_choose);
    prompt.value = '';
    pick.appendChild(prompt);
    rows.forEach((b) => {
      const zones = (b.zones || []).join(', ');
      const why = t.reason[b.reason] || b.reason || '';
      const opt = el('option', '', `${shortTime(b.saved_at)} · ${why}${zones ? ` · ${zones}` : ''}`);
      opt.value = b.name;
      if (this._preview === b.name) opt.selected = true;
      pick.appendChild(opt);
    });
    pick.addEventListener('change', () => {
      if (pick.value) this._openPreview(pick.value);
      else this._closePreview();
    });
    r.backups.appendChild(pick);
  }
}

customElements.define('fp2-companion-card', Fp2CompanionCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'fp2-companion-card',
  name: 'FP2 Companion Card',
  description: 'An Aqara FP2\'s settings and zone editor, from the fp2_companion app.',
});

console.info(`%c FP2-COMPANION-CARD %c ${CARD_VERSION} `,
  'color: white; background: #2196f3; font-weight: 700;',
  'color: #2196f3; background: white; font-weight: 700;');
