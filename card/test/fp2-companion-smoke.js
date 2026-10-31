/*
 * Smoke test for fp2-companion-card, in jsdom, with no Home Assistant and no browser.
 *
 *   NODE_PATH="$(npm root -g)" node card/test/fp2-companion-smoke.js
 *
 * What it can prove: that every status renders without throwing, that the map
 * draws one cell per block in the right place, that overlaps and the
 * interference zone are marked, and that the controls send what they say.
 *
 * What it CANNOT prove: jsdom does no layout and no cascade, so it cannot see
 * a label smeared over a zone or a colour lost against the background. Look
 * at fp2-companion-render.js in a headless browser before shipping a layout change.
 */
const fs = require('fs');
const path = require('path');

const { JSDOM } = require('jsdom');

const CARD = process.env.CARD_PATH || path.join(__dirname, '..', 'fp2-companion-card.js');
const dom = new JSDOM('<!doctype html><html><body></body></html>');
Object.assign(global, {
  window: dom.window, document: dom.window.document,
  HTMLElement: dom.window.HTMLElement, customElements: dom.window.customElements,
  CustomEvent: dom.window.CustomEvent,
});
const SOURCE = fs.readFileSync(CARD, 'utf8');
/* The crypto helpers are top-level functions of the card, so evaluating the
 * same source once more with a return statement hands them to the test. */
const { md5Hex, rsaPkcs1v15, encryptPassword, defaultCountry } = new Function(
  `${SOURCE.replace("customElements.define('fp2-companion-card', Fp2CompanionCard);", '')}`
  + '\nreturn { md5Hex, rsaPkcs1v15, encryptPassword, defaultCountry };')();
new Function(SOURCE)();
const Card = customElements.get('fp2-companion-card');

const P = 'presence_sensor_fp2_b2c3';
const E = {
  status: `sensor.${P}_cloud_status`, zones: `sensor.${P}_zones`,
  sensitivity: `select.${P}_sensitivity`, approach: `select.${P}_approach_distance`,
  refresh: `button.${P}_refresh`, preview: `sensor.${P}_backup_preview`,
  ai: `switch.${P}_ai_detection`, learning: `button.${P}_ai_learning`,
  hub: 'sensor.fp2_companion_devices',
};
const KEY = '54ef44a1b2c3';
const LIVING = { key: KEY, did: 'lumi1.54ef44a1b2c3', name: 'Presence-Sensor-FP2-B2C3',
  prefix: P, area: 'Living Room' };
const OFFICE = { key: '54ef44d4e5f6', did: 'lumi1.54ef44d4e5f6',
  name: 'Motion Sensor - Office', prefix: 'presence_sensor_fp2_e5f6', area: 'Office' };
function hub(devices = [LIVING]) {
  return { state: String(devices.length), attributes: { devices,
    countries: [['GR', 'Greece'], ['US', 'United States']], country: 'GR',
    signed_in: true } };
}

let failures = 0;
let checks = 0;
function ok(condition, what) {
  checks += 1;
  if (!condition) {
    failures += 1;
    console.error(`FAIL: ${what}`);
  }
}
function eq(actual, expected, what) {
  ok(actual === expected, `${what} (got ${JSON.stringify(actual)}, `
    + `expected ${JSON.stringify(expected)})`);
}

/* The live zones of 2026-10-30, trimmed: Couch is a 3 by 5 rectangle, Kitchen
 * shares block 140 with the interference zone. */
const ZONES = [
  { rangeId: 3, name: 'Couch', regionType: 0, sensitivity: 2,
    blockList: [43, 75, 107, 44, 76, 108, 45, 77, 109, 59, 91, 60, 92, 61, 93] },
  { rangeId: 1, name: 'Kitchen Countertop', regionType: 0, sensitivity: 3,
    blockList: [139, 140, 141] },
  { rangeId: 101, name: 'Interference source', regionType: 1, sensitivity: 1,
    blockList: [140, 273, 274, 320] },
];
const TOTAL_BLOCKS = ZONES.reduce((n, z) => n + z.blockList.length, 0);

function states(over = {}) {
  const s = {
    [E.status]: { state: 'ok', attributes: {
      message: 'Settings and zones read from the cloud.',
      last_read: '2026-10-30T09:05:13', token_expires: '2026-11-29T08:47:10',
      account: 'j.w*****@example.com', signed_in: true } },
    [E.zones]: { state: '3', attributes: {
      grid: { columns: 16, rows: 20 }, zones: ZONES,
      backups: [
        { name: 'zones-20261030T090122.json', saved_at: 1793343682,
          reason: 'first-read', zones: ['Couch', 'Kitchen Countertop'] },
      ] } },
    [E.sensitivity]: { state: 'Low', attributes: { options: ['Low', 'Medium', 'High'] } },
    [E.approach]: { state: 'Far', attributes: { options: ['Far', 'Middle', 'Close'] } },
    [E.refresh]: { state: 'unknown', attributes: {} },
    [E.ai]: { state: 'off', attributes: {} },
    [E.learning]: { state: 'unknown', attributes: {} },
    [E.hub]: hub(),
  };
  Object.keys(over).forEach((k) => { s[k] = over[k]; });
  return s;
}

/* A live lease goes out the moment a card is attached and every 30 s after,
 * so it is kept apart from `calls`, which the press checks count. */
function isLease(data) {
  return Boolean(data && /\/cmd\/track$/.test(String(data.topic)));
}

