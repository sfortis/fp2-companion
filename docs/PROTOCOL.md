# The Aqara Cloud API as fp2_companion Uses It

This page describes the private API of the Aqara Home Android app, as far as
this project uses it for the FP2 (model `lumi.motion.agl001`). Aqara does not
document or support this API. It was worked out from the app's own logs, its
runtime code and its web pages, and every request below was then proven
against a real account and a real FP2. It can change without notice.

The app identity (the `Appid` and `Appkey` that sign every request) is the
Aqara Home app's own, the same for every user. This project's pair for each
region is in `regions.py`.

## Hosts and Regions

An account lives on the servers of one region, and the region follows from
the country chosen at sign-in. Every path below is relative to
`<host>/app/v1.0/lumi/`.

| Region | Host |
|---|---|
| EU, AF | `https://rpc-ger.aqara.com` |
| US, HMT, OTHER | `https://aiot-rpc-usa.aqara.com` |
| CN | `https://aiot-rpc.aqara.cn` |
| RU | `https://rpc-ru.aqara.com` |
| KR, JP | `https://rpc-kr.aqara.com` |
| AU, ME | `https://rpc-au.aqara.com` |

The Aqara app gets its country to region table from its server at run time,
so the table in `regions.py` is this project's approximation. A wrong region
shows as a failed sign-in, never as a silent misroute.

## Signing

Every request carries the app's header set: `Appid`, `Nonce`, `Time` (epoch
milliseconds), `Sign`, `PhoneId`, `Lang`, `Cuty` (the country), `Area` (the
region), `App-Version`, `Phone-Model`, `Sys-Type`, `Sys-Version` and
`User-Agent: okhttp/4.12.0`. After sign-in it also carries `Token` and
`UserId`.

The signature is an MD5 in hex over the parts joined with `&`:

```
Appid=<appid>&Nonce=<nonce>&Time=<ms>&Token=<token>&<payload>&<appkey>
```

The payload is the JSON body of a POST and the query string of a GET. At
sign-in there is no token, and the `Token=` part is left out entirely. The
nonce is 32 hex digits, upper case at sign-in and lower case after it.
Headers sent beside the signed set, such as `Clientid`, are not signed.

## Answers

Every answer is `{"code", "message", "result"}`, and only code 0 is success.

| Code | Meaning |
|---|---|
| 0 | Success |
| 106 | Invalid sign: this region does not accept this app identity |
| 108 | The token expired or was revoked |
| 810 | Sign-in to an account that does not exist; the signature was accepted |
| 706 | The zone list of a device that is not on this account |
| 755 | A resource query of a device that is not on this account |

## Session

`POST user/login` takes `{"account", "encryptType": 2, "password"}`. The
password is the MD5 hex of the clear password, encrypted with RSA PKCS#1
v1.5 under Aqara's public 1024-bit key and sent as base64. The card does this
in the browser, so the clear password never leaves it. The answer carries
the token and the user id.

`POST user/refresh-token` with the body `{}` returns a new token and revokes
the old one at once. The new token must therefore be stored before anything
else is sent, or the session is lost. A token lives about 30 days, and the
app refreshes it when fewer than three days remain. A code 108 gets one
refresh and one retry.

Each session file keeps one `PhoneId`. A new sign-in reuses it, so the
account does not collect a new parallel session on every login.

## Device Id

The cloud device id of an FP2 is `lumi1.` followed by its serial in lower
case, as HomeKit reports the serial: `54EF44A1B2C3` becomes
`lumi1.54ef44a1b2c3`.

## Resources

`POST res/query/by/resourceId` with
`{"data": [{"options": ["<rid>", ...], "subjectId": "<did>"}]}` reads
values. `POST res/write` with `{"subjectId": "<did>", "data": {"<rid>":
"<value>"}}` writes them.

A written value must be a JSON string. `{"14.1.85": 2}` is acknowledged with
code 0 and never reaches the device, while `{"14.1.85": "2"}` does. The one
exception is the view heartbeat below, which the Aqara app itself writes as
the number 1.

| Rid | Meaning | Values |
|---|---|---|
| `14.1.85` | Sensitivity | 1 Low, 2 Medium, 3 High |
| `14.47.85` | Approach distance | 0 Far, 1 Middle, 2 Close |
| `4.72.85` | AI person detection | 1 on, 0 off |
| `4.66.85` | Starts AI learning | 1 |
| `4.22.85` | Device page heartbeat | 1 (a number) |
| `4.22.700` | Live targets | JSON, see below |

While AI person detection is on, the device reports sensitivity as 3 and
ignores writes to it. AI learning runs for about 60 seconds and reports no
progress in any resource, so the card counts down on its own.

## Zones

`GET devex/radar/range/list?deviceId=<did>` returns the zones. A zone has a
`rangeId`, a `name`, a `regionType` (0 detection, 1 interference), a
`sensitivity`, a `tagId` and `tagName`, a `colorId` and a `blockList`.

The map is a grid of 16 columns and 20 rows. Block ids run from 1 to 320 in
row-major order, so a block id is `row * 16 + column + 1` with both counted
from 0. The order of `blockList` is not kept by the cloud, so zones are
compared as sets. Zones may share blocks.

`POST devex/radar/range/set` with one zone item rewrites the zone named by
its `rangeId`. Without a `rangeId` it creates a zone, and the server assigns
the next free id and turns an empty `tagId` into `custom_Other`.
`POST devex/radar/range/del` with `{"deviceId", "rangeIdList": [...]}`
deletes zones. A `range/set` with an empty `blockList` is not a delete.

## Live Positions

The Aqara app draws a dot on the map for every target the FP2 tracks. The
cloud pushes the targets over a websocket, and two things are needed before
it pushes anything.

1. Open `wss://<host>/app/v1.0/lumi/push/ws/aqarahome` with the same signed
   headers as an HTTP call, signed over an empty payload, without `UserId`.
2. The first message is `{"success": true, "uniqueKey": "..."}`.
3. `POST app/view/subscribe` with
   `{"subjectId": "<did>", "viewId": "control", "version": "1.8"}` and the
   extra header `Clientid: <uniqueKey>`. The view id and version are the
   ones the Aqara app caches for this model.
4. Write the device page heartbeat, `{"4.22.85": 1}`, and write it again
   every 60 seconds. Without it the FP2 sends no positions at all, whether
   or not a socket has subscribed. With it and without a subscription,
   nothing is pushed either.
5. Pushes arrive about every 0.75 seconds as
   `[{"result": {"attr": "4.22.700", "subjectId", "timeStamp",
   "value": "<json>"}}]`.
6. `POST app/view/unsubscribe` with the same body ends it. The Aqara app
   unsubscribes and subscribes again every 300 seconds.

The value holds 20 slots of `{id, x, y, rangeId, targetType, state}`. A
slot with `state` "1" is a live target. `targetType` 0 is a person and 1 is
an interference source. `rangeId` is the zone the target is in.

For a wall mount, the Aqara app's page places a target at
`column = 8 - x / 50` and `row = y / 50` on the 16 by 20 grid, because it
turns the point by 180 degrees. A ceiling mount and the app's "reverse"
mounting setting use other transforms that this project has not checked.

A single `res/query/by/resourceId` of `4.22.700` returns one snapshot, but
only while something writes the heartbeat.

Any client that writes the heartbeat keeps the FP2 streaming to the cloud.
The Aqara app does that for as long as its device page is open.
