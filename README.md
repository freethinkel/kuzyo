# kuzyo

Home server: Debian 13 with Incus on a ThinkPad T14 Gen 1 (i7-10510U, 32 GB,
512 GB NVMe). Корка кузё is the Udmurt house spirit, the master of the house.

Kept apart from `../ansible`, which provisions work machines. The `dev`
container's environment does come from there, like any other dev box.

## Layout

```
Debian 13 host    Incus, Tailscale, SSH, Home Assistant in Docker
├── apps          container with Docker: Immich and other compose stacks
├── dev           container for development over SSH, set up by ../ansible
├── pets          container with Docker: public pet projects (pets/)
└── ...           throwaway containers and VMs
```

Disk: ESP, 64 GB ext4 root, the rest btrfs on `/srv` for the Incus pool and
app data.

## Home Assistant

Runs on the host with host networking (`homeassistant/compose.yml`, data in
`/srv/homeassistant/config`). The host is on Wi-Fi, which cannot be bridged,
so a guest behind `incusbr0` would miss mDNS and SSDP discovery. Bluetooth
goes through BlueZ on the host over D-Bus.

HA is the source of truth, as in [ai/domovoy](https://github.com/ai/domovoy).
Change things in the UI, run `homeassistant/pull.sh`, commit. The script
copies the YAML into `homeassistant/config/` and renders areas and entities
into `homeassistant/home.yaml`.

## Names and the internet

Everything goes through the `edge` stack on the host (`edge/`):

- **Caddy** serves `*.home.freethinkel.dev` with a real wildcard certificate,
  obtained over DNS-01. The `*.home` record points at the LAN address, so
  these names work at home: `ha.home.freethinkel.dev`,
  `photos.home.freethinkel.dev`. Routes live in `edge/Caddyfile`.
  `home.freethinkel.dev`, also LAN only, is the Glance start page
  (`apps/glance/`).
- **cloudflared** publishes `photos.freethinkel.dev` to the internet through
  Cloudflare Tunnel `kuzyo`. Routes live in `edge/cloudflared.yml`; every
  public name also needs a proxied CNAME to `<tunnel id>.cfargotunnel.com`.
  Cloudflare caps a request at 100 MB, so big video uploads have to go over
  the LAN name. Turn on automatic URL switching in the Immich app.
- **Pet projects** (`pets/<name>/compose.yml`, listed in `pets` in
  `site.yml`) run in the `pets` container, apart from Immich since they face
  the internet. Each lives in `/srv/pets/<name>` (the same path on the host
  and in `pets`), is published on a host loopback port and goes out through
  the tunnel. Their repos deploy by rsync to `kuzyo:/srv/pets/<name>`, then
  `ssh kuzyo incus exec pets -- docker compose --project-directory /srv/pets/<name> up -d`.
  The nightly backup takes the whole of `/srv/pets`.
  - `phocus.freethinkel.dev` (`8100`) and `fmc.freethinkel.dev` (`8101`,
    with Caddy for the SPA in `www`) are PocketBase. `.env`, `pb_data` (and
    fmc's `watchfaces`) exist only there. They moved from DigitalOcean in
    October 2026.
  - `collie.freethinkel.dev` and `relay.freethinkel.dev` share Caddy on
    `8102`: the landing page in `site/` (the Collie repo's
    `make deploy-site`), and Collie's shared relay, livekit-server and
    collie-relay. Caddy passes Cloudflare's `Cf-Connecting-Ip` on as the
    client address. Call media cannot go through the tunnel (no UDP): the
    router forwards `7881/tcp` and `7882/udp` to `192.168.1.243` (Virtual
    Servers in the Cablenet gateway's own page, `http://192.168.1.1`, under
    the hidden `scvrtsrv.cmd`; forwards made in the Plume app and NAT-PMP
    mappings never reach the WAN), the host
    DNATs them to `pets` (`10.45.1.49`, fixed), and LiveKit finds the public
    address over STUN. The `collie-relay` image is built from the Collie
    repo on the host's Docker, then loaded into `pets`:
    `DOCKER_HOST=ssh://kuzyo docker build --network host -f relay/deploy/Dockerfile -t collie-relay:latest desktop`,
    `ssh kuzyo 'docker save collie-relay:latest | incus exec pets -- docker load'`,
    then `up -d` as above. Pushes need the APNs key in
    `/srv/pets/collie/secrets/apns.p8` and its ids in `.env`.
  - `mochi.freethinkel.dev` (`8104`) is mochi's push relay, a Go binary in a
    distroless container: herdr hosts post agent lists, it sends APNs pushes.
    It moved from a Cloudflare Worker in October 2026. The mochi repo builds
    the binary and copies it to `/srv/pets/mochi-relay/mochi-relay`
    (`server/relay/README.md` there). `secrets/apns.p8` (key `JT9M8A4P4P`)
    and `state/devices.json` (the app's tokens) exist only there; the WAF
    rate-limiting rule "mochi relay" on the zone still guards the name.
  - `tg-digest-bot` is @chatovyzhimalka_bot, a Telegram bot that sums up and
    answers in group chats through `claude -p`. It only polls Telegram, so it
    has no port and no tunnel. Its repo deploys with `make deploy`; `.env`
    (with `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`) and
    `data/history.db` exist only there.
  - The Collie demo for App Store review is collie-server in `pets` as the
    user `collie-demo` (uid 1001, a systemd user unit, lingering), joined to
    the relay. It has no agent sources and every action off, so a caller
    only talks; it answers through that user's Claude Code login
    (`incus exec pets -- su - collie-demo`). Its ports (7980–7982, 7990)
    stay clear of the relay's.
- `kuzyo.local` works on the LAN through avahi.

Secrets live in `.env` next to this file, outside git; `.env.sample` lists
them and says how to make each one. `site.yml` reads `.env` directly, nothing
to source first.

The USB SSD (exFAT, label `files`, readable on a Mac too) is mounted at
`/srv/files`; only its `shared-files` folder is served by Samba on the host:
`smb://kuzyo.local/Kenos`, user
`freethinkel`, password `SAMBA_PASSWORD` in `.env`. Only the LAN and
Tailscale reach port 445. One address for home and away:
`smb://kenos.home.freethinkel.dev/Kenos`. At home it goes over the LAN;
away it goes over Tailscale, since kuzyo advertises `192.168.1.0/24` as a
subnet route (approved once in the Tailscale admin console).

Every night at 04:00 `kuzyo-backup` puts the Immich database,
`/srv/homeassistant`, `/srv/pets` and Kenos' `Finances` into a restic repo at `/srv/files/backup`, password
`RESTIC_PASSWORD` in `.env`. Photos are not backed up. To look inside:
`restic -r /srv/files/backup -p /root/.restic-password snapshots`.
That repo shares the USB disk with Kenos, so the script then copies it to
the R2 bucket `kuzyo-backup`, once `R2_ACCESS_KEY_ID` and
`R2_SECRET_ACCESS_KEY` are in `.env` (`.env.sample` says how).

Whatever breaks while nobody looks comes to the phone as a push through
Home Assistant (`kuzyo-alert`): a failed backup or monthly btrfs scrub of
`/srv`, a disk smartd does not like. A dead box sends nothing. Debian
security updates install themselves nightly on the host, `apps` and `pets`;
a full playbook run also pulls newer images for edge, HA and the stacks.
On battery, the box powers itself off at 10% and comes back with AC.

Compose stacks for the `apps` container live in `apps/<name>/`. Add each one
to `stacks` in `site.yml` with the port to publish on the host.

## Install without a USB stick

`preseed.cfg` drives an unattended Debian install from the netboot installer,
booted once by whatever systemd-boot is already on the disk.

1. BIOS (F1): Security → Virtualization → Intel VT-d on; Config → Power →
   Power On with AC Attach on.
2. Plug in Ethernet. The netboot initrd has no Wi-Fi firmware.
3. On the old system, put the netboot `linux` and `initrd.gz` of Debian 13
   into `/boot/kuzyo/`, plus the preseed as a second initrd, with the root
   password hash from `.env` filled in:
   ```sh
   sed "s|@ROOT_PASSWORD_HASH@|$ROOT_PASSWORD_HASH|" preseed.cfg > /tmp/preseed.cfg
   cd /tmp && echo preseed.cfg | bsdcpio -o --format newc | gzip > /boot/kuzyo/preseed.cpio.gz
   ```
4. `/boot/loader/entries/kuzyo-install.conf`:
   ```
   title   Debian 13 unattended install (WIPES DISK)
   linux   /kuzyo/linux
   initrd  /kuzyo/initrd.gz
   initrd  /kuzyo/preseed.cpio.gz
   options auto=true priority=critical
   ```
5. `bootctl set-oneshot kuzyo-install.conf && systemctl reboot`. Until the
   installer starts partitioning, a power cycle falls back to the old system.
6. Find the new IP by MAC `38:f3:ab:92:40:b3`, reserve it on the router, put
   it into `inventory.yml`.

Root logs in over SSH with the `id_ed25519` key. The root password is only
for the local console; `.env` keeps its hash.

## Running

```sh
ansible-playbook site.yml                 # everything
ansible-playbook site.yml --skip-tags upgrade
```

Reboot once after the first run so `consoleblank` takes effect.