function mount(st, connection) {
  const card = new Card();
  card.setConfig({ type: 'custom:fp2-companion-card' });
  const calls = [];
  const leases = [];
  card.hass = { states: st || states(), connection,
    callService: (d, s, data) => (isLease(data) ? leases : calls).push({ d, s, data }) };
  document.body.appendChild(card);
  return { card, calls, leases, root: card.shadowRoot };
}

function segButtons(root, index) {
  return [...root.querySelectorAll('.seg')[index].querySelectorAll('button')];
}

/* ---------- no status ---------- */
{
  const card = new Card();
  card.setConfig({ type: 'custom:fp2-companion-card' });
  card.hass = { states: {} };
  const err = card.shadowRoot.querySelector('.err');
  ok(err && err.textContent.includes(E.hub), 'a missing devices sensor is named');
}

/* ---------- a healthy card ---------- */
{
  const { root, calls, card } = mount();
  eq(root.querySelector('.cloud').title, 'Cloud OK', 'the cloud names the status');
  ok(root.querySelector('.cloud').classList.contains('good'), 'ok is a green cloud');
  eq(root.querySelector('.cloud .mark').getAttribute('d'), 'M8.4 13.4l2.5 2.5 4.8-4.8',
    'with a check mark');
  ok(!root.querySelector('.pill'), 'no text pill any more');
  /* Three panes, the map first. */
  const tabs = [...root.querySelectorAll('.panetabs button')].map((b) => b.textContent);
  eq(JSON.stringify(tabs), '["Zones","Settings","Backups","Account"]', 'four tabs');
  const panes = [...root.querySelectorAll('.pane')];
  eq(panes.map((p) => p.hidden).join(), 'false,true,true,true', 'the Zones pane shows first');
  ok(panes[1].querySelector('.toggle') && panes[1].querySelectorAll('.seg').length === 2,
    'the AI toggle and both settings are on the Settings pane');
  ok(panes[0].querySelector('.map') && !panes[0].querySelector('.seg'),
    'the map is on the Zones pane, without the settings');
  ok(panes[2].querySelector('.backups'), 'the backups have their own pane');
  root.querySelectorAll('.panetabs button')[1].click();
  eq(panes.map((p) => p.hidden).join(), 'true,false,true,true', 'Settings shows its pane');
  ok(panes[3].querySelector('.account .who') && !root.querySelector('.foot .account'),
    'the account is on its own pane, not under every pane');
  ok(!root.querySelector('.foot .info').textContent.includes('Session'),
    'and the footer keeps only the last read');
  ok(root.querySelectorAll('.panetabs button')[1].classList.contains('on'), 'and its tab is on');
  root.querySelectorAll('.panetabs button')[0].click();
  ok(root.querySelector('.message').hidden, 'no message while ok');

  const sens = segButtons(root, 0);
  eq(sens.map((b) => b.textContent).join(','), 'Low,Medium,High',
    'sensitivity options come from the select');
  eq(sens.filter((b) => b.classList.contains('on')).map((b) => b.textContent).join(),
    'Low', 'current sensitivity is lit');
  eq(segButtons(root, 1).find((b) => b.classList.contains('on')).textContent, 'Far',
    'current approach distance is lit');

  /* A press goes through the select entity, and shows at once as pending. */
  sens[1].click();
  eq(calls.length, 1, 'one service call per press');
  eq(`${calls[0].d}.${calls[0].s}`, 'select.select_option', 'press uses select_option');
  eq(calls[0].data.entity_id, E.sensitivity, 'press names the select');
  eq(calls[0].data.option, 'Medium', 'press sends the option');
  const lit = segButtons(root, 0).find((b) => b.classList.contains('on'));
  ok(lit.textContent === 'Medium' && lit.classList.contains('pending'),
    'the pressed option shows as pending');

  /* The readback arrives: pending clears. */
  const st = states({ [E.sensitivity]: { state: 'Medium',
    attributes: { options: ['Low', 'Medium', 'High'] } } });
  card.hass = { states: st, callService: (d, s, data) => (isLease(data) ? [] : calls).push({ d, s, data }) };
  const after = segButtons(root, 0).find((b) => b.classList.contains('on'));
  ok(after.textContent === 'Medium' && !after.classList.contains('pending'),
    'pending clears when the entity reports the value');

  root.querySelector('.foot button').click();
  const press = calls[calls.length - 1];
  eq(`${press.d}.${press.s}`, 'button.press', 'refresh presses the button entity');
  eq(press.data.entity_id, E.refresh, 'refresh names the button');

  ok(root.querySelector('.account .sess').textContent.includes('29/11'),
    'the session expiry is shown');
}

