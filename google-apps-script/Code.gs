/**
 * Calendar bridge for the AI Voice Appointment Agent.
 * Runs inside YOUR Google account (no Google Cloud project, no API keys).
 *
 * Deploy: Deploy > New deployment > Web app
 *   Execute as: Me   |   Who has access: Anyone
 * Copy the /exec URL into the backend env var APPS_SCRIPT_URL.
 * Optional: Services (+) > Google Calendar API  -> enables Google Meet links.
 *
 * POST JSON:
 *   {"action":"busy",   "start":"ISO", "end":"ISO"}          -> {"busy":[{"start","end"}]}
 *   {"action":"create", "event":{...Calendar API event...},
 *                        "sendInvites":true}                  -> {"id","htmlLink","hangoutLink"}
 */
const CALENDAR_ID = 'primary';

function doPost(e) {
  try {
    const req = JSON.parse(e.postData.contents);
    if (req.action === 'busy') return json_(busy_(req.start, req.end));
    if (req.action === 'create') return json_(create_(req.event, req.sendInvites));
    return json_({ error: 'unknown action' });
  } catch (err) {
    return json_({ error: String(err) });
  }
}

function doGet() {
  return json_({ ok: true, calendar: CalendarApp.getDefaultCalendar().getName() });
}

function busy_(start, end) {
  const cal = CalendarApp.getDefaultCalendar();
  const events = cal.getEvents(new Date(start), new Date(end));
  const busy = events
    .filter(ev => !ev.isAllDayEvent())
    .filter(ev => ev.getMyStatus() !== CalendarApp.GuestStatus.NO)
    .map(ev => ({ start: ev.getStartTime().toISOString(), end: ev.getEndTime().toISOString() }));
  return { busy: busy };
}

function create_(event, sendInvites) {
  // Preferred: Advanced Calendar service (supports Google Meet links + invite emails)
  try {
    if (typeof Calendar !== 'undefined') {
      const created = Calendar.Events.insert(event, CALENDAR_ID, {
        conferenceDataVersion: 1,
        sendUpdates: sendInvites ? 'all' : 'none',
      });
      return { id: created.id, htmlLink: created.htmlLink, hangoutLink: created.hangoutLink || null };
    }
  } catch (err) {
    console.warn('Advanced service failed, falling back: ' + err);
  }
  // Fallback: CalendarApp (no Meet link)
  const cal = CalendarApp.getDefaultCalendar();
  const guests = (event.attendees || []).map(a => a.email).join(',');
  const ev = cal.createEvent(event.summary, new Date(event.start.dateTime), new Date(event.end.dateTime), {
    description: event.description || '',
    guests: guests,
    sendInvites: !!sendInvites,
  });
  return { id: ev.getId(), htmlLink: null, hangoutLink: null };
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

/** Run once from the editor to grant calendar permission. */
function authorize() {
  console.log(JSON.stringify(busy_(new Date().toISOString(), new Date(Date.now() + 864e5).toISOString())));
}
