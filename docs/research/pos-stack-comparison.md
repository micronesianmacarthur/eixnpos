# POS Technology Stack Comparison: Python & Django vs Full-Stack TypeScript

Research date: 2026-11-05. Repo: `eixnpos`.

## Summary / Recommendation

**Recommendation: Python & Django (server-rendered + lightweight JS) for this POS**, with the browser used as a thin client and a small native helper (or networked printer) handling hardware. Rationale at the end.

The decisive constraints for a *local* POS are hardware access (USB backup, thermal printers) and offline resilience. A pure browser app cannot reliably access arbitrary USB devices across browsers; Django running as a local service keeps hardware access in Python (where `pyusb`/`python-escpos` and the OS filesystem work) while still delivering a web UI.

## 1. Feature-by-feature feasibility

| Feature | Django (Python) | Full-stack TS (Next.js) | Notes |
|---|---|---|---|
| User management | Built-in `django.contrib.auth`: users, groups, permissions, password hashing, sessions. [Django auth docs](https://docs.djangoproject.com/en/6.1/topics/auth/) | Must assemble (NextAuth/Auth.js, Prisma, custom). No built-in. | Django ships auth out of the box. |
| Inventory management | Django ORM models + admin; straightforward. [Django ORM](https://docs.djangoproject.com/en/6.1/topics/db/models/) | Prisma/Drizzle + custom UI. | Both fine; Django faster to scaffold. |
| Sale cart | Server-side session/cart or client state. | Client state (React) + API routes. | TS client cart is smoother UX. |
| Sales management | ORM + reporting queries. | Same via Prisma. | Tie. |
| Customer management | Model + admin. | Custom. | Django admin accelerates. |
| Supplier management | Model + admin. | Custom. | Django admin accelerates. |
| DB backup to USB | Python can write to OS-mounted USB drives directly. | Browser cannot write to arbitrary USB storage; needs File System Access API (Chromium) or native helper. [MDN WebUSB](https://developer.mozilla.org/en-US/docs/Web/API/WebUSB_API) | Django advantage. |
| Small & letter receipt display | HTML/CSS templates; print via browser or ESC/POS. | Same, React components. | Tie. |
| Z-report | Django ORM aggregation + template/PDF. | Same. | Tie. |

## 2. DB backup to USB (hardware access constraints in browser)

- Browsers cannot freely access USB storage. The WebUSB API is **experimental and limited availability** (Chromium only); Safari/Firefox do not support it. [MDN WebUSB](https://developer.mozilla.org/en-US/docs/Web/API/WebUSB_API)
- WebUSB requires user gesture, HTTPS/secure context, and platform-specific drivers (WinUSB on Windows, udev rules on Linux). [Chrome WebUSB guide](https://developer.chrome.com/docs/capabilities/usb)
- Chrome's policy explicitly says: for external storage use the **File System Access API**, not WebUSB. [IWA policy](https://developer.chrome.com/docs/iwa/developer-policy)
- Therefore a full browser/TS stack must either (a) run as an Isolated Web App with unrestricted APIs, or (b) ship a native companion. Django, running as a local service, writes backups to the OS-mounted USB drive directly with no browser permission flow.

## 3. Receipt printing (ESC/POS, browser print, thermal printers)

- ESC/POS is the de-facto command language for thermal receipt printers. [Epson ESC/POS reference](https://reference.epson-biz.com/modules/ref_escpos/index.php)
- Browser-based ESC/POS printing over USB requires WebUSB → Chromium only; Safari cannot talk to USB/Bluetooth printers. [CloudBridal help](https://help.cloudbridal.com/en/article/printing-receipts-with-a-thermal-printer-7t79xf/), [Elysano](https://www.elysano.com/how-it-works)
- On Windows, WebUSB to a printer-class device needs a WinUSB driver via Zadig. [CloudBridal](https://help.cloudbridal.com/en/article/printing-receipts-with-a-thermal-printer-7t79xf/)
- Network (LAN) printers avoid this: Epson ePOS-Print is XML-over-HTTP to the printer's built-in server, works from any browser including Safari. [Epson ePOS SDK blog](https://finalpos.com/blog/epson-epos-sdk-custom-pos)
- Python side: `python-escpos` can drive USB/serial/network printers directly from the server. (Library, not official docs; cited as ecosystem.)

Conclusion: Django can print via USB/serial/network without browser limitations; a TS-only browser app is constrained to Chromium + WebUSB or LAN printers.

## 4. Z-report / end-of-day reporting

- Both stacks can aggregate. Django ORM aggregation + Django templates/PDF generation; TS via Prisma + React PDF. No primary-source advantage either way.
- Django's built-in admin + ORM reduces boilerplate for reporting endpoints.

## 5. Offline capability considerations for POS

- POS must keep selling during network outages. A browser PWA with a service worker can cache assets and queue sales; IndexedDB for local data. [MDN Service Workers](https://developer.mozilla.org/en-US/docs/Web/API/Service_Worker_API)
- Django as a local service (localhost) is inherently offline-capable for the LAN; no internet needed. Data lives in local SQLite/Postgres.
- Recommendation: run Django locally on the POS machine (SQLite or local Postgres) so the app works fully offline; sync/backup to USB.

## 6. Deployment/packaging for a local POS machine

- Django: `python manage.py runserver` is dev-only; production via WSGI (gunicorn) behind a reverse proxy, or packaged as a desktop app with PyInstaller/Tauri. [Django deployment checklist](https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/)
- TS/Next: `next build` + `next start` (Node server) or static export; package with Electron/Tauri for desktop. [Next.js deployment](https://nextjs.org/docs/app/getting-started/deploying)
- For a local POS machine, both need a process manager / autostart. Django has fewer moving parts (single Python process + DB).

## 7. Recommendation with rationale

Choose **Python & Django** because:
1. Built-in auth, admin, ORM cover user/inventory/customer/supplier with less code. [Django auth](https://docs.djangoproject.com/en/6.1/topics/auth/)
2. Server-side process can access USB drives and printers directly, avoiding browser hardware constraints. [MDN WebUSB limited availability](https://developer.mozilla.org/en-US/docs/Web/API/WebUSB_API)
3. Runs locally/offline by default.
4. Simpler deployment on a single POS machine.

Trade-off: client-side cart UX and type safety are better in TS; mitigate by adding HTMX/Alpine or a small React frontend consuming Django REST endpoints, while keeping hardware/DB logic in Django.

If the product must be a pure web SaaS POS (no local install, network printers only), full-stack TypeScript/Next.js is viable and gives better frontend DX — but accept Chromium-only USB printing or require LAN printers.