/* ---------- the map ---------- */
{
  const { root } = mount();
  const svg = root.querySelector('svg.map');
  ok(svg, 'the map is drawn');
  eq(svg.getAttribute('viewBox'), '-1 -1 162 202', 'a 16 by 20 grid of 10 unit cells');
  const cells = [...svg.querySelectorAll('rect.cell')];
  eq(cells.length, TOTAL_BLOCKS, 'one cell per block, overlaps included');

  /* Block 43 is row 2, column 10. */
  ok(cells.some((c) => c.getAttribute('x') === '100' && c.getAttribute('y') === '20'),
    'block 43 lands at row 2, column 10');
  /* Block 320 is the bottom right corner. */
  ok(cells.some((c) => c.getAttribute('x') === '150' && c.getAttribute('y') === '190'),
    'block 320 lands at the bottom right');

  eq(svg.querySelectorAll('rect.overlap').length, 1, 'block 140 is marked as an overlap');
  ok(cells.filter((c) => c.getAttribute('fill') === 'url(#hatch)').length === 4,
    'the interference zone is hatched');
  eq([...svg.querySelectorAll('text.label')].map((t) => t.textContent).join(','),
    'Couch,Kitchen Countertop', 'only detection zones are labelled');
  ok(root.querySelector('.note').textContent.includes('1'), 'the overlap count is noted');

  const legend = [...root.querySelectorAll('.legend .row:not(.head)')];
  eq(legend.length, 3, 'one legend row per zone');
  ok(legend[2].textContent.includes('Interference'), 'the interference zone says so');
  ok(legend[0].textContent.includes('Medium'), 'a zone shows its sensitivity');
  ok(!root.querySelector('.legend').textContent.includes('blocks'), 'no block count anywhere');
  eq(legend[2].querySelector('.type').textContent, 'Interference', 'the interference zone says so');
  eq(legend[2].querySelector('.sens').textContent, '', 'and leaves its sensitivity empty');
  eq(legend[0].children.length, legend[2].children.length,
    'every row has the same cells, so the columns line up');
  eq(root.querySelector('.legend .row.head').textContent, 'TypeSensitivity', 'the columns have titles');

}

/* ---------- picking a zone out, without editing ---------- */
{
  const { root } = mount();
  root.querySelectorAll('.legend .row:not(.head)')[0].click();
  ok(root.querySelector('.editor').hidden, 'a press on the row does not edit');
  eq(root.querySelectorAll('svg.map rect.cell.dim').length, TOTAL_BLOCKS - 15,
    'a press on the row dims every other zone');
  root.querySelectorAll('.legend .row:not(.head)')[0].click();
  eq(root.querySelectorAll('svg.map rect.cell.dim').length, 0,
    'a second press clears it');
  eq(root.querySelectorAll('.legend .badge').length, 3, 'every zone has an Edit badge');
}

/* ---------- editing a zone ---------- */
function ev(node, type) {
  node.dispatchEvent(new dom.window.Event(type, { bubbles: true, cancelable: true }));
}
function lastPublish(calls) {
  const c = calls.filter((x) => x.d === 'mqtt' && x.s === 'publish').pop();
  return c ? c.data : null;
}

{
  const { root, calls } = mount();
  root.querySelectorAll('.legend .row .badge')[0].click();   // Couch
  const editor = root.querySelector('.editor');
  ok(!editor.hidden, 'pressing a zone opens the editor');
  const svg = root.querySelector('svg.map');
  ok(svg.classList.contains('editing'), 'the map is in painting mode');
  eq(root.querySelectorAll('.legend .badge').length, 0,
    'the other Edit badges hide while a zone is being edited');
  eq(svg.querySelectorAll('rect.hit').length, 320, 'one paint cell per block');
  eq(svg.querySelectorAll('rect.hit.on').length, 15, 'the zone being edited is the draft');
  eq(svg.querySelectorAll('rect.cell:not(.dim)').length, 0, 'every other zone is dimmed');
  const save = [...editor.querySelectorAll('button')].find((b) => b.textContent === 'Save');
  ok(save.disabled, 'Save waits for a change');

  /* Block 46 is row 2, column 13, next to the couch. */
  const hit46 = svg.querySelector('rect.hit[data-block="46"]');
  ev(hit46, 'pointerdown');
  ev(svg, 'pointerup');
  ok(hit46.classList.contains('on'), 'a tap adds the block');
  ok(!save.disabled, 'Save is enabled after a change');

  /* A second tap on a draft block removes it. */
  const hit43 = svg.querySelector('rect.hit[data-block="43"]');
  ev(hit43, 'pointerdown');
  ev(svg, 'pointerup');
  ok(!hit43.classList.contains('on'), 'a tap on a draft block removes it');

  save.click();
  const pub = lastPublish(calls);
  eq(pub.topic, 'fp2companion/54ef44a1b2c3/cmd/zone_set', 'Save goes to zone_set');
  const sent = JSON.parse(pub.payload);
  eq(sent.rangeId, 3, 'an edit names its rangeId');
  eq(sent.blockList.join(','), '75,107,44,76,108,45,77,109,59,91,60,92,61,93,46',
    'the read order is kept and the new block appended');
  eq(sent.sensitivity, 2, 'the sensitivity travels with the zone');
  ok(root.querySelector('.editor').hidden, 'the editor closes after Save');
  ok(root.querySelector('.message').textContent.includes('Sent'),
    'the card says the change is on its way');
}

{
  const { root, calls } = mount();
  root.querySelectorAll('.legend .row .badge')[0].click();
  const del = [...root.querySelectorAll('.editor button')].find((b) => b.textContent === 'Delete');
  const before = calls.length;
  del.click();
  eq(calls.length, before, 'one press on Delete sends nothing');
  eq(del.textContent, 'Press again', 'Delete asks for a second press');
  del.click();
  const pub = lastPublish(calls);
  eq(pub.topic, 'fp2companion/54ef44a1b2c3/cmd/zone_delete', 'the second press deletes');
  eq(pub.payload, '3', 'the delete names the rangeId');
}

