# Domovoi alongside Home Assistant

Domovoi has **no Home Assistant integration**. There is no config flow, no
MQTT bridge and no device-control handler, and Domovoi does not control
lights, locks or thermostats. If you already run Home Assistant, this page
covers the two things you need to know: the two run side by side without
conflict, and Home Assistant can make Domovoi speak.

## Running both on one network

- **Ports don't overlap.** Home Assistant uses 8123. Domovoi uses 6369 for
  the dashboard, 6370 for the core API, 6432 for its Postgres (deliberately
  not 5432), and 6650+N / 8050+N for per-room MPD. The one possible clash
  on a shared machine is Ollama on 11434: share one instance or keep them on
  separate hosts.
- **No discovery overlap.** Domovoi registers no mDNS/zeroconf services, so
  Home Assistant won't discover it and it won't appear in Home Assistant's
  device list.
- **One voice system per microphone.** A Domovoi satellite owns its mic
  board. Don't run a Wyoming satellite against the same mic. Domovoi in some
  rooms and Home Assistant voice hardware in others is fine.

## Spoken announcements from Home Assistant

Home Assistant's `rest_command` can call Domovoi's announce endpoint to
speak a message on one satellite, or on every satellite if `room_id` is
omitted. A satellite that is mid-response is skipped rather than cut off,
and music resumes after the announcement.

The endpoint is on Domovoi's device tier (see
[SECURITY_PRIVACY.md](SECURITY_PRIVACY.md#the-tiers)), so it needs the
household device token. Read it from the dashboard (Settings) or from
`~/.domovoi/device-token.txt` on the server, and keep it in Home Assistant's
`secrets.yaml` as `domovoi_device_token`. If you rotate it in Domovoi,
update it here too.

A household token an admin chose may hold any printable ASCII, so **quote
the value in `secrets.yaml`**: `domovoi_device_token: "Maple Street, 1984!"`.
An unquoted YAML scalar that begins with `#`, `&`, `*`, `{`, `[`, `!` or `%`,
or that contains `: `, is not the string you think it is, and edge
whitespace is silently dropped.

```yaml
# configuration.yaml
rest_command:
  domovoi_announce:
    url: "http://<domovoi-server>:6370/v1/admin/announce"
    method: POST
    content_type: "application/json"
    headers:
      X-Device-Token: !secret domovoi_device_token
    payload: '{"room_id": "{{ room }}", "message": "{{ message }}"}'
```

Example: when the washing machine finishes, call `domovoi_announce` with
`room: kitchen` and `message: The wash is done`.

Endpoint details are in the [API reference](API_REFERENCE.md).
