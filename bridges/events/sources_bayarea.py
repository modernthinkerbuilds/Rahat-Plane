"""bridges.events.sources_bayarea — the feeds Genie subscribes to (2026-10-14).

Maintained data, like registry.DEFAULT_SOURCES, which imports this list.
Every URL here was fetched and found to return upcoming events on
2026-10-11/12 (the research is claude/genie_event_feeds_2026-10-11.md in
the owner's project); a feed that stops yielding shows up on the daily
health line. The vault overlay can add, override or disable any entry.

Kinds: the code kinds (bibliocommons, libcal, communico, tribe, localist,
livewhale, spektrix, squarespace_rss, ical) are parsed in code and run
every pass; "page" is a server-rendered page the model reads once a day.

The last section holds feeds whose robots.txt disallows automated
readers. The owner chose to subscribe (2026-10-14): they are the
calendars' own subscribe links, read a few times a day by a reader that
names itself in its User-Agent; a 403 or 429 is honoured (the fetch
fails, the health line says so), never retried in a loop.

URL tokens: {start} {end} {days} are filled at fetch time; {family} on a
BiblioCommons URL becomes the library's own ids for babies, toddlers,
preschoolers, kids and families (readers.fetch finds them in the feed),
so a 2,000-event county calendar comes back as the family programs.

Categories carry what the household filters on: "kids", "infant",
"toddler" come from each event's own labels (readers.audience_tags), so
a library source carries only "library" and its events say who they are
for;
"adults" marks a venue whose shows are for grown-ups; "culture" is the
owner's 2026-10-12 ask (music, theatre, art, film, books, festivals).
"""
from __future__ import annotations

_BC = "https://gateway.bibliocommons.com/v2/libraries/{slug}/events/search?startDate={{start}}&endDate={{end}}&audiences={{family}}&limit=50&page=1"
_LIBCAL = "https://{host}.libcal.com/ajax/calendar/list?c={cid}&date=0000-00-00&perpage=48&page=1&audience=&cats=&camps=&inc=0"
_COMMUNICO = ("https://{host}.libnet.info/eeventcaldata?event_type=0&req=%7B%22private%22%3Afalse%2C%22date%22%3A%22{{start}}%22%2C"
              "%22days%22%3A{{days}}%2C%22locations%22%3A%5B%5D%2C%22ages%22%3A%5B%5D%2C%22types%22%3A%5B%5D%7D")
_TRIBE = "https://{host}/wp-json/tribe/events/v1/events?per_page=50&start_date={{start}}&end_date={{end}}&page=1"


def _bc(slug: str) -> str:
    return _BC.format(slug=slug)


def _libcal(host: str, cid: str = "-1") -> str:
    return _LIBCAL.format(host=host, cid=cid)


def _communico(host: str) -> str:
    return _COMMUNICO.format(host=host)


def _tribe(host: str) -> str:
    return _TRIBE.format(host=host)