{
  const { root, calls } = mount();
  root.querySelectorAll('.legend .row .badge')[2].click();   // interference
  const buttons = [...root.querySelectorAll('.editor button')].map((b) => b.textContent);
  ok(!buttons.includes('Delete'), 'the interference zone has no Delete');
  ok(!buttons.includes('Low'), 'the interference zone has no sensitivity');
  const svg = root.querySelector('svg.map');
  ev(svg.querySelector('rect.hit[data-block="1"]'), 'pointerdown');
  ev(svg, 'pointerup');
  [...root.querySelectorAll('.editor button')].find((b) => b.textContent === 'Save').click();
  const sent = JSON.parse(lastPublish(calls).payload);
  eq(sent.rangeId, 101, 'the interference zone is rewritten in place');
  ok(!('sensitivity' in sent), 'no sensitivity is sent for it');
}

{
  const { root, calls } = mount();
  root.querySelector('.sect .cap button').click();            // New zone
  const name = root.querySelector('.editor input');
  eq(name.value, 'Detection Zone 3', 'a new zone gets the next default name');
  const save = [...root.querySelectorAll('.editor button')].find((b) => b.textContent === 'Save');
  ok(save.disabled, 'an empty new zone cannot be saved');
  const svg = root.querySelector('svg.map');
  ev(svg.querySelector('rect.hit[data-block="15"]'), 'pointerdown');
  ev(svg, 'pointerup');
  ok(!save.disabled, 'a new zone with a block can be saved');
  save.click();
  const sent = JSON.parse(lastPublish(calls).payload);
  ok(!('rangeId' in sent), 'a new zone is sent without a rangeId');
  eq(sent.blockList.join(','), '15', 'with its blocks');
}

{
  const { root, calls } = mount();
  root.querySelectorAll('.legend .row .badge')[0].click();
  [...root.querySelectorAll('.editor button')].find((b) => b.textContent === 'Cancel').click();
  ok(root.querySelector('.editor').hidden, 'Cancel closes the editor');
  ok(!lastPublish(calls), 'Cancel sends nothing');
}

/* ---------- previewing and restoring a backup ---------- */
{
  const st = states();
  const { root, calls, card } = mount(st);
  const pick = root.querySelector('.backups select');
  eq(pick.options.length, 2, 'the backups are one dropdown with a prompt');
  ok(pick.options[1].textContent.includes('First read'), 'an option says what the backup is');
  pick.value = 'zones-20261030T090122.json';
  pick.dispatchEvent(new dom.window.Event('change'));
  const pub = lastPublish(calls);
  eq(pub.topic, 'fp2companion/54ef44a1b2c3/cmd/preview', 'pressing a backup asks for its preview');
  eq(pub.payload, 'zones-20261030T090122.json', 'by its name');
  ok(root.querySelector('.banner').textContent.includes('Loading'),
    'the card waits for the preview');

  const withPreview = states({ [E.preview]: { state: 'zones-20261030T090122.json',
    attributes: { name: 'zones-20261030T090122.json', saved_at: 1793343682,
      reason: 'first-read', zones: ZONES.slice(0, 1) } } });
  card.hass = { states: withPreview, callService: (d, s, data) => (isLease(data) ? [] : calls).push({ d, s, data }) };
  eq(root.querySelectorAll('svg.map rect.cell').length, 15, 'the map shows the backup');
  const restore = [...root.querySelectorAll('.banner button')].find((b) => b.textContent === 'Restore');
  const before = calls.length;
  restore.click();
  eq(calls.length, before, 'one press on Restore sends nothing');
  restore.click();
  const r = lastPublish(calls);
  eq(r.topic, 'fp2companion/54ef44a1b2c3/cmd/restore', 'the second press restores');
  eq(r.payload, 'zones-20261030T090122.json', 'the backup that was shown');
  ok(root.querySelector('.banner').hidden, 'the preview closes after a restore');
}

/* ---------- statuses ---------- */
{
  const st = states({ [E.status]: { state: 'write_not_applied', attributes: {
    message: 'The cloud accepted the write but the device still reports the old value.',
  } } });
  const { root } = mount(st);
  ok(root.querySelector('.cloud').classList.contains('warn'), 'an ignored write warns');
  ok(!root.querySelector('.message').hidden, 'the app message is shown');

  const lost = states({ [E.status]: { state: 'no_session', attributes: {} } });
  const m2 = mount(lost);
  ok(m2.root.querySelector('.cloud').classList.contains('bad'), 'a lost session is a red cloud');
  eq(m2.root.querySelector('.cloud .mark').getAttribute('d'), 'M9.5 10.5l5 5M14.5 10.5l-5 5',
    'with a cross');
  eq(m2.root.querySelector('.cloud').title, 'Not signed in', 'lost session text');
}

/* ---------- no zones, no backups ---------- */
{
  const st = states({ [E.zones]: { state: '0', attributes: { zones: [], backups: [] } } });
  const { root } = mount(st);
  ok(!root.querySelector('svg.map'), 'no map without zones');
  ok(root.querySelector('.empty'), 'an empty zone list says so');
}

/* ---------- an unrelated change does not redraw ---------- */
{
  const { card, root } = mount();
  const before = root.querySelector('svg.map');
  const st = states();
  st['light.kitchen'] = { state: 'on', attributes: {} };
  card.hass = { states: st, callService: () => {} };
  ok(root.querySelector('svg.map') === before, 'the map is not rebuilt for nothing');
}

