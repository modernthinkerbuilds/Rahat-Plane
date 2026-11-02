# ADR-019 — Sites: one Rahat, run from more than one place

**Status:** Accepted, 2026-11-02 (Architect A; owner's ask of Nov 1)
**Code:** [`core/site.py`](../core/site.py), [`scripts/rahat_site.py`](../scripts/rahat_site.py)

## Context

The Mac mini that runs Rahat travels: weeks at a time in another country,
on another network, in another time zone. Until now every place it could
run from was spelled in code or in launchd:

- the athlete's clock in Kipchoge (`HOME_ZONE`), home in Bourdain's profile;
- nine launchd jobs, five of which read one region's event feeds, food
  feeds and gym, whether or not anyone is there, and spend money doing it;
- a health line that would say, every morning abroad, that those feeds
  were stale;
- the phone's health sync pointed at the Mac's address on one Wi-Fi.

Moving meant editing all of it, and editing it back.

## Decision

A **site** is one place the Mac runs from, described in one private file:
`vault/sites/<name>.json` (vault/ is never in git). It names the zone, the
home point (an address, geocoded once), the launchd jobs that are off
there, whether Genie is on, and environment overrides. One site is active
at a time (`vault/sites/active.json`). Every process reads the active site
once at start; **one command** moves between sites:

```
.venv/bin/python scripts/rahat_site.py switch hyderabad
.venv/bin/python scripts/rahat_site.py switch home
```

The switch geocodes the home address if the point is empty, writes the
active marker, unloads the jobs the site turns off, loads back what an
earlier switch stopped, restarts the always-on bots so they read the new
site, and says whether the Mac's clock matches the site's zone. Jobs it
never named (a private overlay agent's, ADR on `core/overlay.py`) are left
alone. `plan` prints the same without doing it; `playbook` prints the
landing checklist with live values; `status` shows where Rahat is.

What the site decides, and who reads it:

| Setting | Read by |
|---|---|
| `timezone` | Kipchoge's athlete clock; Bourdain's home clock; the health line, which names a Mac clock on another zone (the scheduled jobs run on the Mac's clock, which macOS sets by location) |
| `home` (address → lat/lng, city, region, travel mode) | Bourdain's "near home" |
| `services.off` | the switch (launchd); the health line skips the checks those jobs would fail; the event and food runs refuse to run there |
| `agents.genie` | Miya's roster at boot, and what Miya says on Genie's rungs |
| `env` | every process, at start, on top of `.env` (budget caps, flags) |

With no site files, or under `RAHAT_TEST_MODE`, Rahat is at **home**: the
built-in default, which is exactly how it ran before this ADR. Nothing
changes for a clone with no site files, and the test suite stays hermetic.

The phone reaches the health bridge over **Tailscale**: one address for the
Mac on every network, set once in Health Auto Export, working from cellular
too. The bridge's `/health` names the active site, so the phone's browser
shows which Rahat it reached.

## Alternatives considered

- **A "travel mode" flag.** One boolean can't carry a second home point,
  a zone and a job list, and the third place would need a third flag.
- **Editing `.env` per place.** The values that change aren't secrets, and
  a diff of `.env` is the worst place to keep "where are we".
- **Bourdain's trip legs for the stay.** Legs are dated itineraries with
  stays; a three-month stay with the family is a home, and Kipchoge and
  the health line need the same answer Bourdain does. The leg mechanism
  stays for trips within a site (and its runner clock now honours a leg's
  own zone instead of one constant).
- **Per-site launchd plists.** Rendering a second set of plists per place
  duplicates the templates; unloading by label from one set is smaller
  and reversible.

## Consequences

- Adding a place is `rahat_site.py new <name> ...`, an address, and
  `switch`. New code that depends on where the Mac is reads
  `core.site.active()` rather than a constant.
- The owner runs the switch (it touches launchd); the health line is the
  check that the clock followed.
- Not decided here: a second knowledge-base region for Bourdain (the
  gazetteer loader takes a bounding box; Maps grounding answers until one
  is loaded), currency in Bourdain's price rules, and Genie's household
  calendar while Genie is off (nothing is written to it there).
