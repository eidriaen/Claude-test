/**
 * NPG Room Booker — Node.js proxy server
 * Fetches ICS calendars server-side (CORS workaround), parses RRULE,
 * exposes a JSON API to the front-end.
 *
 * Usage:
 *   node server.js            # production, fetches live ICS URLs
 *   node server.js --demo     # serves demo.ics for all rooms (no network required)
 *   PORT=8080 node server.js  # custom port (default 3000)
 */

'use strict';

const http  = require('http');
const https = require('https');
const fs    = require('fs');
const path  = require('path');

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const PORT      = parseInt(process.env.PORT || '3000', 10);
const DEMO_MODE = process.argv.includes('--demo');
const ROOT_DIR  = __dirname;

const roomsConfig = JSON.parse(fs.readFileSync(path.join(ROOT_DIR, 'rooms.json'), 'utf8'));
const CACHE_TTL   = (roomsConfig.cache_ttl_seconds || 60) * 1000; // ms

// In-memory cache: roomId -> { data: parsedEvents, fetchedAt: timestamp, raw: string }
const cache = {};

// ---------------------------------------------------------------------------
// Logging helpers
// ---------------------------------------------------------------------------
function log(msg) { process.stdout.write(`[${new Date().toISOString()}] ${msg}\n`); }
function err(msg) { process.stderr.write(`[${new Date().toISOString()}] ERROR: ${msg}\n`); }