/* ---------- signing in ---------- */
{
  const { root } = mount();
  ok(root.querySelector('form.signin').hidden, 'signed in, the form is hidden');
  ok(!root.querySelector('.account').hidden, 'signed in, the account row shows');
  ok(root.querySelector('.account .who').textContent.includes('j.w*****@example.com'),
    'the account is shown as the app masked it');
}

{
  const st = states({ [E.status]: { state: 'no_session', attributes: {
    message: 'Not signed in to Aqara. Sign in on the card.' } } });
  const { root, calls } = mount(st);
  const form = root.querySelector('form.signin');
  ok(!form.hidden, 'without a session the form shows');
  ok([...root.querySelectorAll('.body > div')].some((d) => d.hidden && d.querySelector('.setting')),
    'without a session the settings are hidden');
  ok([...form.querySelectorAll('button')].find((b) => b.textContent === 'Cancel').hidden,
    'a first sign-in has no Cancel');
  const [email, password] = form.querySelectorAll('input');
  eq(password.type, 'password', 'the password field masks');
  email.value = 'new.user@example.com';
  password.value = 'correct horse';
  form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
  const pub = lastPublish(calls);
  eq(pub.topic, 'fp2companion/cmd/login', 'the form sends login');
  const body = JSON.parse(pub.payload);
  eq(body.account, 'new.user@example.com', 'with the account');
  eq(Buffer.from(body.password_rsa, 'base64').length, 128, 'and a 1024-bit RSA block');
  ok(!pub.payload.includes('correct horse'), 'never the clear password');
  eq(password.value, '', 'the password field is cleared at once');
  ok(root.querySelector('.message').textContent.includes('Signing in'),
    'the card says it is signing in');
}

{
  const { root, calls } = mount();
  const change = [...root.querySelectorAll('.account button')].find((b) => b.textContent === 'Change login');
  change.click();
  const form = root.querySelector('form.signin');
  ok(!form.hidden, 'Change login opens the form');
  ok(form.closest('.pane') === root.querySelectorAll('.pane')[3], 'on the Account pane');
  [...form.querySelectorAll('button')].find((b) => b.textContent === 'Cancel').click();
  ok(form.hidden, 'Cancel closes it again');
  const out = [...root.querySelectorAll('.account button')].find((b) => b.textContent === 'Log out');
  const before = calls.length;
  out.click();
  eq(calls.length, before, 'one press on Log out sends nothing');
  out.click();
  eq(lastPublish(calls).topic, 'fp2companion/cmd/logout', 'the second press logs out');
}

{
  /* A session from before the card could sign in: a token, no account. */
  const st = states({ [E.status]: { state: 'ok', attributes: {
    signed_in: true, account: null } } });
  const { root } = mount(st);
  ok(root.querySelector('form.signin').hidden, 'a token without an account is signed in');
  eq(root.querySelector('.account .who').textContent, 'Signed in', 'and says so plainly');
}

/* ---------- AI person detection and AI learning ---------- */
{
  const { root, calls } = mount();
  const toggle = root.querySelector('button.toggle');
  ok(toggle && !toggle.classList.contains('on'), 'AI off shows the switch off');
  ok(!root.querySelectorAll('.seg')[0].classList.contains('off'),
    'with AI off the sensitivity is enabled');
  toggle.click();
  const c = calls[calls.length - 1];
  eq(`${c.d}.${c.s}`, 'switch.turn_on', 'the switch is turned on through HA');
  eq(c.data.entity_id, E.ai, 'the AI switch entity');
  const start = [...root.querySelectorAll('.setting button.act')][0];
  eq(start.textContent, 'Start', 'the learning button offers Start');
  start.click();
  const p = calls[calls.length - 1];
  eq(`${p.d}.${p.s}`, 'button.press', 'learning presses the button entity');
  eq(p.data.entity_id, E.learning, 'the learning button entity');
}

{
  const soon = new Date(Date.now() + 42000);
  const pad = (n) => String(n).padStart(2, '0');
  const stamp = `${soon.getFullYear()}-${pad(soon.getMonth() + 1)}-${pad(soon.getDate())}T`
    + `${pad(soon.getHours())}:${pad(soon.getMinutes())}:${pad(soon.getSeconds())}`;
  const st = states({ [E.ai]: { state: 'on', attributes: {} },
    [E.status]: { state: 'ok', attributes: { signed_in: true, learning_until: stamp } } });
  const { root } = mount(st);
  ok(root.querySelector('button.toggle').classList.contains('on'), 'AI on shows the switch on');
  ok(root.querySelectorAll('.seg')[0].classList.contains('off'),
    'with AI on the sensitivity is disabled');
  ok(root.querySelectorAll('.seg')[0].closest('.setting').textContent
    .includes('Set by AI person detection'), 'and the sensitivity row says why');
  const groups = [...root.querySelectorAll('.pane')[1].querySelectorAll('.group')].map((g) => [
    g.querySelector('.ghead').textContent, g.querySelectorAll('.setting').length]);
  eq(JSON.stringify(groups), '[["AI",2],["Detection",2]]',
    'the settings come in two groups, AI first');
  const start = [...root.querySelectorAll('.setting button.act')][0];
  ok(start.disabled && /Learning \d+ s/.test(start.textContent),
    'a running learning counts down and cannot be started again');
}

