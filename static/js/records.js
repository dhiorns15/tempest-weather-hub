// Records and "on this day" cards on the History page, from /site/records.
// Days are the station's local calendar days and times are shown in the
// station's timezone (CAPABILITIES.timezone), whatever the viewer's own
// clock says. Values use the shared unit helpers in charts.js.
//
// history.js calls loadRecords() once capabilities have loaded.

const RECORD_ROWS = [
  { key: "temp_high", label: "Highest temperature", quantity: "temp" },
  { key: "temp_low", label: "Lowest temperature", quantity: "temp" },
  { key: "gust", label: "Peak gust", quantity: "wind" },
  { key: "wettest_day", label: "Wettest day", quantity: "precip", dateOnly: true },
  { key: "pressure_high", label: "Highest station pressure", quantity: "pressure" },
  { key: "pressure_low", label: "Lowest station pressure", quantity: "pressure" },
];
const RECORD_SCOPES = ["all_time", "this_year", "this_month"];

function recordValueText(value, quantity) {
  let text = formatReading(value, quantity);
  if (/^-0(\.0*)?$/.test(text)) text = text.slice(1); // no "-0°"
  return quantity === "temp" ? `${text}${UNITS.temp}` : `${text} ${UNITS[quantity]}`;
}

function stationTimeZone() {
  const zone = CAPABILITIES.timezone;
  try {
    new Intl.DateTimeFormat([], { timeZone: zone });
    return zone;
  } catch (err) {
    return undefined; // unknown to this browser - fall back to its own zone
  }
}

// "2026-08-02" -> "Aug 2" (or "Aug 2, 2025" outside `currentYear`).
function recordDateText(isoDate, currentYear) {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString([], {
    month: "short",
    day: "numeric",
    year: y === currentYear ? undefined : "numeric",
    timeZone: "UTC",
  });
}

function recordTimeText(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    timeZone: stationTimeZone(),
  });
}

function recordsEl(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text != null) el.textContent = text;
  return el;
}

function recordCell(scope, row, currentYear) {
  const td = document.createElement("td");
  const record = scope[row.key];
  if (record) {
    td.appendChild(recordsEl("span", "rec-value", recordValueText(record.value, row.quantity)));
    const when = recordsEl("span", "rec-when", recordDateText(record.date, currentYear));
    if (!row.dateOnly && record.ts != null) {
      when.appendChild(recordsEl("span", "rec-time", recordTimeText(record.ts)));
    }
    td.appendChild(when);
    return td;
  }
  td.appendChild(recordsEl("span", "rec-value rec-empty", "—"));
  let why = "No readings";
  if (row.key === "wettest_day") {
    if (scope.rain_reported) why = "No rain recorded";
    else if (!CAPABILITIES.cloud_configured) why = "Rain needs the cloud source";
    else why = "No rain data";
  }
  td.appendChild(recordsEl("span", "rec-when", why));
  return td;
}

