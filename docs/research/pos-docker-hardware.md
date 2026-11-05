# Docker-based Deployment and Hardware Access for a Local POS

Research date: 2026-11-05. Repo: `eixnpos`. Companion to `pos-stack-comparison.md`.

## 1. Can a Docker container access host USB drives and USB thermal printers?

**Linux host (native Docker Engine): yes, with caveats.**

- By default containers are unprivileged and cannot access any devices. [Docker run docs](https://docs.docker.com/engine/containers/run/#runtime-privilege-and-linux-capabilities)
- Grant access to specific devices with `--device=/dev/...` (read/write/mknod by default; restrictable with `:rwm`). Example: `--device=/dev/ttyUSB0`. [Docker run docs](https://docs.docker.com/engine/containers/run/#runtime-privilege-and-linux-capabilities)
- Or grant all devices with `--privileged` (reconfigures AppArmor/SELinux; use with caution). [Docker run docs](https://docs.docker.com/engine/containers/run/#runtime-privilege-and-linux-capabilities)
- For devices plugged in *after* container start, a bind mount of `/dev` is needed (`-v /dev:/dev`) plus `--privileged`, because `--device` is resolved at container creation. (Community/balena forums; not an official Docker guarantee.) [balena forums](https://forums.balena.io/t/docker-container-cannot-access-dynamically-plugged-usb-devices/4277)
- USB backup drives: mount the host directory (bind mount) e.g. `-v /media/usb:/backup` — no device access needed if the host already mounted the filesystem. [Docker run docs (bind mounts)](https://docs.docker.com/engine/containers/run/#bind-mounts)

**Docker Desktop on Windows/macOS: historically not possible; now possible only via USB/IP (Docker Desktop ≥ 4.35.0) and only for a limited set of devices.**

- Docker Desktop runs containers in a Linux VM, so `/dev` passthrough is unavailable; USB devices must be shared into the VM via USB/IP. [Docker Desktop USB/IP docs](https://docs.docker.com/desktop/features/usbip)
- Setup requires running a privileged container with `--pid=host`, `nsenter -t 1 -m`, then `usbip attach` to `host.docker.internal`; the initial container must stay running to keep the device attached. [Docker Desktop USB/IP docs](https://docs.docker.com/desktop/features/usbip)
- "Docker Desktop includes built-in drivers for many common USB devices but Docker can't guarantee every possible USB device works." [Docker Desktop USB/IP docs](https://docs.docker.com/desktop/features/usbip)
- Prior to 4.35.0, the official stance was: "Unfortunately, it is not possible to pass through a USB device (or a serial port) to a container as it requires support at the hypervisor level." [Docker Community Forums](https://forums.docker.com/t/how-to-use-a-host-usb-device-in-a-container-in-docker-desktop/138905)

**udev / permissions:** When using `--device`, the container sees the device node but may still need the right group (e.g. `dialout` for serial printers) and `--group-add`. Dynamic plug/unplug and udev rules on the host are not propagated into the container automatically.

## 2. Is Docker practical for a local POS machine's hardware I/O?

**No — not as the primary hardware path.** Docker adds a fragile layer between the app and the USB devices that a non-technical operator must keep working:

- On Linux, it works but requires `--privileged`/`--device` flags, host udev, and handling of hot-plug.
- On Windows/macOS (Docker Desktop), USB access is experimental and device-dependent.
- A single POS machine benefits from direct host access. The pragmatic options:
  - **Network (LAN) printer** — Epson ePOS-Print is XML-over-HTTP to the printer's built-in server, works from any client including from inside a container. [Epson ePOS SDK blog](https://finalpos.com/blog/epson-epos-sdk-custom-pos)
  - **Native helper** — a small host-side process (Python with `python-escpos`/`pyusb`) that the containerized app calls over localhost, or run the whole app natively.
  - Or skip Docker and run the app as a native service on the POS machine.

## 3. Effect on TypeScript (browser client + Dockerized API) vs Django-in-Docker

Dockerizing does **not** resolve the browser hardware constraints; it actually makes them worse:

- A browser client still cannot reach USB printers/storage directly (WebUSB = Chromium only, requires drivers/secure context). [MDN WebUSB](https://developer.mozilla.org/en-US/docs/Web/API/WebUSB_API)
- If the API is in a container, USB access must happen either in the container (Linux-only, privileged) or via a native helper on the host.
- **Django-in-Docker** is the better of the two Dockerized options: Python libraries (`python-escpos`, `pyusb`) run inside the Linux container and can talk to `--device`-passed USB printers/backup drives, and Django still serves the web UI. The container must be Linux-hosted (or use Docker Desktop USB/IP) for this to work.
- **TS (browser + Dockerized API)**: the browser still can't print to USB directly, and the Node API container would need the same privileged Linux setup to do ESC/POS printing — gaining nothing over Django while losing Django's built-in auth/admin/ORM. So Docker does not flip the recommendation; if anything it reinforces Django, provided the host is Linux.

## 4. Packaging comparison for a non-technical POS operator

| Packaging | Pros | Cons | Verdict |
|---|---|---|---|
| **Docker image** | Reproducible, isolated, easy updates via `docker compose pull` | Requires Docker installed, CLI knowledge, privileged flags for USB, fragile on Windows/macOS for hardware | Poor fit for non-technical operator with USB hardware |
| **PyInstaller executable** | Single `.exe`/binary, no Python install, autostart-friendly, direct USB/serial access | Larger binary, antivirus false positives possible, slower startup | Best for non-technical operator on Windows/Linux |
| **Source + venv** | Transparent, easy to patch | Requires Python install, dependency management, CLI | Only for technical staff |

Recommendation: **PyInstaller (or a similar single-binary packager) for the Django app**, running as a native OS service with autostart, rather than Docker — because the POS machine's USB hardware is the bottleneck and Docker adds operational complexity without benefit. Keep Docker for development/CI only. If the product later becomes a SaaS POS with network printers only, Docker becomes viable and TS/Next.js is competitive.

## Bottom line

Docker does not make hardware access easier for a POS; it makes it harder on non-Linux hosts and adds operator burden. The prior recommendation (Python/Django, native service, USB drives/printers via Python, or LAN printer) stands. Prefer a single native executable (PyInstaller) for the non-technical operator; reserve Docker for dev/CI.