/* ---------- several devices ---------- */
{
  const st = states({ [E.hub]: hub([LIVING, OFFICE]),
    'sensor.presence_sensor_fp2_e5f6_cloud_status': { state: 'ok', attributes: { signed_in: true } },
    'select.presence_sensor_fp2_e5f6_sensitivity': { state: 'Medium',
      attributes: { options: ['Low', 'Medium', 'High'] } } });
  const { root, card, calls } = mount(st);
  const pick = root.querySelector('select.devsel');
  ok(pick, 'two devices show the picker');
  eq([...pick.options].map((o) => o.textContent).join(' | '),
    'Presence-Sensor-FP2-B2C3 · Living Room | Motion Sensor - Office · Office',
    'each option is the name and the area');
  pick.value = OFFICE.key;
  pick.dispatchEvent(new dom.window.Event('change'));
  const root2 = card.shadowRoot;
  eq(root2.querySelectorAll('.seg')[0].querySelector('.on').textContent, 'Medium',
    'switching shows the other device');
  root2.querySelector('.foot button').click();
  const press = calls[calls.length - 1];
  eq(press.data.entity_id, 'button.presence_sensor_fp2_e5f6_refresh',
    'and its entities follow its prefix');
}

{
  const st = states({ [E.hub]: hub([LIVING, OFFICE]) });
  const card = new Card();
  document.body.appendChild(card);
  card.setConfig({ type: 'custom:fp2-companion-card', device: 'living room'.replace(' room', '') });
  card.hass = { states: st, callService: () => {} };
  ok(card.shadowRoot.querySelector('.err'), 'a pin that matches no device says so');
  const pinned = new Card();
  document.body.appendChild(pinned);
  pinned.setConfig({ type: 'custom:fp2-companion-card', device: P });
  pinned.hass = { states: st, callService: () => {} };
  ok(!pinned.shadowRoot.querySelector('select.devsel'), 'a pinned card has no picker');
  ok(!pinned.shadowRoot.querySelector('.err'), 'and shows its device');
}

{
  const st = states({ [E.status]: { state: 'no_session', attributes: {} } });
  const { root, calls } = mount(st);
  const form = root.querySelector('form.signin');
  const country = form.querySelector('select');
  eq(country.value, 'GR', 'the country starts at the session country');
  country.value = 'US';
  const [email, password] = form.querySelectorAll('input');
  email.value = 'a@example.com';
  password.value = 'pw';
  form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
  const pub = lastPublish(calls);
  eq(pub.topic, 'fp2companion/cmd/login', 'a sign-in goes to the app topic');
  eq(JSON.parse(pub.payload).country, 'US', 'with the chosen country');
}

/* ---------- the country to offer first ---------- */
{
  const codes = ['DE', 'GR', 'US'];
  eq(defaultCountry('US', codes, ['el-GR']), 'US', 'the session country comes first');
  eq(defaultCountry(null, codes, ['el-GR', 'en-US']), 'GR', 'then the browser language');
  eq(defaultCountry(null, codes, ['en', 'de-de']), 'DE', 'the first language that names a country');
  eq(defaultCountry(null, codes, ['en', 'fr-FR']), '', 'and no guess when none is on the list');

  /* No session country and a browser that names none: the person chooses. */
  const st = states({ [E.status]: { state: 'no_session', attributes: {} },
    [E.hub]: { ...hub(), attributes: { ...hub().attributes, country: null } } });
  const { root, calls } = mount(st);
  const form = root.querySelector('form.signin');
  const country = form.querySelector('select');
  const submit = form.querySelector('button[type=submit]');
  ok(!form.closest('.pane') && !form.hidden, 'signed out, the form stands in place of the tabs');
  eq(country.value, '', 'jsdom names no country, so none is chosen');
  eq(country.options[0].textContent, 'Choose your country', 'the list asks for one');
  ok(submit.disabled, 'and Sign in waits for it');
  const [email, password] = form.querySelectorAll('input');
  email.value = 'a@example.com';
  password.value = 'pw';
  form.dispatchEvent(new dom.window.Event('submit', { bubbles: true, cancelable: true }));
  ok(!lastPublish(calls), 'a sign-in without a country sends nothing');
  country.value = 'GR';
  ev(country, 'change');
  ok(!submit.disabled, 'a chosen country enables Sign in');
}

/* ---------- review fixes ---------- */
{
  /* A second FP2 found after the card was drawn brings the picker. */
  const { root, card } = mount();
  ok(!root.querySelector('select.devsel'), 'one device, no picker');
  card.hass = { states: states({ [E.hub]: hub([LIVING, OFFICE]) }), callService: () => {} };
  ok(card.shadowRoot.querySelector('select.devsel'), 'a device found later brings the picker');
}

{
  /* An override holds on a pinned card only. */
  const st = states({ [E.hub]: hub([LIVING, OFFICE]),
    'button.renamed_refresh': { state: 'unknown', attributes: {} } });
  const calls = [];
  const free = new Card();
  document.body.appendChild(free);
  free.setConfig({ type: 'custom:fp2-companion-card', entities: { refresh: 'button.renamed_refresh' } });
  free.hass = { states: st, callService: (d, sv, data) => calls.push(data) };
  free.shadowRoot.querySelector('.foot button').click();
  eq(calls[calls.length - 1].entity_id, E.refresh, 'with the picker an override is ignored');
  const pinned = new Card();
  document.body.appendChild(pinned);
  pinned.setConfig({ type: 'custom:fp2-companion-card', device: P,
    entities: { refresh: 'button.renamed_refresh' } });
  pinned.hass = { states: st, callService: (d, sv, data) => calls.push(data) };
  pinned.shadowRoot.querySelector('.foot button').click();
  eq(calls[calls.length - 1].entity_id, 'button.renamed_refresh', 'pinned, the override holds');
  /* A config change applies at the next update, without a device switch. */
  pinned.setConfig({ type: 'custom:fp2-companion-card', device: P });
  pinned.hass = { states: st, callService: (d, sv, data) => calls.push(data) };
  pinned.shadowRoot.querySelector('.foot button').click();
  eq(calls[calls.length - 1].entity_id, E.refresh, 'an edited config takes effect at once');
}

