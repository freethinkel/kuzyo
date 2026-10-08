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
- `kuzyo.local` works on the LAN through avahi.

Secrets live in `.env` next to this file, outside git; `.env.sample` lists
them and says how to make each one. `site.yml` reads `.env` directly, nothing
to source first.

The USB SSD (exFAT, label `files`, readable on a Mac too) is mounted at
`/srv/files` and shared by Samba on the host: `smb://kuzyo.local/ssd`, user
`freethinkel`, password `SAMBA_PASSWORD` in `.env`. Only the LAN and
Tailscale reach port 445.

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
