/*
 * Render fp2-companion-card from the LIVE Home Assistant states to a standalone
 * HTML file, so it can be screenshotted in a headless browser and looked at.
 *
 *   HA_URL=http://homeassistant.local:8123 HA_TOKEN=<long-lived token> \
 *     NODE_PATH="$(npm root -g)" node tests/card/fp2-companion-render.js > ~/fp2.html
 *   chromium --headless --screenshot=$HOME/fp2.png --window-size=420,1500 ~/fp2.html
 *
 * The snap Chromium cannot read /tmp, so write the HTML under $HOME.
 * CARD_THEME=light renders the light palette, CARD_WRAP_WIDTH the card width
 * (380 is a phone, 500 a dashboard column), CARD_EDIT=<rangeId> the editor.
 * CARD_PANE=settings or backups opens that pane, and CARD_DEVICE=<serial or
 * name> pins the card to one FP2.
 * CARD_LIVE=1 draws sample live dots (a person in each detection zone and one
 * interference source), or CARD_LIVE='<json list of targets>' those targets.
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const { JSDOM } = require('jsdom');
const CARD = process.env.CARD_PATH || path.join(__dirname, '..', '..', 'apps', 'fp2_companion', 'card', 'fp2-companion-card.js');
const HA_URL = process.env.HA_URL || 'http://homeassistant.local:8123';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
Object.assign(global, {
  window: dom.window, document: dom.window.document,
  HTMLElement: dom.window.HTMLElement, customElements: dom.window.customElements,
  CustomEvent: dom.window.CustomEvent,
});
/* The card announces its version with console.info, which jsdom prints to
 * stdout, and stdout is the HTML this script writes. */
console.info = () => {};
new Function(fs.readFileSync(CARD, 'utf8'))();
const Card = customElements.get('fp2-companion-card');

/* HA_TOKEN, or the file HA_TOKEN_FILE (default ~/.ha_token) that holds it. */
const token = process.env.HA_TOKEN || fs.readFileSync(process.env.HA_TOKEN_FILE
  || path.join(process.env.HOME, '.ha_token'), 'utf8').trim();
const raw = JSON.parse(execFileSync('curl', ['-s', '-H', `Authorization: Bearer ${token}`,
  `${HA_URL}/api/states`], { maxBuffer: 64 * 1024 * 1024 }).toString());
const states = {};
raw.forEach((s) => { states[s.entity_id] = s; });

const dark = process.env.CARD_THEME !== 'light';
const THEME = dark ? `
  :root {
    --primary-text-color: #e1e1e1; --secondary-text-color: #9b9b9b;
    --divider-color: rgba(225,225,225,0.14); --card-background-color: #1c1c1c;
    --ha-card-background: #1c1c1c; --secondary-background-color: #2a2a2a;
    --error-color: #ef5350; --warning-color: #ffa726; --success-color: #4caf50;
    --primary-color: #03a9f4;
  }
  body { margin: 0; background: #111111; font-family: Roboto, sans-serif; }
` : `
  :root {
    --primary-text-color: #212121; --secondary-text-color: #727272;
    --divider-color: #e0e0e0; --card-background-color: #ffffff;
    --ha-card-background: #ffffff; --secondary-background-color: #f1f3f5;
    --error-color: #db4437; --warning-color: #f57c00; --success-color: #2e7d32;
    --primary-color: #03a9f4;
  }
  body { margin: 0; background: #f2f4f7; font-family: Roboto, sans-serif; }
`;

const card = new Card();
document.body.appendChild(card);
/* CARD_DEVICE pins the card to one FP2, which also hides the device picker. */
card.setConfig({ type: 'custom:fp2-companion-card',
  ...(process.env.CARD_DEVICE ? { device: process.env.CARD_DEVICE } : {}) });
card.hass = { states, callService: () => {} };
/* CARD_EDIT=<rangeId> opens the editor on that zone, since a render cannot
 * press anything. */
if (process.env.CARD_PANE) card._selectPane(process.env.CARD_PANE);
/* The live dots arrive over a websocket the render does not have, so the
 * message is handed to the card directly. */
if (process.env.CARD_LIVE) {
  let targets;
  if (process.env.CARD_LIVE === '1') {
    const za = card._za || {};
    const cols = (za.grid || { columns: 16 }).columns;
    targets = (za.zones || []).filter((z) => z.regionType !== 1 && (z.blockList || []).length)
      .map((z, i) => {
        const b = z.blockList[Math.floor(z.blockList.length / 2)] - 1;
        return { id: i, type: 0, col: (b % cols) + 0.5, row: Math.floor(b / cols) + 0.5, zone: z.rangeId };
      });
    targets.push({ id: 19, type: 1, col: 3.3, row: 17.6, zone: null });
  } else {
    targets = JSON.parse(process.env.CARD_LIVE);
  }
  card._onLive(card._liveKey, { payload: JSON.stringify({ state: 'live', ts: 1, targets }) });
}
if (process.env.CARD_EDIT) {
  const zones = card._za.zones || [];
  card._startEdit(zones.find((z) => String(z.rangeId) === process.env.CARD_EDIT));
}

process.stdout.write(`<!doctype html><html><head><meta charset="utf-8">
<style>${THEME}
  .wrap { width: ${process.env.CARD_WRAP_WIDTH || '380'}px; padding: 12px; }
  ha-card { display: block; background: var(--ha-card-background);
    border-radius: 12px; border: 1px solid ${dark ? 'rgba(225,225,225,0.08)' : '#e3e6ea'}; }
</style></head><body><div class="wrap">${card.shadowRoot.innerHTML}</div></body></html>
`);
/* Detached, the card drops its live lease and the renewal timer that would
 * otherwise keep node running. */
card.remove();