// ---------------------------------------------------------------------------
// HTTP fetch helper (built-in, no axios/node-fetch)
// ---------------------------------------------------------------------------
function fetchUrl(url) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https') ? https : http;
    const req = mod.get(url, { timeout: 10000 }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        // simple one-level redirect
        fetchUrl(res.headers.location).then(resolve).catch(reject);
        return;
      }
      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode} for ${url}`));
        return;
      }
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error(`Timeout fetching ${url}`)); });
  });
}

// ---------------------------------------------------------------------------
// ICS / RRULE parser
// ---------------------------------------------------------------------------

/** Unfold RFC 5545 lines (continuation lines start with space/tab) */
function unfoldICS(raw) {
  return raw.replace(/\r\n[ \t]/g, '').replace(/\n[ \t]/g, '').replace(/\r/g, '');
}

/** Parse an ICS datetime string to a JS Date (UTC).
 *  Handles: 19700101T000000Z  (UTC)
 *            19700101T000000   (assumed Europe/Oslo — we convert via offset)
 *            19700101          (date-only, assumed midnight Oslo)
 */
function parseICSDate(str, tzid) {
  str = (str || '').trim();
  // UTC explicit
  if (str.endsWith('Z')) {
    const s = str.slice(0, -1);
    return new Date(Date.UTC(
      +s.slice(0,4), +s.slice(4,6)-1, +s.slice(6,8),
      +s.slice(9,11)||0, +s.slice(11,13)||0, +s.slice(13,15)||0
    ));
  }
  // Date-only (YYYYMMDD)
  if (str.length === 8) {
    return localToUTC(+str.slice(0,4), +str.slice(4,6)-1, +str.slice(6,8), 0, 0, 0);
  }
  // Local time with TZID (we treat anything non-UTC as Europe/Oslo)
  return localToUTC(
    +str.slice(0,4), +str.slice(4,6)-1, +str.slice(6,8),
    +str.slice(9,11)||0, +str.slice(11,13)||0, +str.slice(13,15)||0
  );
}

/** Convert Europe/Oslo local time to UTC Date.
 *  CET = UTC+1, CEST (summer) = UTC+2.
 *  Summer: last Sunday March 02:00 → last Sunday October 03:00.
 */
function localToUTC(y, mo, d, h, mi, s) {
  // Determine if date is in CEST (summer) or CET (winter)
  const lastSunMar = lastSundayOf(y, 2);  // month 2 = March (0-based)
  const lastSunOct = lastSundayOf(y, 9);  // month 9 = October

  // Build a UTC candidate with +1 (CET) and check against DST boundary
  const utcCET  = new Date(Date.UTC(y, mo, d, h - 1, mi, s));
  const utcCEST = new Date(Date.UTC(y, mo, d, h - 2, mi, s));

  // DST start: last Sunday of March at 02:00 local CET → 01:00 UTC
  const dstStart = new Date(Date.UTC(lastSunMar.y, lastSunMar.mo, lastSunMar.d, 1, 0, 0));
  // DST end: last Sunday of October at 03:00 local CEST → 01:00 UTC
  const dstEnd   = new Date(Date.UTC(lastSunOct.y, lastSunOct.mo, lastSunOct.d, 1, 0, 0));

  if (utcCEST >= dstStart && utcCEST < dstEnd) return utcCEST;
  return utcCET;
}

function lastSundayOf(year, month0) {
  // Find last day of month, walk back to Sunday
  const d = new Date(Date.UTC(year, month0 + 1, 0)); // last day
  d.setUTCDate(d.getUTCDate() - d.getUTCDay()); // subtract day-of-week (0=Sun)
  return { y: d.getUTCFullYear(), mo: d.getUTCMonth(), d: d.getUTCDate() };
}

/** Add duration (in ms) to a Date, returning new Date */
function addMs(date, ms) { return new Date(date.getTime() + ms); }

/** Expand an RRULE string into an array of {start, end} occurrences within [windowStart, windowEnd]. */
function expandRRULE(rule, dtstart, dtend, windowStart, windowEnd) {
  if (!rule) return [];
  const duration = dtend.getTime() - dtstart.getTime();
  const params = {};
  rule.replace(/^RRULE:/i, '').split(';').forEach((part) => {
    const [k, v] = part.split('=');
    params[k.toUpperCase()] = v;
  });

  const freq  = params.FREQ || 'DAILY';
  const count = params.COUNT ? parseInt(params.COUNT, 10) : Infinity;
  const until = params.UNTIL ? parseICSDate(params.UNTIL) : null;
  const interval = parseInt(params.INTERVAL || '1', 10);

  // BYDAY: e.g. MO,TU,WE  or -1SU
  const byday = params.BYDAY ? params.BYDAY.split(',') : null;
  // BYMONTHDAY, BYMONTH: for monthly/yearly (simplified)
  const bymonthday = params.BYMONTHDAY ? params.BYMONTHDAY.split(',').map(Number) : null;
  const bymonth    = params.BYMONTH    ? params.BYMONTH.split(',').map(Number)    : null;

  const DOW_MAP = { SU:0, MO:1, TU:2, WE:3, TH:4, FR:5, SA:6 };

  const results = [];
  let current = new Date(dtstart);
  let n = 0;

  // Safety limit: never generate more than 1000 occurrences per rule
  const MAX_OCC = 1000;

  while (n < count && n < MAX_OCC) {
    if (until && current > until) break;
    if (current > windowEnd) break;

    // For WEEKLY with BYDAY, emit all matching days in the current week
    if (freq === 'WEEKLY' && byday) {
      // Find the Monday of current's week
      const weekStart = new Date(current);
      weekStart.setUTCDate(weekStart.getUTCDate() - ((weekStart.getUTCDay() + 6) % 7)); // Mon
      weekStart.setUTCHours(current.getUTCHours(), current.getUTCMinutes(), current.getUTCSeconds(), 0);

      for (const day of byday) {
        const dow = DOW_MAP[day.replace(/[0-9-]/g, '')];
        if (dow === undefined) continue;
        const offset = (dow - 1 + 7) % 7; // days from Monday
        const occ = new Date(weekStart);
        occ.setUTCDate(occ.getUTCDate() + offset);
        if (occ >= dtstart && (!until || occ <= until) && occ <= windowEnd) {
          if (occ >= windowStart) {
            results.push({ start: occ, end: addMs(occ, duration) });
          }
          n++;
          if (n >= count || n >= MAX_OCC) break;
        }
      }
      // Advance by interval weeks
      current = new Date(current);
      current.setUTCDate(current.getUTCDate() + 7 * interval);
      continue;
    }

    // Check BYDAY for non-weekly (simplified: just weekday name match)
    if (byday) {
      const curDow = ['SU','MO','TU','WE','TH','FR','SA'][current.getUTCDay()];
      const matches = byday.some((bd) => bd.replace(/[0-9-]/g, '') === curDow);
      if (!matches) {
        current = advanceByFreq(current, freq, interval);
        continue;
      }
    }

    if (bymonth && !bymonth.includes(current.getUTCMonth() + 1)) {
      current = advanceByFreq(current, freq, interval);
      continue;
    }
    if (bymonthday && !bymonthday.includes(current.getUTCDate())) {
      current = advanceByFreq(current, freq, interval);
      continue;
    }

    if (current >= windowStart) {
      results.push({ start: new Date(current), end: addMs(current, duration) });
    }
    n++;
    current = advanceByFreq(current, freq, interval);
  }

  return results;
}

function advanceByFreq(date, freq, interval) {
  const d = new Date(date);
  switch (freq) {
    case 'DAILY':   d.setUTCDate(d.getUTCDate() + interval); break;
    case 'WEEKLY':  d.setUTCDate(d.getUTCDate() + 7 * interval); break;
    case 'MONTHLY': d.setUTCMonth(d.getUTCMonth() + interval); break;
    case 'YEARLY':  d.setUTCFullYear(d.getUTCFullYear() + interval); break;
  }
  return d;
}

/** Parse ICS text → array of {start: Date, end: Date} for today (Oslo) */
function parseICS(raw) {
  const text = unfoldICS(raw);
  const lines = text.split('\n');

  // Today's window in Oslo: midnight → 23:59:59
  const nowUTC = new Date();
  const osloStr = nowUTC.toLocaleDateString('sv-SE', { timeZone: 'Europe/Oslo' }); // YYYY-MM-DD
  const [oy, om, od] = osloStr.split('-').map(Number);
  const windowStart = localToUTC(oy, om-1, od, 0, 0, 0);
  const windowEnd   = localToUTC(oy, om-1, od, 23, 59, 59);

  const events = [];
  let inEvent = false;
  let current = {};

  for (const line of lines) {
    if (line === 'BEGIN:VEVENT') { inEvent = true; current = {}; continue; }
    if (line === 'END:VEVENT') {
      if (inEvent && current.dtstart) {
        // Single occurrence
        if (!current.rrule) {
          const s = current.dtstart;
          const e = current.dtend || addMs(s, 3600000);
          if (e > windowStart && s < windowEnd) {
            events.push({ start: s, end: e });
          }
        } else {
          // Recurring
          const occs = expandRRULE(
            current.rrule,
            current.dtstart,
            current.dtend || addMs(current.dtstart, 3600000),
            windowStart,
            windowEnd
          );
          events.push(...occs);
        }
      }
      inEvent = false; current = {};
      continue;
    }
    if (!inEvent) continue;

    // Parse key:value — handle DTSTART;TZID=...:value
    const colonIdx = line.indexOf(':');
    if (colonIdx < 0) continue;
    const keyPart = line.slice(0, colonIdx).toUpperCase();
    const val     = line.slice(colonIdx + 1).trim();

    // Extract TZID if present
    const tzid = (keyPart.match(/TZID=([^;:]+)/) || [])[1] || null;
    const key  = keyPart.split(';')[0];

    switch (key) {
      case 'DTSTART': current.dtstart = parseICSDate(val, tzid); break;
      case 'DTEND':   current.dtend   = parseICSDate(val, tzid); break;
      case 'RRULE':   current.rrule   = 'RRULE:' + val; break;
      case 'EXDATE':  /* ignored for simplicity */ break;
    }
  }

  return events.sort((a, b) => a.start - b.start);
}

// ---------------------------------------------------------------------------
// Cache + fetch
// ---------------------------------------------------------------------------
async function getEventsForRoom(room) {
  const cached = cache[room.id];
  if (cached && Date.now() - cached.fetchedAt < CACHE_TTL) {
    return cached.data;
  }

  let raw;
  if (DEMO_MODE) {
    raw = fs.readFileSync(path.join(ROOT_DIR, 'demo.ics'), 'utf8');
  } else {
    raw = await fetchUrl(room.ics_url);
  }
  const data = parseICS(raw);
  cache[room.id] = { data, fetchedAt: Date.now() };
  return data;
}

// ---------------------------------------------------------------------------
// Room status logic
// ---------------------------------------------------------------------------
function getRoomStatus(events) {
  const now = new Date();
  const LOOKAHEAD = 60 * 60 * 1000; // 1 hour

  // Find ongoing event
  const ongoing = events.find((e) => e.start <= now && e.end > now);
  if (ongoing) {
    return { status: 'busy', freeAt: ongoing.end };
  }

  // Find next event within 1 hour
  const upcoming = events.find((e) => e.start > now && e.start - now <= LOOKAHEAD);
  if (upcoming) {
    return { status: 'soon', busyAt: upcoming.start };
  }

  return { status: 'free' };
}

/** Format a Date as HH:MM in Europe/Oslo */
function fmtTime(date) {
  return date.toLocaleTimeString('no-NO', {
    timeZone: 'Europe/Oslo',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  });
}

// ---------------------------------------------------------------------------
// API: GET /api/rooms
// ---------------------------------------------------------------------------
async function handleApiRooms(req, res) {
  const results = await Promise.allSettled(
    roomsConfig.rooms.map(async (room) => {
      const events = await getEventsForRoom(room);
      const statusInfo = getRoomStatus(events);

      // Build today's schedule (start/end times, no titles)
      const now = new Date();
      const todaySchedule = events.map((e) => ({
        start: fmtTime(e.start),
        end:   fmtTime(e.end),
        past:  e.end <= now
      }));

      return {
        id:         room.id,
        name:       room.name,
        capacity:   room.capacity,
        status:     statusInfo.status,          // 'free' | 'busy' | 'soon'
        freeAt:     statusInfo.freeAt  ? fmtTime(statusInfo.freeAt)  : null,
        busyAt:     statusInfo.busyAt  ? fmtTime(statusInfo.busyAt)  : null,
        schedule:   todaySchedule,
        error:      null
      };
    })
  );

  const rooms = results.map((r, i) => {
    if (r.status === 'fulfilled') return r.value;
    err(`Room ${roomsConfig.rooms[i].id}: ${r.reason}`);
    return {
      id:       roomsConfig.rooms[i].id,
      name:     roomsConfig.rooms[i].name,
      capacity: roomsConfig.rooms[i].capacity,
      status:   'error',
      freeAt:   null,
      busyAt:   null,
      schedule: [],
      error:    'Kunne ikke hente kalender'
    };
  });

  // Sort: free first, then soon, then busy/error — within status sort by freeAt or busyAt
  const ORDER = { free: 0, soon: 1, busy: 2, error: 3 };
  rooms.sort((a, b) => {
    const od = ORDER[a.status] - ORDER[b.status];
    if (od !== 0) return od;
    // Both busy: soonest-to-free first
    if (a.status === 'busy' && a.freeAt && b.freeAt) return a.freeAt.localeCompare(b.freeAt);
    return 0;
  });

  const now = new Date();
  const payload = {
    demo:      DEMO_MODE,
    updatedAt: fmtTime(now),
    rooms
  };

  const body = JSON.stringify(payload);
  res.writeHead(200, {
    'Content-Type':  'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'Access-Control-Allow-Origin': '*'
  });
  res.end(body);
}

// ---------------------------------------------------------------------------
// API: POST /api/book  (optional Power Automate webhook)
// ---------------------------------------------------------------------------
async function handleApiBook(req, res) {
  const webhookUrl = roomsConfig.booking_webhook_url;
  if (!webhookUrl) {
    res.writeHead(503, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: false, error: 'Booking ikke konfigurert' }));
    return;
  }

  let body = '';
  for await (const chunk of req) body += chunk;

  try {
    const payload = JSON.parse(body);
    const postBody = JSON.stringify(payload);
    const url = new URL(webhookUrl);
    const mod  = url.protocol === 'https:' ? https : http;

    const result = await new Promise((resolve, reject) => {
      const preq = mod.request(webhookUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(postBody) }
      }, (pres) => {
        let d = '';
        pres.on('data', (c) => d += c);
        pres.on('end', () => resolve({ status: pres.statusCode, body: d }));
      });
      preq.on('error', reject);
      preq.write(postBody);
      preq.end();
    });

    res.writeHead(result.status < 400 ? 200 : 502, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: result.status < 400 }));
  } catch (e) {
    res.writeHead(400, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: false, error: e.message }));
  }
}

// ---------------------------------------------------------------------------
// Static file server
// ---------------------------------------------------------------------------
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'application/javascript',
  '.json': 'application/json',
  '.css':  'text/css',
  '.ics':  'text/calendar',
  '.ico':  'image/x-icon'
};

function serveStatic(req, res) {
  let urlPath = req.url.split('?')[0];
  if (urlPath === '/') urlPath = '/index.html';
  const filePath = path.join(ROOT_DIR, urlPath);
  // Prevent directory traversal
  if (!filePath.startsWith(ROOT_DIR)) {
    res.writeHead(403); res.end('Forbidden'); return;
  }
  fs.readFile(filePath, (e, data) => {
    if (e) { res.writeHead(404); res.end('Not Found'); return; }
    const ext  = path.extname(filePath).toLowerCase();
    const mime = MIME[ext] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': mime });
    res.end(data);
  });
}

// ---------------------------------------------------------------------------
// Main router
// ---------------------------------------------------------------------------
const server = http.createServer(async (req, res) => {
  const url = req.url.split('?')[0];
  try {
    if (url === '/api/rooms' && req.method === 'GET') {
      await handleApiRooms(req, res);
    } else if (url === '/api/book' && req.method === 'POST') {
      await handleApiBook(req, res);
    } else if (req.method === 'GET') {
      serveStatic(req, res);
    } else {
      res.writeHead(405); res.end('Method Not Allowed');
    }
  } catch (e) {
    err(e.stack || e.message);
    if (!res.headersSent) { res.writeHead(500); res.end('Internal Server Error'); }
  }
});

server.listen(PORT, () => {
  log(`NPG Room Booker running on http://localhost:${PORT}${DEMO_MODE ? ' [DEMO MODE]' : ''}`);
});
