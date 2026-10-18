-- The only table of the temporary visit counter. No IP address, no user agent, no identifier.
CREATE TABLE IF NOT EXISTS events (
  day      TEXT    NOT NULL,   -- UTC date, YYYY-MM-DD
  hour     INTEGER NOT NULL,   -- UTC hour, 0-23
  kind     TEXT    NOT NULL,   -- view | time | click | act
  section  TEXT    NOT NULL,   -- web | addon | check
  path     TEXT    NOT NULL,   -- page, without query string or fragment
  ref      TEXT    NOT NULL,   -- host the visitor came from, or direct / internal (views only)
  country  TEXT    NOT NULL,   -- two letters from the connection, XX if unknown
  value    INTEGER NOT NULL,   -- seconds on the page (time only)
  action   TEXT    NOT NULL    -- store or button name (click and act only)
);
CREATE INDEX IF NOT EXISTS events_day ON events (day);
