# Push channels from zero: Bark and ntfy

This project doesn't send SMS or build its own app — when your AI wants to
reach you, it goes through **Bark or ntfy**, two third-party free and
open-source push tools. Neither is affiliated with this project; think of
them as couriers whose only job is "drop a message into your phone's
notification tray."

If you've never heard either name, this doc starts from zero: what they are,
how to install them, and how to fill them into `config.yaml`.

## What is Bark (iOS)

Bark is a free, open-source push app for iOS. It does exactly one thing:
**receive push notifications someone sends you**.

1. Open the App Store, search "Bark", install it.
2. Open the app — you'll see an address like
   `https://api.day.app/YOUR_KEY_HERE`. That's your personal push address.
   `YOUR_KEY_HERE` is a random string the app generates for you (not a
   password you set; you don't need to remember it, it's always visible in
   the app).
3. Whoever sends a request to that address (in this case, the process running
   this project) makes your phone buzz with a notification.

Messages are relayed through Bark's official server (`api.day.app`) by
default. If you'd rather not route through a third-party server, Bark also
supports full self-hosting — see the official repo
[github.com/Finb/Bark](https://github.com/Finb/Bark) for self-hosting
instructions.

### Filling in config.yaml

For the Runtime's core scheduler (`providers.notification`):

```yaml
providers:
  notification: bark
  bark:
    url: https://api.day.app/YOUR_KEY_HERE   # the address you saw in the app
```

If you've also wired up the emotional sentinel (`adapters/sentinel`, an
optional component), it uses a different pair of keys — **just the key
itself**, not the full address:

```yaml
sentinel:
  push_provider: bark
  bark_key: "YOUR_KEY_HERE"   # just the key; or use env var RR_BARK_KEY instead
```

## What is ntfy (Android / desktop / everywhere)

ntfy is a free, open-source push service that works on Android, iOS, and
desktop browsers — and it's the **go-to for Android users** specifically,
since Bark is iOS-only.

1. Android users: search "ntfy" on Google Play or F-Droid and install it.
2. ntfy doesn't use accounts or passwords — it works by **subscribing to a
   topic**. A topic is essentially a passphrase you make up (a string).
   Whoever knows the topic name can publish to it / receive its pushes, so
   **pick something long and random** — don't use something guessable like
   "my-notifications".
3. Open the app, tap subscribe, type in the topic name you chose.

By default it goes through ntfy's official public server
[ntfy.sh](https://ntfy.sh), free to use out of the box. It also supports full
self-hosting (a one-line Docker command); see the official docs at
[docs.ntfy.sh](https://docs.ntfy.sh) for self-hosting instructions.

### Filling in config.yaml

For the Runtime's core scheduler (`providers.notification`):

```yaml
providers:
  notification: ntfy
  ntfy:
    url: https://ntfy.sh              # public server; swap for your own if self-hosted
    topic: "your-long-random-topic"   # the topic you chose — long and random
    token: ""                         # only needed if your self-hosted instance has auth
```

For the emotional sentinel (`adapters/sentinel`, an optional component):

```yaml
sentinel:
  push_provider: ntfy
  ntfy:
    url: https://ntfy.sh
    topic: "your-long-random-topic"   # or use env var RR_NTFY_TOPIC
    token: ""
```

## Which one should I pick

- **iPhone** → Bark. Install the app, open it, the address is right there —
  no need to invent a name.
- **Android** → ntfy. Bark is iOS-only and won't work.

## Privacy note

Either way, your push content passes through a third-party server —
`api.day.app` for Bark by default, `ntfy.sh` for ntfy by default. That means
the operator's server can, in principle, see the push content that passes
through it (this project has its own controls, like
`privacy.store_message_text`, over how much detail goes into a push body —
but once it's sent, the relay in the middle belongs to a third party).

If that bothers you, both support self-hosting, so only your own server ever
sees it:

- Bark self-hosting: see the
  [github.com/Finb/Bark](https://github.com/Finb/Bark) repo.
- ntfy self-hosting: see the self-hosting docs at
  [docs.ntfy.sh](https://docs.ntfy.sh).