/* ---------- the browser crypto against Python and Node ---------- */
{
  const nodeCrypto = require('crypto');
  ['', 'a', 'abc', 'correct horse battery staple', 'Καλημέρα κόσμε', 'x'.repeat(1000)]
    .forEach((text) => eq(md5Hex(text),
      nodeCrypto.createHash('md5').update(text, 'utf8').digest('hex'),
      `md5 of ${JSON.stringify(text.slice(0, 12))}`));

  /* A throwaway key pair: the card encrypts with its public half, Python's
   * cryptography decrypts with the private half and must get the MD5 back. */
  const { publicKey, privateKey } = nodeCrypto.generateKeyPairSync('rsa', { modulusLength: 1024 });
  const jwk = publicKey.export({ format: 'jwk' });
  const big = (b64u) => BigInt(`0x${Buffer.from(b64u, 'base64url').toString('hex')}`);
  const md5 = md5Hex('pässwörd');
  const blob = rsaPkcs1v15(md5, big(jwk.n), big(jwk.e));
  const { execFileSync } = require('child_process');
  const pem = privateKey.export({ format: 'pem', type: 'pkcs8' });
  const out = execFileSync('python3', ['-c', `
import base64, sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
key = serialization.load_pem_private_key(sys.argv[1].encode(), None)
print(key.decrypt(base64.b64decode(sys.argv[2]), padding.PKCS1v15()).decode())
`, pem, blob]).toString().trim();
  eq(out, md5, 'Python decrypts the browser block to the MD5');
  const again = rsaPkcs1v15(md5, big(jwk.n), big(jwk.e));
  ok(again !== blob, 'the padding is random, so two blocks differ');
  eq(Buffer.from(encryptPassword('x'), 'base64').length, 128,
    'the Aqara key gives a 128-byte block');
}

/* ---------- live dots ---------- */
/* A fake of Home Assistant's websocket connection: it records the MQTT
 * subscription and lets the test deliver messages on it. */
function fakeConnection(reject = false) {
  const conn = { subs: [], unsubs: 0 };
  conn.subscribeMessage = (cb, msg) => {
    if (reject) return Promise.reject(new Error('Unauthorized'));
    conn.subs.push({ cb, msg });
    return Promise.resolve(() => { conn.unsubs += 1; });
  };
  conn.send = (payload) => conn.subs.forEach((x) => x.cb({
    topic: x.msg.topic, payload: JSON.stringify(payload) }));
  return conn;
}

