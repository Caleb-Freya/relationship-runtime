# Location + Weather bridge

A small host-side collector that tells your Relationship Runtime **where your
person is and what the weather is like there** — so the AI's proactive loop can
care about it, not just about chat.

> "It's 4°C and you've been out for two hours — text her to put a coat on."
> "She's been at the hospital since morning — check in gently."

It feeds the Runtime's existing health-snapshot channel (`POST /api/health`), so
no changes to the Runtime are needed. Everything runs on your own machine; the
snapshot only ever travels to your local Runtime over `127.0.0.1`.

## What you need

| Piece | Options | Needs an app? | Needs a key? |
|-------|---------|---------------|--------------|
| **Location** | OwnTracks (self-hosted, background) **or** a manual lat/lon you set by hand | OwnTracks: yes · manual: no | no |
| **Weather + place names** | AMap / 高德 (precise, China, street-level) | no | yes — your own free AMap key |
| **Weather (no key, worldwide)** | Open-Meteo | no | no |
| **Wearable (stress/sleep)** | *not required* — this bridge doesn't need one | — | — |

The point of the table: **you don't need a smartwatch, and you don't need to
install anything for the weather.** The only thing that costs an app is
background location via OwnTracks — and even that you can skip with a manual
lat/lon.

## Setup

1. **Copy the config:**
   ```bash
   cp config.example.json config.json
   cp places.example.json places.json   # optional, for named places
   ```

2. **Point it at your Runtime.** Set `runtime_url` (default
   `http://127.0.0.1:18200`) and give it the token — either export it as the env
   var named in `runtime_token_env` (default `RR_TOKEN`) or paste it into
   `runtime_token`. This is the same `mcp.token` from your Runtime's
   `config.yaml`.

3. **Pick a location source** (`location.source`):
   - `owntracks_file` — run the companion self-hosted location backend
     ([eyes-for-you / geo-track](https://github.com/45694354xm/eyes-for-you) or
     any OwnTracks HTTP recorder) and point `path` at its `latest.json`.
   - `manual` — set `lat`/`lon` by hand (no app at all; good for a fixed home
     setup or testing).

4. **Weather + street-level place names — get a free AMap key** (recommended for
   China): register at <https://lbs.amap.com/>, create a **Web 服务 (REST)** key,
   put it in `amap_key`. That single key powers both the weather and the
   "华仑港湾幼儿园 11m" style precise address.
   - **Outside China / don't want a key?** Set `"weather_provider": "open-meteo"`
     — worldwide, keyless, no app. You lose Chinese street-level place names
     (you still get weather and your own named geofences).

5. **(Optional) name your places.** Edit `places.json` with the spots you want
   the AI to recognize by name (home, work, the hospital). To read a place's
   coordinates once: open your OwnTracks map, or visit
   `https://uri.amap.com/marker?position=LON,LAT` after a report, or just stand
   there and copy the lat/lon the bridge prints. `home_place_names` lists which
   place names count as "home" (so `out` is computed correctly).

6. **Test it:**
   ```bash
   python3 bridge.py --dry-run     # prints the snapshot, doesn't send
   python3 bridge.py               # sends one snapshot to the Runtime
   ```

7. **Run it on a schedule** (every 5 minutes is plenty):
   ```cron
   */5 * * * * /path/to/adapters/location-weather-bridge/bridge-cron.sh >> /tmp/rr-bridge.log 2>&1
   ```

## The snapshot it sends

```json
{
  "updated_at": "2026-09-04T17:25:50+0800",
  "battery": 50,
  "location": {"lat": 31.31, "lon": 118.36, "acc": 18},
  "place": "家",
  "out": false,
  "loc_age_sec": 61,
  "weather": "晴", "temp": 29.0, "wind": "北风4级", "humidity": 54.0,
  "place_address": "…华仑港湾幼儿园"
}
```

`updated_at` **must be an ISO-8601 string** (the Runtime parses it); the bridge
already formats it that way — don't change it to an epoch number.

## Making the AI act on it

The bridge only *delivers* the data. What the Runtime does with it depends on
your Policy. The default Policy reacts to `stress` / `sleep_hours` out of the
box; to make it also react to being out in the cold, low battery while out, or a
long stay somewhere, extend `_health_concern` in
`runtime/policy/default.py` to read these fields and raise a concern with a note
— the same shape the wearable signals already use. See the Composer's context:
these fields ride along in `relationship_context`, so even a template or LLM
Composer can weave "wrap up warm" into a proactive message without any Policy
change.

## Privacy

- The snapshot is sent **only** to your local Runtime over `127.0.0.1`.
- The bridge strips proxy env vars so AMap/weather calls go out over your own
  connection (an AMap key is often IP-whitelisted).
- `config.json` and `places.json` hold your key and your real coordinates —
  they are git-ignored here; keep them that way.