function renderRecordsTable(host, data) {
  host.textContent = "";
  const currentYear = Number(data.today.slice(0, 4));
  const scopes = data.records;

  const table = recordsEl("table", "records-table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  headRow.appendChild(recordsEl("td"));
  RECORD_SCOPES.forEach((key) => {
    const th = recordsEl("th", null, scopes[key].label);
    th.scope = "col";
    headRow.appendChild(th);
  });
  head.appendChild(headRow);
  table.appendChild(head);

  const body = document.createElement("tbody");
  RECORD_ROWS.forEach((row) => {
    const tr = document.createElement("tr");
    const th = recordsEl("th", null, row.label);
    th.scope = "row";
    tr.appendChild(th);
    RECORD_SCOPES.forEach((key) => {
      const td = recordCell(scopes[key], row, currentYear);
      td.dataset.label = scopes[key].label;
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
  table.appendChild(body);
  host.appendChild(table);
}

const COMPARISON_COLUMNS = [
  { key: "temp_high", label: "High", quantity: "temp" },
  { key: "temp_low", label: "Low", quantity: "temp" },
  { key: "gust", label: "Gust", quantity: "wind" },
  { key: "rain", label: "Rain", quantity: "precip" },
];

function comparisonValueText(value, quantity) {
  if (value == null) return "—";
  let text = formatReading(value, quantity);
  if (/^-0(\.0*)?$/.test(text)) text = text.slice(1);
  return quantity === "temp" ? `${text}°` : text;
}

function renderOnThisDay(host, data) {
  host.textContent = "";
  const currentYear = Number(data.today.slice(0, 4));
  const { today, comparisons } = data.on_this_day;

  // Rain only when there's any to show, or the cloud source could supply it.
  const days = [today, ...comparisons].filter((day) => day.available);
  const showRain = CAPABILITIES.cloud_configured || days.some((day) => day.rain != null);
  const columns = COMPARISON_COLUMNS.filter((c) => c.key !== "rain" || showRain);

  const table = recordsEl("table", "otd-table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  headRow.appendChild(recordsEl("td"));
  columns.forEach((c) => {
    const th = recordsEl("th", null, c.label);
    th.scope = "col";
    th.appendChild(recordsEl("span", "otd-unit", c.quantity === "temp" ? UNITS.temp : UNITS[c.quantity]));
    headRow.appendChild(th);
  });
  head.appendChild(headRow);
  table.appendChild(head);

  const body = document.createElement("tbody");
  const addRow = (day, label, note, className) => {
    const tr = recordsEl("tr", className);
    const th = recordsEl("th", null, label);
    th.scope = "row";
    th.appendChild(recordsEl("span", "otd-date", note));
    tr.appendChild(th);
    if (day.available) {
      columns.forEach((c) => tr.appendChild(recordsEl("td", null, comparisonValueText(day[c.key], c.quantity))));
    } else {
      const reasons = {
        before_history: `Before history began (${recordDateText(data.first_day, currentYear)})`,
        today: "No readings yet today",
        no_data: "No readings that day",
      };
      const td = recordsEl("td", "otd-missing", reasons[day.reason] || reasons.no_data);
      td.colSpan = columns.length;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  };

  const todayNote = today.available
    ? `${recordDateText(today.date, currentYear)} · to ${recordTimeText(today.last_ts)}`
    : recordDateText(today.date, currentYear);
  addRow(today.available ? today : { available: false, reason: "today" }, "Today so far", todayNote, "otd-today");
  comparisons.forEach((day) => {
    let note = recordDateText(day.date, currentYear);
    if (day.available && !day.complete) note += " · partial day";
    addRow(day, day.label, note);
  });
  table.appendChild(body);
  host.appendChild(table);
}

function renderRecordsNote(data) {
  const note = document.getElementById("records-note");
  if (!note) return;
  if (!data.first_day) {
    note.textContent = "";
    return;
  }
  const currentYear = Number(data.today.slice(0, 4));
  const days = data.days_with_data;
  note.textContent =
    `Since ${recordDateText(data.first_day, currentYear)} · ${days.toLocaleString()} ` +
    `${days === 1 ? "day" : "days"} with readings · station days (${data.timezone})`;
}

function renderRecordsEmpty(message) {
  ["records-body", "on-this-day-body"].forEach((id) => {
    const host = document.getElementById(id);
    if (!host) return;
    host.textContent = "";
    host.appendChild(recordsEl("p", "records-message", message));
  });
}

async function loadRecords() {
  try {
    const response = await fetch("/site/records");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);

    renderRecordsNote(data);
    if (!data.first_day) {
      renderRecordsEmpty("No readings stored yet. Records start with the station's first report.");
      return;
    }
    renderRecordsTable(document.getElementById("records-body"), data);
    renderOnThisDay(document.getElementById("on-this-day-body"), data);

    const footnote = document.getElementById("on-this-day-note");
    if (footnote) {
      const missing = data.on_this_day.comparisons.filter((c) => !c.available).length;
      footnote.textContent =
        data.first_day === data.today
          ? "History began today. Yesterday, last week, last month and last year fill in as the days pass."
          : missing
            ? "Rows fill in by themselves as history builds up."
            : "";
    }
  } catch (err) {
    renderRecordsEmpty(`Unable to load records (${err.message || "network error"}).`);
  }
}