async function liveChecks() {
  {
    const conn = fakeConnection();
    const { card, root, leases } = mount(undefined, conn);
    await Promise.resolve();
    const lease = JSON.parse(leases[0].data.payload);
    eq(leases[0].data.topic, 'fp2companion/54ef44a1b2c3/cmd/track', 'a lease names the device');
    ok(lease.on === true && typeof lease.client === 'string' && lease.client.length > 4,
      'a lease carries this tab as its client');
    eq(conn.subs[0] && conn.subs[0].msg.type, 'mqtt/subscribe', 'the card listens through HA MQTT');
    eq(conn.subs[0] && conn.subs[0].msg.topic, 'fp2companion/54ef44a1b2c3/live', 'on the device live topic');
    eq(root.querySelector('.livebar').textContent, 'Connecting to the live view',
      'the live line before a push');
    ok(!root.querySelector('.livebar').hidden, 'is shown under the map');

    conn.send({ state: 'live', ts: 1, targets: [
      { id: 9, type: 0, col: 7.2, row: 10.36, zone: 2 },
      { id: 3, type: 1, col: 1, row: 2, zone: null },
      { id: 4, type: 0, col: 40, row: 2, zone: null }] });
    const dots = [...root.querySelectorAll('.map .live circle:not(.halo)')];
    eq(dots.length, 2, 'a dot per target on the map, none off it');
    const halos = [...root.querySelectorAll('.map .live circle.halo')]
      .filter((h) => h.style.display !== 'none');
    eq(halos.length, 1, 'only a person pulses, not an interference source');
    eq(halos[0].getAttribute('cx'), '72.0', 'the halo sits on its dot');
    ok(halos[0].nextSibling && halos[0].nextSibling.classList.contains('person'),
      'and is drawn under it');
    const person = root.querySelector('.map .live circle.person');
    eq(person && person.getAttribute('cx'), '72.0', 'col 7.2 is 72 units across');
    eq(person && person.getAttribute('cy'), '103.6', 'row 10.36 is 103.6 units down');
    ok(root.querySelector('.map .live circle.ghost'), 'an interference source is a ring');
    eq(root.querySelector('.livebar').textContent, 'Live · 2 people · 1 interference',
      'the live line counts people and interference apart');
    ok(root.querySelector('.livebar').classList.contains('on'), 'and pulses while live');
    ok(root.querySelector('.map .live') === root.querySelector('.map').lastChild,
      'the dots are drawn over everything');

    conn.send({ state: 'live', ts: 2, targets: [{ id: 9, type: 0, col: 8, row: 11 }] });
    const moved = root.querySelector('.map .live circle.person');
    ok(moved === person, 'a target keeps its dot, so it glides');
    eq(moved.getAttribute('cx'), '80.0', 'and the dot moves');
    eq(root.querySelectorAll('.map .live circle:not(.halo)').length, 1,
      'a target that left loses its dot');
    eq(root.querySelectorAll('.map .live circle.halo').length, 1, 'and its halo');

    root.querySelector('.legend .badge').click();
    ok(root.querySelector('.map.editing .live'), 'the dots stay on while editing');
    eq(root.querySelectorAll('.map .live circle:not(.halo)').length, 1,
      'and are redrawn on the new map');

    card.remove();
    eq(conn.unsubs, 1, 'a removed card stops listening');
    const off = JSON.parse(leases[leases.length - 1].data.payload);
    ok(off.on === false && off.client === lease.client, 'and gives its lease back');
  }
  {
    /* The FP2 sends no push while it tracks nobody (2026-10-31). */
    const conn = fakeConnection();
    const { card, root } = mount(undefined, conn);
    await Promise.resolve();
    conn.send({ state: 'watching' });
    eq(root.querySelector('.livebar').textContent, 'Live · nobody in view',
      'subscribed with no push is an empty room, not a connection in progress');
    ok(root.querySelector('.livebar').classList.contains('on'), 'and is shown as live');
    conn.send({ state: 'live', ts: 1, targets: [{ id: 9, type: 0, col: 7, row: 10 }] });
    eq(root.querySelectorAll('.map .live circle.person').length, 1, 'a push draws its dot');
    card._liveAt = Date.now() - 6000;
    card._drawLive();
    eq(root.querySelector('.livebar').textContent, 'Live · nobody in view',
      'a stream that went quiet is an empty room, not a warning');
    ok(root.querySelector('.livebar').classList.contains('on'), 'and stays live');
    eq(root.querySelectorAll('.map .live circle').length, 0,
      'and the last dots are taken off the map');
    card.remove();
  }
  {
    const conn = fakeConnection(true);
    const { root, leases } = mount(undefined, conn);
    await Promise.resolve();
    await Promise.resolve();
    eq(root.querySelector('.livebar').textContent, 'Live view needs an admin user',
      'a user without MQTT access is told so');
    eq(JSON.parse(leases[leases.length - 1].data.payload).on, false,
      'and the lease is given back');
  }
  {
    const st = states({ [E.hub]: hub([LIVING, OFFICE]) });
    const { card, leases } = mount(st, fakeConnection());
    await Promise.resolve();
    const pick = card.shadowRoot.querySelector('.devsel');
    ok(!pick.closest('.head'), 'the device picker has its own row, outside the head');
    eq(pick.options[1].textContent, 'Motion Sensor - Office · Office',
      'and shows the full name and area');
    pick.value = OFFICE.key;
    ev(pick, 'change');
    const topics = leases.map((x) => [x.data.topic, JSON.parse(x.data.payload).on]);
    eq(JSON.stringify(topics.slice(-2)), JSON.stringify([
      ['fp2companion/54ef44a1b2c3/cmd/track', false],
      ['fp2companion/54ef44d4e5f6/cmd/track', true]]), 'switching device moves the lease');
    card.remove();
  }
  {
    const { card, leases } = mount(states({ [E.hub]: { ...hub(),
      attributes: { ...hub().attributes, signed_in: false } } }), fakeConnection());
    eq(leases.length, 0, 'no session, no lease');
    card.remove();
  }
}

async function viewChecks() {
  /* A fake IntersectionObserver the test drives by hand. */
  const observers = [];
  global.IntersectionObserver = class {
    constructor(cb) { this.cb = cb; this.on = false; observers.push(this); }
    observe() { this.on = true; }
    disconnect() { this.on = false; }
  };
  const { card, leases } = mount(undefined, fakeConnection());
  await Promise.resolve();
  const obs = observers[observers.length - 1];
  ok(obs && obs.on, 'the card watches whether it is on screen');
  const n = leases.length;
  obs.cb([{ isIntersecting: false }]);
  eq(JSON.parse(leases[leases.length - 1].data.payload).on, false,
    'scrolled away, the lease is given back');
  eq(leases.length, n + 1, 'once');
  obs.cb([{ isIntersecting: false }]);
  eq(leases.length, n + 1, 'and not again while it stays away');
  obs.cb([{ isIntersecting: true }]);
  eq(JSON.parse(leases[leases.length - 1].data.payload).on, true,
    'back on screen, the lease is taken again');
  card.shadowRoot.querySelectorAll('.panetabs button')[1].click();
  eq(JSON.parse(leases[leases.length - 1].data.payload).on, false,
    'the Settings pane has no map, so the lease is given back');
  card.shadowRoot.querySelectorAll('.panetabs button')[0].click();
  eq(JSON.parse(leases[leases.length - 1].data.payload).on, true,
    'and taken again on the Zones pane');
  card.remove();
  ok(!obs.on, 'a removed card stops watching');
  delete global.IntersectionObserver;
}

liveChecks().then(viewChecks).then(() => {
  /* Every card left attached holds a renewal timer. */
  document.body.innerHTML = '';
  console.log(`${checks - failures}/${checks} checks passed`);
  process.exit(failures ? 1 : 0);
});