BAY_AREA_SOURCES: list[dict] = [
    # ══ Libraries: the backbone. Storytimes, baby and toddler times,
    # family concerts and crafts, Diwali and holiday programs. ══
    # ── Peninsula ──
    {"id": "smcl", "name": "San Mateo County Libraries", "kind": "bibliocommons", "url": _bc("smcl"),
     "city": "San Mateo County", "categories": ["library"], "region": "peninsula"},
    {"id": "sanmateo-library", "name": "San Mateo Public Library", "kind": "libcal", "url": _libcal("sanmateopublic"),
     "city": "San Mateo", "venue": "San Mateo Public Library", "categories": ["library"], "region": "peninsula"},
    {"id": "burlingame-library", "name": "Burlingame Public Library", "kind": "libcal", "url": _libcal("burlingame"),
     "city": "Burlingame", "venue": "Burlingame Public Library", "categories": ["library"], "region": "peninsula"},
    # ── South Bay ──
    {"id": "sunnyvale-library", "name": "Sunnyvale Public Library", "kind": "page",
     "url": "https://www.library.sunnyvale.ca.gov/events/calendar-month-view",
     "city": "Sunnyvale", "venue": "Sunnyvale Public Library", "categories": ["library"], "region": "south-bay",
     "query_hint": "baby lapsit, toddler, preschool, family and sensory storytimes, kids programs"},
    {"id": "losgatos-library", "name": "Los Gatos Library", "kind": "libcal", "url": _libcal("losgatosca"),
     "city": "Los Gatos", "venue": "Los Gatos Library", "categories": ["library"], "region": "south-bay"},
    # ── East Bay ──
    {"id": "oakland-library", "name": "Oakland Public Library", "kind": "bibliocommons", "url": _bc("oaklandlibrary"),
     "city": "Oakland", "categories": ["library"], "region": "east-bay"},
    {"id": "berkeley-library", "name": "Berkeley Public Library", "kind": "communico", "url": _communico("berkeleypubliclibrary"),
     "city": "Berkeley", "categories": ["library"], "region": "east-bay"},
    {"id": "alameda-county-library", "name": "Alameda County Library", "kind": "bibliocommons", "url": _bc("aclibrary"),
     "city": "Alameda County", "categories": ["library"], "region": "east-bay"},
    {"id": "contra-costa-library", "name": "Contra Costa County Library", "kind": "bibliocommons", "url": _bc("ccclib"),
     "city": "Contra Costa County", "categories": ["library"], "region": "east-bay"},
    # ── SF, Marin, the coast ──
    {"id": "marin-library", "name": "Marin County Free Library", "kind": "bibliocommons", "url": _bc("marinlibrary"),
     "city": "Marin", "categories": ["library"], "region": "north-bay"},
    {"id": "sfpl", "name": "San Francisco Public Library", "kind": "page", "url": "https://sfpl.org/events",
     "city": "San Francisco", "venue": "San Francisco Public Library", "categories": ["library"], "region": "sf",
     "query_hint": "storytimes, baby and toddler programs, family events, author talks"},
    {"id": "santacruz-library", "name": "Santa Cruz Public Libraries", "kind": "communico", "url": _communico("santacruzpl"),
     "city": "Santa Cruz", "categories": ["library"], "region": "santa-cruz"},

    # ══ Family venues and museums ══
    {"id": "cdm-sj", "name": "Children's Discovery Museum of San José", "kind": "tribe", "url": _tribe("www.cdm.org"),
     "city": "San Jose", "venue": "Children's Discovery Museum", "categories": ["museum", "kids"], "region": "south-bay"},
    {"id": "sccounty-parks", "name": "Santa Clara County Parks events", "kind": "page",
     "url": "https://parks.santaclaracounty.gov/events", "city": "Santa Clara County",
     "categories": ["outdoor", "family"], "region": "south-bay",
     "query_hint": "park events: Day on the Bay, Raptor Fest, Fantasy of Lights, nature walks, family programs"},
    {"id": "hiller", "name": "Hiller Aviation Museum", "kind": "tribe", "url": _tribe("www.hiller.org"),
     "city": "San Carlos", "venue": "Hiller Aviation Museum", "categories": ["museum", "kids"], "region": "peninsula"},
    {"id": "curiodyssey", "name": "CuriOdyssey", "kind": "tribe", "url": _tribe("curiodyssey.org"),
     "city": "San Mateo", "venue": "CuriOdyssey", "categories": ["museum", "kids"], "region": "peninsula"},
    {"id": "fairyland", "name": "Children's Fairyland", "kind": "tribe", "url": _tribe("fairyland.org"),
     "city": "Oakland", "venue": "Children's Fairyland", "categories": ["kids", "show"], "region": "east-bay"},
    {"id": "chabot", "name": "Chabot Space & Science Center", "kind": "ical",
     "url": "https://chabotspace.org/?post_type=tribe_events&ical=1&eventDisplay=list",
     "city": "Oakland", "venue": "Chabot Space & Science Center", "categories": ["museum", "kids"], "region": "east-bay"},
    {"id": "omca", "name": "Oakland Museum of California", "kind": "ical", "url": "https://museumca.org/events/list/?ical=1",
     "city": "Oakland", "venue": "Oakland Museum of California", "categories": ["museum", "culture", "family"], "region": "east-bay"},
    {"id": "ebparks", "name": "East Bay Regional Park District", "kind": "page", "url": "https://www.ebparks.org/calendar",
     "city": "East Bay", "categories": ["outdoor", "family"], "region": "east-bay",
     "query_hint": "naturalist programs, storytimes in the parks, farm days, drop-in programs (the page shows today only)"},
    {"id": "berkeley-city", "name": "City of Berkeley events", "kind": "page",
     "url": "https://berkeleyca.gov/community-recreation/events?page=0", "city": "Berkeley",
     "categories": ["city", "family", "festival"], "region": "east-bay",
     "query_hint": "city festivals, family craft workshops, recreation events; skip council and commission meetings"},
    {"id": "gggp", "name": "Gardens of Golden Gate Park", "kind": "tribe", "url": _tribe("gggp.org"),
     "city": "San Francisco", "venue": "SF Botanical Garden", "categories": ["garden", "outdoor", "family"], "region": "sf"},
    {"id": "cal-academy", "name": "California Academy of Sciences", "kind": "page", "url": "https://www.calacademy.org/events",
     "city": "San Francisco", "venue": "California Academy of Sciences", "categories": ["museum", "family"], "region": "sf",
     "query_hint": "exhibits, family programs and festivals; NightLife is 21+ (mark it 21+)"},
    {"id": "badm", "name": "Bay Area Discovery Museum", "kind": "page", "url": "https://bayareadiscoverymuseum.org/events/",
     "city": "Sausalito", "venue": "Bay Area Discovery Museum", "categories": ["museum", "kids"], "region": "north-bay",
     "query_hint": "family events and festivals at the museum"},
    {"id": "visit-santacruz", "name": "Visit Santa Cruz County events", "kind": "page",
     "url": "https://www.santacruz.org/upcoming-events/list/?hide_subsequent_recurrences=1",
     "city": "Santa Cruz", "categories": ["family", "festival", "beach"], "region": "santa-cruz",
     "query_hint": "festivals, beach and downtown events, family days"},
    {"id": "montereypl", "name": "Monterey Public Library & Museums", "kind": "ical",
     "url": "https://montereypl.libcal.com/ical_subscribe.php?src=p&cid=10382",
     "city": "Monterey", "venue": "Monterey Public Library", "categories": ["library", "coastal"], "region": "monterey",
     "note": "the coast's feed: Monterey, Pacific Grove, Carmel; a Big Sur trip is planned by the concierge's live search"},

    # ══ City and downtown pages ══
    {"id": "menlopark-city", "name": "Menlo Park city & library events", "kind": "page",
     "url": "https://www.menlopark.gov/Government/Departments/Library-and-Community-Services/Events",
     "city": "Menlo Park", "categories": ["city", "library", "family"], "region": "peninsula",
     "query_hint": "library storytimes, community events, festivals; skip council and commission meetings"},
    {"id": "ssf-city", "name": "South San Francisco city & library events", "kind": "page", "url": "https://www.ssfca.gov/Events",
     "city": "South San Francisco", "categories": ["city", "library", "family"], "region": "peninsula",
     "query_hint": "baby, toddler and pajama storytimes, kids & family events, festivals"},
    {"id": "downtown-losaltos", "name": "Downtown Los Altos events", "kind": "tribe", "url": _tribe("downtownlosaltos.org"),
     "city": "Los Altos", "venue": "Downtown Los Altos", "categories": ["festival", "family", "market"], "region": "south-bay"},
    {"id": "losaltos-history", "name": "Los Altos History Museum", "kind": "ical", "url": "https://www.losaltoshistory.org/events.ics",
     "city": "Los Altos", "venue": "Los Altos History Museum", "categories": ["museum", "culture"], "region": "south-bay"},
    {"id": "hidden-villa", "name": "Hidden Villa", "kind": "page", "url": "https://www.hiddenvilla.org/calendar/individuals-families/",
     "city": "Los Altos Hills", "venue": "Hidden Villa", "categories": ["farm", "outdoor", "family"], "region": "south-bay",
     "query_hint": "farm tours, family programs and workshops (dates print without a year); skip volunteer days"},
    {"id": "losgatos-chamber", "name": "Los Gatos Chamber events", "kind": "page", "url": "https://www.losgatoschamber.com/events",
     "city": "Los Gatos", "categories": ["city", "family", "culture"], "region": "south-bay",
     "query_hint": "downtown and community events, museum (NUMU) events, workshops; skip council meetings and business mixers"},
    {"id": "saratoga-chamber", "name": "Saratoga Village events", "kind": "page", "url": "https://www.saratogachamber.org/",
     "city": "Saratoga", "categories": ["city", "family", "festival"], "region": "south-bay",
     "query_hint": "village events: SCAREatoga scarecrows, festivals, concerts; skip business mixers"},
    {"id": "hakone", "name": "Hakone Estate & Gardens", "kind": "page", "url": "https://www.hakone.com/events",
     "city": "Saratoga", "venue": "Hakone Estate & Gardens", "categories": ["garden", "culture", "family"], "region": "south-bay",
     "query_hint": "tea ceremony demonstrations, Oshogatsu, exhibits, workshops (dates may print without a year)"},
    {"id": "gamble-garden", "name": "Gamble Garden", "kind": "tribe", "url": _tribe("www.gamblegarden.org"),
     "city": "Palo Alto", "venue": "Gamble Garden", "categories": ["garden", "outdoor", "family"], "region": "south-bay"},
    {"id": "uc-botanical", "name": "UC Botanical Garden at Berkeley", "kind": "tribe", "url": _tribe("botanicalgarden.berkeley.edu"),
     "city": "Berkeley", "venue": "UC Botanical Garden", "categories": ["garden", "outdoor", "family"], "region": "east-bay"},
    {"id": "lake-merritt-gardens", "name": "Gardens at Lake Merritt", "kind": "page",
     "url": "https://gardensatlakemerritt.org/autumn-lights-festival-2026/", "city": "Oakland",
     "venue": "Gardens at Lake Merritt", "categories": ["garden", "festival", "family"], "region": "east-bay",
     "query_hint": "the Autumn Lights Festival dates and hours"},

    # ══ Culture (owner, 2026-10-12): Berkeley, Oakland, Palo Alto,
    # Saratoga, Los Altos, Los Gatos. Music incl. Indian classical,
    # theatre, art, film, books, festivals. ══
    # ── Stanford and Palo Alto ──
    {"id": "stanford-live", "name": "Stanford Live", "kind": "spektrix",
     "url": "https://ticketing.purchase.live.stanford.edu/stanfordlive/api/v3/events",
     "city": "Stanford", "venue": "Stanford Live", "categories": ["music", "show", "culture"], "region": "south-bay"},
    {"id": "stanford-music", "name": "Stanford Department of Music", "kind": "ical",
     "url": "https://events.stanford.edu/department/department_of_music/calendar/ics",
     "city": "Stanford", "categories": ["music", "concert", "culture"], "region": "south-bay"},
    {"id": "cantor", "name": "Cantor Arts Center", "kind": "localist",
     "url": "https://events.stanford.edu/api/2/events?start={start}&days={days}&pp=100&venue_id=37946731848987&page=1",
     "city": "Stanford", "venue": "Cantor Arts Center", "categories": ["museum", "art", "culture"], "region": "south-bay"},
    {"id": "anderson", "name": "Anderson Collection at Stanford", "kind": "ical",
     "url": "https://events.stanford.edu/department/anderson_collection_at_stanford_university/calendar/ics",
     "city": "Stanford", "venue": "Anderson Collection", "categories": ["museum", "art", "culture"], "region": "south-bay"},
    {"id": "stanford-south-asia", "name": "Stanford Center for South Asia", "kind": "ical",
     "url": "https://events.stanford.edu/department/center_for_south_asia/calendar/ics",
     "city": "Stanford", "categories": ["indian", "talk", "culture", "adults"], "region": "south-bay"},
    {"id": "stanford-jazz", "name": "Stanford Jazz Workshop", "kind": "tribe", "url": _tribe("stanfordjazz.org"),
     "city": "Stanford", "categories": ["music", "jazz", "culture"], "region": "south-bay"},
    {"id": "paphil", "name": "Palo Alto Philharmonic", "kind": "squarespace_rss", "url": "https://www.paphil.org/performances?format=rss",
     "city": "Palo Alto", "venue": "Palo Alto Philharmonic", "categories": ["music", "concert", "culture"], "region": "south-bay"},
    {"id": "theatreworks", "name": "TheatreWorks Silicon Valley", "kind": "page", "url": "https://theatreworks.org/mainstage/",
     "city": "Palo Alto", "categories": ["theatre", "show", "culture"], "region": "south-bay",
     "query_hint": "mainstage productions with their run dates and venue (Lucie Stern Theatre or the Mountain View Center for the Performing Arts)"},
    {"id": "paplayers", "name": "Palo Alto Players", "kind": "page", "url": "https://paplayers.org/season/",
     "city": "Palo Alto", "venue": "Lucie Stern Theatre", "categories": ["theatre", "show", "culture"], "region": "south-bay",
     "query_hint": "the season's shows and their run dates"},
    {"id": "oshman-jcc", "name": "Oshman Family JCC events", "kind": "page",
     "url": "https://www.tickettailor.com/all-tickets-by-event/oshmanfamilyjcc/", "city": "Palo Alto",
     "venue": "Oshman Family JCC", "categories": ["culture", "family", "music"], "region": "south-bay",
     "query_hint": "concerts, author talks, toddler music and family events with dates"},
    {"id": "music-at-menlo", "name": "Music@Menlo", "kind": "page", "url": "https://www.musicatmenlo.org/",
     "city": "Menlo Park", "categories": ["music", "concert", "culture", "adults"], "region": "peninsula",
     "query_hint": "chamber music concerts and their dates"},
    # ── Saratoga, Los Altos, Los Gatos ──
    {"id": "montalvo", "name": "Montalvo Arts Center", "kind": "page", "url": "https://montalvoarts.org/experience/events-calendar/",
     "city": "Saratoga", "venue": "Montalvo Arts Center", "categories": ["culture", "music", "family"], "region": "south-bay",
     "query_hint": "concerts, family and garden events, school-series matinees with dates"},
    {"id": "mountain-winery", "name": "The Mountain Winery", "kind": "page", "url": "https://www.jambase.com/venue/mountain-winery",
     "city": "Saratoga", "venue": "The Mountain Winery", "categories": ["music", "concert", "adults"], "region": "south-bay",
     "query_hint": "concert dates at the venue"},
    {"id": "westvalley-theatre", "name": "West Valley College performing arts", "kind": "page",
     "url": "https://www.westvalley.edu/academics/theater/productions/", "city": "Saratoga",
     "venue": "West Valley College", "categories": ["theatre", "show", "culture"], "region": "south-bay",
     "query_hint": "the current season's productions and dates only (the page keeps an archive of past seasons)"},
    {"id": "losaltos-stage", "name": "Los Altos Stage Company", "kind": "page", "url": "https://losaltosstage.org/single-tickets/",
     "city": "Los Altos", "venue": "Bus Barn Theater", "categories": ["theatre", "show", "culture"], "region": "south-bay",
     "query_hint": "productions and their run dates (years from the season heading)"},
    {"id": "foothill-theatre", "name": "Foothill College Theatre Arts", "kind": "page",
     "url": "https://foothill.edu/theatre/shows/index.html", "city": "Los Altos Hills", "venue": "Lohman Theatre",
     "categories": ["theatre", "show", "culture"], "region": "south-bay", "query_hint": "productions and performance dates"},
    {"id": "cinelux-losgatos", "name": "Los Gatos Theatre (CineLux)", "kind": "page",
     "url": "https://www.cineluxtheatres.com/cinelux-los-gatos-theatre", "city": "Los Gatos",
     "venue": "Los Gatos Theatre", "categories": ["film", "culture"], "region": "south-bay",
     "query_hint": "special screenings: sensory-friendly, opera, Flashback Cinema (today's regular showtimes are not events)"},
    {"id": "testarossa", "name": "Testarossa Winery live music", "kind": "page",
     "url": "https://www.testarossa.com/bistro107/livemusic/", "city": "Los Gatos", "venue": "Testarossa Winery",
     "categories": ["music", "adults"], "region": "south-bay", "query_hint": "live music dates (Thursdays) and dinners"},
    # ── Berkeley ──
    {"id": "bampfa", "name": "BAMPFA", "kind": "ical", "url": "https://bampfa.org/events/upcoming.ics",
     "city": "Berkeley", "venue": "BAMPFA", "categories": ["film", "art", "culture"], "region": "east-bay"},
    {"id": "freight", "name": "Freight & Salvage", "kind": "page", "url": "https://thefreight.org/",
     "city": "Berkeley", "venue": "Freight & Salvage", "categories": ["music", "folk", "culture"], "region": "east-bay",
     "query_hint": "upcoming shows with dates and times, genres (folk, roots, world, Indian classical); skip cancelled shows"},
    {"id": "cal-performances", "name": "Cal Performances", "kind": "page", "url": "https://calperformances.org/calendar/",
     "city": "Berkeley", "venue": "Zellerbach Hall", "categories": ["music", "dance", "culture"], "region": "east-bay",
     "query_hint": "performances with dates and times"},
    {"id": "berkeley-rep", "name": "Berkeley Repertory Theatre", "kind": "page", "url": "https://www.berkeleyrep.org/shows",
     "city": "Berkeley", "venue": "Berkeley Rep", "categories": ["theatre", "show", "culture", "adults"], "region": "east-bay",
     "query_hint": "productions and their run dates"},
    {"id": "berkeley-symphony", "name": "Berkeley Symphony", "kind": "page", "url": "https://www.berkeleysymphony.org/",
     "city": "Berkeley", "categories": ["music", "concert", "culture"], "region": "east-bay",
     "query_hint": "concerts with dates, times and venues, including the free family concert"},
    {"id": "berkeley-playhouse", "name": "Berkeley Playhouse", "kind": "page", "url": "https://berkeleyplayhouse.org/2026-27-season/",
     "city": "Berkeley", "venue": "Julia Morgan Theater", "categories": ["theatre", "show", "family"], "region": "east-bay",
     "query_hint": "family musicals and their run dates"},
    {"id": "cjc", "name": "California Jazz Conservatory", "kind": "page", "url": "https://concerts.jazzschool.org/",
     "city": "Berkeley", "venue": "California Jazz Conservatory", "categories": ["music", "jazz", "culture"], "region": "east-bay",
     "query_hint": "concerts, jams and free Brown Bag talks with dates"},
    {"id": "uc-theatre", "name": "The UC Theatre", "kind": "page", "url": "https://www.theuctheatre.org/",
     "city": "Berkeley", "venue": "The UC Theatre", "categories": ["music", "concert", "adults"], "region": "east-bay",
     "query_hint": "shows with dates (no year printed: the next occurrence)"},
    {"id": "greek-theatre", "name": "Greek Theatre Berkeley", "kind": "page", "url": "https://thegreekberkeley.com/",
     "city": "Berkeley", "venue": "Greek Theatre", "categories": ["music", "concert", "adults"], "region": "east-bay",
     "query_hint": "concert dates"},
    {"id": "shotgun-players", "name": "Shotgun Players", "kind": "page", "url": "https://shotgunplayers.org/tickets/",
     "city": "Berkeley", "venue": "Ashby Stage", "categories": ["theatre", "show", "adults"], "region": "east-bay",
     "query_hint": "productions and run dates"},
    {"id": "mrs-dalloways", "name": "Mrs. Dalloway's bookshop events", "kind": "page", "url": "https://www.mrsdalloways.com/events",
     "city": "Berkeley", "venue": "Mrs. Dalloway's", "categories": ["books", "author", "culture", "kids"], "region": "east-bay",
     "query_hint": "author events and kids' story times with dates and times"},
    # ── Oakland ──
    {"id": "art-murmur", "name": "Oakland Art Murmur", "kind": "tribe", "url": _tribe("oaklandartmurmur.org"),
     "city": "Oakland", "categories": ["art", "gallery", "culture"], "region": "east-bay"},
    {"id": "oacc", "name": "Oakland Asian Cultural Center", "kind": "tribe", "url": _tribe("oacc.cc"),
     "city": "Oakland", "venue": "Oakland Asian Cultural Center", "categories": ["culture", "music", "indian"], "region": "east-bay"},
    {"id": "piedmont-piano", "name": "Piedmont Piano Company concerts", "kind": "squarespace_rss",
     "url": "https://piedmontpiano.com/calendar?format=rss", "city": "Oakland", "venue": "Piedmont Piano Company",
     "categories": ["music", "jazz", "culture", "adults"], "region": "east-bay"},
    {"id": "oigc", "name": "Oakland Interfaith Gospel Choir", "kind": "page", "url": "https://www.oigc.org/events",
     "city": "Oakland", "categories": ["music", "culture", "family"], "region": "east-bay",
     "query_hint": "concerts with dates, times and places; skip out-of-town workshops"},
    {"id": "fox-oakland", "name": "Fox Theater Oakland", "kind": "page", "url": "https://thefoxoakland.com/",
     "city": "Oakland", "venue": "Fox Theater", "categories": ["music", "concert", "adults"], "region": "east-bay",
     "query_hint": "shows with dates"},
    {"id": "yoshis", "name": "Yoshi's Oakland", "kind": "page", "url": "https://yoshis.com/events/calendar",
     "city": "Oakland", "venue": "Yoshi's", "categories": ["music", "jazz", "adults"], "region": "east-bay",
     "query_hint": "shows with dates (no year printed: the next occurrence); skip VIP and add-on listings"},
    {"id": "paramount-oakland", "name": "Paramount Theatre Oakland", "kind": "page", "url": "https://www.paramountoakland.org/events",
     "city": "Oakland", "venue": "Paramount Theatre", "categories": ["show", "culture"], "region": "east-bay",
     "query_hint": "shows with dates"},
    {"id": "oakland-ballet", "name": "Oakland Ballet", "kind": "page", "url": "https://oaklandballet.org/",
     "city": "Oakland", "venue": "Paramount Theatre", "categories": ["dance", "show", "culture", "family"], "region": "east-bay",
     "query_hint": "performances with dates (Luna Mexicana, the Nutcracker)"},
    {"id": "oakland-symphony", "name": "Oakland Symphony", "kind": "page",
     "url": "https://www.oaklandsymphony.org/event-category/tickets-available/", "city": "Oakland",
     "venue": "Paramount Theatre", "categories": ["music", "concert", "culture"], "region": "east-bay",
     "query_hint": "concerts with dates"},
    {"id": "grand-lake-theatre", "name": "Grand Lake Theatre", "kind": "page", "url": "https://www.renaissancerialto.com/",
     "city": "Oakland", "venue": "Grand Lake Theatre", "categories": ["film", "culture"], "region": "east-bay",
     "query_hint": "special screenings and events with dates (regular daily showtimes are not events)"},
    {"id": "oakland-theater-project", "name": "Oakland Theater Project", "kind": "page",
     "url": "https://oaklandtheaterproject.org/2026-season", "city": "Oakland", "venue": "Oakland Theater Project",
     "categories": ["theatre", "show", "adults"], "region": "east-bay", "query_hint": "productions, film club and book club dates"},
    {"id": "india-currents", "name": "India Currents events", "kind": "tribe", "url": _tribe("indiacurrents.com"),
     "city": "Bay Area", "categories": ["indian", "music", "dance", "culture"], "region": "south-bay"},
    {"id": "funcheap-feed", "name": "Funcheap newest events", "kind": "page",
     "url": "https://feeds.feedburner.com/funcheapsf_recent_added_events", "city": "Bay Area",
     "categories": ["free", "festival", "family"], "region": "sf",
     "query_hint": "free and cheap events; the date is the title's prefix (MM/DD/YY)"},

    # ══ Feeds whose robots.txt asks automated readers to stay out (owner's
    # decision, 2026-10-14: "there's no harm in reading"). These are the
    # calendars' own subscribe links, read a few times a day by a reader
    # that names itself; none could be verified from the research tools,
    # which honour robots.txt, so the daily health line is the check: a
    # feed that fetches nothing for three days is named there, and the
    # overlay's "disable" list retires it without a commit. ══
    # ── CivicPlus city calendars: the standard RSS of every calendar ──
    *[{"id": sid, "name": name, "kind": "page",
       "url": f"https://{host}/RSSFeed.aspx?ModID=58&CID=All-calendar.xml",
       "city": city, "categories": ["city", "family", "festival"], "region": region,
       "query_hint": "city special events, festivals, parks and recreation programs, library events; "
                     "skip council, commission and committee meetings",
       "note": "unverified: robots.txt disallows readers; the owner chose to subscribe (2026-10-14)"}
      for sid, name, host, city, region in (
          ("sanmateo-city", "City of San Mateo events", "www.cityofsanmateo.org", "San Mateo", "peninsula"),
          ("dalycity-city", "City of Daly City events", "www.dalycity.org", "Daly City", "peninsula"),
          ("sanbruno-city", "City of San Bruno events", "www.sanbruno.ca.gov", "San Bruno", "peninsula"),
          ("halfmoonbay-city", "City of Half Moon Bay events", "www.half-moon-bay.ca.us", "Half Moon Bay", "peninsula"),
          ("fostercity-city", "City of Foster City events", "www.fostercity.org", "Foster City", "peninsula"),
          ("burlingame-city", "City of Burlingame events", "burlingame.org", "Burlingame", "peninsula"),
          ("campbell-city", "City of Campbell events", "www.campbellca.gov", "Campbell", "south-bay"),
          ("losgatos-town", "Town of Los Gatos events", "www.losgatosca.gov", "Los Gatos", "south-bay"),
          ("losaltoshills-town", "Town of Los Altos Hills events", "www.losaltoshills.ca.gov", "Los Altos Hills", "south-bay"),
          ("richmond-city", "City of Richmond events", "www.ci.richmond.ca.us", "Richmond", "east-bay"),
          ("dublin-city", "City of Dublin events", "dublin.ca.gov", "Dublin", "east-bay"),
          ("elcerrito-city", "City of El Cerrito events", "www.el-cerrito.org", "El Cerrito", "east-bay"))],
    {"id": "millbrae-city", "name": "City of Millbrae events", "kind": "ical",
     "url": "https://www.ci.millbrae.ca.us/common/modules/iCalendar/iCalendar.aspx?catID=26&feed=calendar",
     "city": "Millbrae", "categories": ["city", "family", "festival"], "region": "peninsula",
     "note": "unverified: robots.txt disallows readers; the owner chose to subscribe (2026-10-14)"},
    # ── The East Bay family calendar, and two aggregators ──
    # 2026-10-15: the site answers 404 on The Events Calendar's REST route (three passes); its iCal export
    # is the feed the site itself offers. A list view from today, so the export is upcoming events.
    {"id": "510families", "name": "510 Families", "kind": "ical",
     "url": "https://www.510families.com/events/list/?tribe-bar-date={start}&ical=1",
     "city": "East Bay", "categories": ["kids", "family", "festival"], "region": "east-bay",
     "note": "unverified: robots.txt disallows readers; the owner chose to subscribe (2026-10-14); iCal since 2026-10-15"},
    {"id": "visit-trivalley", "name": "Visit Tri-Valley events", "kind": "page", "url": "https://www.visittrivalley.com/event/rss/",
     "city": "Tri-Valley", "categories": ["family", "festival"], "region": "east-bay",
     "query_hint": "Pleasanton, Livermore, Dublin, Danville and San Ramon events; the dates are in each item's text",
     "note": "unverified: robots.txt disallows readers; the owner chose to subscribe (2026-10-14)"},
    {"id": "ybg-festival", "name": "Yerba Buena Gardens Festival", "kind": "tribe", "url": _tribe("ybgfestival.org"),
     "city": "San Francisco", "venue": "Yerba Buena Gardens", "categories": ["music", "festival", "family", "free"], "region": "sf",
     "note": "unverified: robots.txt disallows readers; the owner chose to subscribe (2026-10-14)"},
    # ── Two public Google Calendars ──
    {"id": "losaltos-stage-cal", "name": "Los Altos Stage Company calendar", "kind": "ical",
     "url": "https://calendar.google.com/calendar/ical/c776afcc005eb01f0f5e4507c83697e20eeed9430b2101435f5a4b10a914cbee%40group.calendar.google.com/public/basic.ics",
     "city": "Los Altos", "venue": "Bus Barn Theater", "categories": ["theatre", "show", "culture"], "region": "south-bay",
     "note": "unverified from here (robots.txt); a public Google Calendar; the owner chose to subscribe (2026-10-14)"},
    {"id": "saratoga-history-cal", "name": "Saratoga Historical Foundation calendar", "kind": "ical",
     "url": "https://calendar.google.com/calendar/ical/0e32a316dda2fe3a402d603cc0436b49dc2e5db919c60cc3d91dc451f8f3eab1%40group.calendar.google.com/public/basic.ics",
     "city": "Saratoga", "venue": "Saratoga History Museum", "categories": ["museum", "culture"], "region": "south-bay",
     "note": "unverified from here (robots.txt); a public Google Calendar; the owner chose to subscribe (2026-10-14)"},
    # ── Two Los Gatos pages ──
    {"id": "numu", "name": "New Museum Los Gatos (NUMU)", "kind": "page", "url": "https://numu.org/",
     "city": "Los Gatos", "venue": "NUMU", "categories": ["museum", "art", "culture", "family"], "region": "south-bay",
     "query_hint": "exhibitions, family art days, talks with dates",
     "note": "unverified: robots.txt disallows readers; the owner chose to subscribe (2026-10-14)"},
    {"id": "billy-jones-railroad", "name": "Billy Jones Wildcat Railroad", "kind": "page", "url": "https://www.billyjonesrailroad.org/",
     "city": "Los Gatos", "venue": "Oak Meadow Park", "categories": ["kids", "family", "outdoor"], "region": "south-bay",
     "query_hint": "special train days, holiday trains, operating hours changes with dates",
     "note": "unverified: robots.txt disallows readers; the owner chose to subscribe (2026-10-14)"},
]
