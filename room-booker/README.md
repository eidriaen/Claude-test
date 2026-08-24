# NPG Room Booker

Touch-screen room availability display for Outlook calendar rooms.

## Features

- Shows **real-time availability** for all configured meeting rooms
- Fetches ICS feeds server-side (avoids CORS issues from browser)
- Expands recurring events (RRULE: daily, weekly, monthly, yearly)
- Shows today's schedule per room — no event titles/organizer names (privacy)
- Sorts rooms: **free first**, then soon-to-be-busy, then occupied
- Auto-refreshes every 60 seconds
- **Kiosk mode** (`?kiosk=1`) for touch-screen deployment
- **Demo mode** (`--demo` flag) for testing without network
- Optional room booking via Power Automate webhook

## Setup

### 1. Prerequisites

- Node.js 16+ (no npm packages required — uses only built-in modules)

### 2. Configure rooms

Edit `rooms.json` and replace the placeholder ICS URLs with real ones:

```json
{
  "rooms": [
    {
      "id": "room-a",
      "name": "Møterom A",
      "capacity": 8,
      "ics_url": "https://outlook.office365.com/owa/calendar/TOKEN/reachcalendar.ics"
    }
  ]
}
```

#### How to get ICS URLs from Outlook

ICS calendar sharing must be enabled by an admin in Microsoft 365:

1. In **Outlook Web** (outlook.office.com), go to **Settings → Calendar → Shared calendars**
2. Under "Publish a calendar", select the room calendar and permission level **"Can view all details"**
3. Copy the **ICS link** (not the HTML link)
4. The email `interactive@npg.no` must have access to view each room's calendar

Alternatively: ask your IT admin to create sharing links for each room resource via the Exchange Admin Center.

### 3. Start the server

```bash
# Production
node server.js

# Custom port
PORT=8080 node server.js

# Demo mode (no network, serves demo.ics for all rooms)
node server.js --demo
```

The server starts on **http://localhost:3000** by default.

### 4. Open in browser

- Normal: `http://localhost:3000`
- Kiosk (touch-screen): `http://localhost:3000?kiosk=1`

## Kiosk Deployment (IoT touch screen)

1. Install Node.js on the touch-screen device
2. Copy the `room-booker/` folder to the device
3. Set the device to auto-start `node server.js` on boot (use `systemd`, PM2, or a startup script)
4. Open a browser in full-screen kiosk mode pointing to `http://localhost:3000?kiosk=1`

### Example: systemd service

Create `/etc/systemd/system/room-booker.service`:

```ini
[Unit]
Description=NPG Room Booker
After=network.target

[Service]
WorkingDirectory=/opt/room-booker
ExecStart=/usr/bin/node server.js
Restart=on-failure
Environment=PORT=3000

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable room-booker
sudo systemctl start room-booker
```

## Optional: Room Booking

To enable a "Reserver rom" button, set `booking_webhook_url` in `rooms.json` to a Power Automate HTTP trigger URL. The server will POST:

```json
{
  "roomId": "room-a",
  "roomName": "Møterom A",
  "requestedAt": "2026-08-24T09:15:00.000Z"
}
```

Leave `booking_webhook_url` as `""` to hide the booking button.

## File Overview

| File         | Purpose |
|--------------|---------|
| `server.js`  | Node.js proxy server — fetches ICS, parses RRULE, serves JSON API + static files |
| `index.html` | Front-end — room cards, auto-refresh, kiosk mode |
| `rooms.json` | Room configuration — ICS URLs, capacities, optional booking webhook |
| `demo.ics`   | Sample ICS fixture used in `--demo` mode |
| `README.md`  | This file |
