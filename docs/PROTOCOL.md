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
| `4.21N.85` | Written after a detection zone write, see Zones | 1 (a number) |
| `4.22.700` | Live targets | JSON, see below |

While AI person detection is on, the device reports sensitivity as 3 and
ignores writes to it. AI learning runs for about 60 seconds and reports no
progress in any resource, so the card counts down on its own.

## Zones

`GET devex/radar/range/list?deviceId=<did>` returns the zones. A zone has a
`rangeId`, a `name`, a `regionType` (0 detection, 1 interference, 2
entrance and exit, 3 the room's edge), a `sensitivity`, a `tagId` and
`tagName`, a `colorId` and a `blockList`.

A PS-T02E reported an entrance and exit zone on 2026-10-31, and a PS-S02D
got one on 2026-11-01 when its owner turned a detection zone into one in the
Aqara app. A device holds at most one entrance and exit zone, one edge and
one interference zone.

The map is a grid of 16 columns and 20 rows. Block ids run from 1 to 320 in
row-major order, so a block id is `row * 16 + column + 1` with both counted
from 0. The order of `blockList` is not kept by the cloud, so zones are
compared as sets. Zones may share blocks.

`POST devex/radar/range/set` with one zone item rewrites the zone named by
its `rangeId`. Without a `rangeId` it creates a zone, and the server assigns
the next free id and turns an empty `tagId` into `custom_Other`.
`POST devex/radar/range/del` with `{"deviceId", "rangeIdList": [...]}`
deletes zones. A `range/set` with an empty `blockList` is not a delete.

A zone of `regionType` 3 marks the room's edge. It holds every block
OUTSIDE the room, where the FP2 does not look. On 2026-10-31 one FP2 had a
zone named "Edge" with `rangeId` 103 covering 288 of the 320 blocks; the 32
it left out formed a 4 by 8 room that held that FP2's other zones.

On 2026-10-31 the Aqara app took one block out of that edge with a
`range/set` that carried six fields: `deviceId`, `rangeId`, `regionType`,
`name`, `colorId` and `blockList`. It sent no `sensitivity`, `tagId` or
`tagName`, and the server kept the values the zone already had for those
three. FP2 Companion writes the edge the same way. It refuses to rename the
edge, to give it a type or a sensitivity, and to create or delete one.

The Aqara app also lets a person change a zone's attribute, which is its
`regionType`. Its zone editor offers Detection zone, Exits and Entrances and
Boundary, the last being the edge. On 2026-11-01 three changes were captured
on a PS-S02D:

- The edge became a detection zone with a `range/set` that carried the full
  detection fields: `regionType` 0, `rangeId` 103, the name "Detection Zone
  3" chosen by the Aqara app, `tagId` `custom_` with `tagName` "Other", and
  `sensitivity` 1.
- That zone went back to the edge with the six edge fields, `regionType` 3
  and the name "Boundary". The Aqara app sent `rangeId` 3, not 103.
- Detection zone 2 became an entrance and exit zone with the same six
  fields, `regionType` 2 and the name "Exits and Entrances". Its blocks had
  included the interference zone's only block, and the Aqara app left that
  block out of the request. The server kept the zone's `sensitivity` and set
  `tagId` to `custom_` with an empty `tagName`.

The server renumbers a zone whose attribute changes. Detection zones get
ids from 1 up, and every other kind gets 100 plus its `regionType`: the
interference zone is 101, the entrance and exit zone 102 and the edge 103.
After the changes above a refresh read the edge as 103 again and the former
zone 2 as 102.

The Aqara app redraws an entrance and exit zone and an interference zone
with the same six fields it sends for the edge (captured 2026-11-01).

When a person picks an attribute that another zone already holds, the Aqara
app warns "This attribute has been configured" and offers Modify. Modify does
not convert the zone being edited. It opens the zone that holds the
attribute for editing and drops the unsaved changes, so on 2026-11-01 the
only writes that followed were to that zone.

FP2 Companion makes the same changes the same way. It changes an attribute
only from or to detection, because a direct change between an entrance zone
and the edge was never captured, and it refuses an attribute another zone
holds. It leaves the interference zone's blocks out of an entrance zone
before it sends it. After a change it finds the zone again by its attribute,
name and blocks, since its `rangeId` has changed.

After each `range/set` of a detection zone, an edit or a create, the Aqara
app writes the resource `4.21N.85` as the number 1, where N is the zone's
`rangeId`: `4.211.85` after an edit of zone 1, `4.214.85` after creating
zone 4, and `4.213.85` when the edge became detection zone 3. It writes
nothing of the kind after an edge write, an entrance write or a delete. The
captures of 2026-10-30 show the same writes. What the resource does is not
known, and FP2 Companion does not write it.

A zone's type is a tag. The Aqara app fills its type list from
`GET app/tag/bottom/list?tagType=9`. On 2026-10-31 that returned 30 tags,
each with a `tagId` such as `1.2` (Sofa Area) or `3.6` (Bed), a `tagName` in
the language of the session, a `parentType` that groups them, and an `extend`
string such as `{"sensitivity":"3"}` that holds the sensitivity the Aqara app
suggests for that type. "Other" is not in the list: a zone without a type
reads as `custom_Other` named `Other`. FP2 Companion writes a type by
sending the zone to `range/set` with the new `tagId` and `tagName`, the same
way it writes the other fields.

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
   "value": "<json>"}}]`. They arrive only while the FP2 tracks someone.
   With the room empty, the subscribe and the heartbeat succeed and nothing
   is pushed. This was observed on 2026-10-31: both FP2s reported no
   occupancy, and a 75 second subscription received no push.
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
