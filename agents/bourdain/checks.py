"""agents.bourdain.checks — Bourdain's own mechanical outcome checks
(ADR-018 §2.2 step 4; outcomes P1–P12, D2, D4, D5, N2).

These are the household's eating rules as code over the profile data:
diet words per member with the day's overrides, the gluten scope, the
seafood-room rule, a named main per diner, the infant's room, bar-led
rooms, the price policy, chains, the coffee ritual, the day's rhythm,
acclaim, format, variety and novelty. Every rule reads the profile
(`agents/bourdain/profile.py`), never a constant about a person.

`register(registry)` is called by `core.verify.for_agent("bourdain")`.
"""
from __future__ import annotations

import re

from core.harness.contract import ContractOutcome
from core.verify import Failure, VerifyContext, family_of, matches_cuisine
from core.kb import schema as kb

MEAT = {"beef", "steak", "pork", "bacon", "ham", "sausage", "chorizo", "pepperoni", "prosciutto", "salami",
        "pancetta", "lamb", "mutton", "goat", "veal", "duck", "turkey", "meatball", "meatballs", "lardons",
        "gelatin", "oxtail", "brisket", "ribs", "carnitas", "pastor", "pastrami", "bolognese", "ragu",
        "carne", "asada", "kebab", "kabob", "gyro", "shawarma", "bulgogi", "galbi", "pho", "meat"}
CHICKEN = {"chicken", "wings", "drumstick", "thigh", "poultry", "samgyetang", "dakgalbi"}
SEAFOOD = {"fish", "shrimp", "prawn", "prawns", "crab", "lobster", "oyster", "oysters", "clam", "clams",
           "mussel", "mussels", "squid", "calamari", "octopus", "salmon", "tuna", "cod", "eel", "anchovy",
           "anchovies", "scallop", "scallops", "crawfish", "unagi", "sashimi", "ceviche", "roe", "caviar",
           "seafood", "halibut", "trout", "branzino", "haddock", "chowder", "shellfish", "camarones"}
EGG = {"egg", "eggs", "omelet", "omelette", "frittata", "shakshuka", "benedict"}
DAIRY = {"cheese", "milk", "cream", "butter", "yogurt", "paneer", "ghee", "honey", "mozzarella", "ricotta",
         "parmesan", "burrata", "custard", "gelato"}
GLUTEN_SCOPE = {"pizza", "pasta", "gnocchi", "ravioli", "lasagna", "lasagne", "spaghetti", "penne", "rigatoni",
                "fettuccine", "linguine", "tagliatelle", "pappardelle", "orecchiette", "cavatelli", "bread",
                "toast", "bagel", "bagels", "sandwich", "panini", "burger", "bun", "croissant", "muffin", "cake",
                "pastry", "pastries", "pie", "tart", "waffle", "waffles", "pancake", "pancakes", "cookie",
                "cookies", "scone", "brioche", "focaccia", "pita", "naan", "roti", "paratha", "dumpling",
                "dumplings", "noodle", "noodles", "ramen", "udon", "baguette", "crepe", "crêpe", "biscuit",
                "donut", "doughnut", "danish", "cannoli", "tiramisu", "cone", "pretzel", "crust", "flatbread",
                "bao", "wonton", "gyoza", "mandu", "injera", "japchae", "lo", "mein", "chow"}
GF_MARK = re.compile(r"gluten[- ]free|\bgf\b|\bg\.f\.\b", re.I)
BAR_LED = re.compile(r"\b(wine bar|cocktail bar|pub|taproom|brewery|bar-led|bar led|sports bar|beer hall|lounge)\b", re.I)
BAR_FLAG = re.compile(r"\b(full bar|cocktails?|bar seating|bar area)\b", re.I)
QUICK_TAGS = {"quick", "counter", "takeout", "fast", "fast_casual", "grab_and_go", "cafe", "café", "bakery", "stand"}
HOLE_TAGS = {"hole_in_the_wall", "hole-in-the-wall", "casual", "counter", "no_frills", "family_run", "family-run", "cash_only"}
TABLE_TAGS = {"table_service", "sit_down", "sit-down", "trattoria", "dining_room", "reservations"}


def _tokens(s: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split()}


def _without(note: str) -> set[str]:
    """Words a diner line removes: 'without the dried shrimp', 'no egg', 'hold the bacon'."""
    out: set[str] = set()
    for m in re.finditer(r"\b(?:without|no|hold the|minus|skip the|sans)\s+((?:\w+\s?){1,3})", (note or "").lower()):
        out |= _tokens(m.group(1))
    return out


def _excluded_words(ctx: VerifyContext, member: str) -> tuple[set[str], set[str]]:
    """(hard words, soft words) a member's named main must not contain."""
    diet = (ctx.rules_for(member).get("diet") or {})
    hard: set[str] = set()
    soft: set[str] = set()
    rules = ctx.active_rules(member)
    vegetarian = bool(diet.get("vegetarian")) or any(r in ("vegetarian", "veg", "veggie") for r in rules)
    vegan = any(r == "vegan" for r in rules)
    if vegetarian or vegan:
        hard |= MEAT | CHICKEN | SEAFOOD
    if vegan:
        hard |= EGG | DAIRY
    if diet.get("eggs") is False:
        hard |= EGG
    chicken = str(diet.get("chicken", "yes")).lower()
    if chicken == "no":
        hard |= CHICKEN
    elif chicken == "occasionally" and not any(r in ("chicken ok", "chicken okay", "chicken is ok") for r in rules):
        if not any("chicken" in d.lower() for d in ctx.frame.dishes):
            soft |= CHICKEN
    for w in diet.get("avoid") or []:
        hard |= _tokens(str(w))
    for r in rules:
        m = re.match(r"^no\s+(.+)$", r)
        if m:
            words = _tokens(m.group(1))
            hard |= words
            if words & {"egg", "eggs"}:
                hard |= EGG
            if words & {"chicken"}:
                hard |= CHICKEN
        m = re.match(r"^(.+?)\s+(?:is\s+)?(?:ok|okay|back|fine)$", r)
        if m:
            words = _tokens(m.group(1))
            if words & {"egg", "eggs"}:
                hard -= EGG; soft -= EGG
            if words & {"chicken"}:
                hard -= CHICKEN; soft -= CHICKEN
            hard -= words
    for w in diet.get("ok") or []:
        hard -= _tokens(str(w))
    return hard, soft


def _needs_main(ctx: VerifyContext, member: str) -> bool:
    role = ctx.rules_for(member).get("role", "guest")
    return role not in ("infant", "toddler")


def register(reg) -> None:
    @reg.register("P1")
    def diet_rules(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        for pick in ctx.answer.picks:
            for line in pick.diner_lines:
                if line.member not in ctx.frame.diners:
                    continue
                hard, soft = _excluded_words(ctx, line.member)
                words = (_tokens(line.dish) | _tokens(line.note or "")) - _without(line.note or "")
                hit = sorted(words & hard)
                if hit:
                    out.append(Failure(o.id, f"{pick.name}: {line.member}'s '{line.dish}' has {', '.join(hit)}, outside their rules",
                                       "name a main inside the member's rules", place_id=pick.place_id))
                    continue
                soft_hit = sorted(words & soft)
                if soft_hit:
                    out.append(Failure(o.id, f"{pick.name}: {line.member}'s '{line.dish}' has {', '.join(soft_hit)}, only occasionally in their rules",
                                       "say it is an occasional choice or name another main", place_id=pick.place_id, hard=False))
        return out

    @reg.register("P2")
    def gluten_scope(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        banned = [b.lower() for b in ctx.banned_phrases]
        members = ctx.profile.get("members") or {}
        gf_members = [m for m, v in members.items() if (v.get("diet") or {}).get("gf")] or (["primary"] if "primary" in members else [])
        for pick in ctx.answer.picks:
            texts = [pick.why or ""] + [f"{d.dish} {d.note or ''}" for d in pick.diner_lines] + [f.text for f in pick.flags]
            for t in texts:
                for b in banned:
                    if b and b in t.lower():
                        out.append(Failure(o.id, f"{pick.name}: the banned phrase '{b}' appears", "drop the phrase",
                                           place_id=pick.place_id))
            pe = ctx.place(pick.place_id)
            for line in pick.diner_lines:
                if line.member not in gf_members or line.member not in ctx.frame.diners:
                    continue
                text = f"{line.dish} {line.note or ''}"
                words = _tokens(text)
                if not (words & GLUTEN_SCOPE):
                    continue
                if "injera" in words:
                    if not re.search(r"teff|ask|call|confirm|question", text, re.I):
                        out.append(Failure(o.id, f"{pick.name}: injera for {line.member} without carrying the teff-vs-wheat question",
                                           "say to ask whether the injera is pure teff", place_id=pick.place_id))
                    continue
                item = pe.menu_match(line.dish)
                gf_on_menu = (item or {}).get("gf") if item else None
                if gf_on_menu == "no":
                    out.append(Failure(o.id, f"{pick.name}: the menu says no gluten-free {line.dish} for {line.member}",
                                       "name a different main for the member", place_id=pick.place_id))
                    continue
                if not GF_MARK.search(text) and gf_on_menu not in ("yes", "on_request"):
                    out.append(Failure(o.id, f"{pick.name}: '{line.dish}' for {line.member} is in the gluten scope with no GF version named",
                                       "name the GF version (or a different main) and whether to ask for it", place_id=pick.place_id))
                    continue
                if gf_on_menu not in ("yes", "on_request") and not line.verified:
                    out.append(Failure(o.id, f"{pick.name}: the GF {line.dish} for {line.member} is unverified",
                                       "read the menu page or say 'ask first'", place_id=pick.place_id, hard=False))
        for ln in ctx.answer.lines + ctx.answer.assumptions:
            for b in banned:
                if b and b in ln.lower():
                    out.append(Failure(o.id, f"the banned phrase '{b}' appears", "drop the phrase"))
        return out

    @reg.register("P3")
    def profile_cuisine_exclusions(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        asked = {kb.CUISINE_FAMILIES.get(c.lower(), c.lower()) for c in ctx.frame.cuisines}
        lifted = {kb.CUISINE_FAMILIES.get(c.lower(), c.lower()) for c in ctx.answer.lifted_exclusions}
        for pick in ctx.answer.picks:
            pe = ctx.place(pick.place_id)
            fam = family_of(pe)
            for member in ctx.diners():
                excl = {kb.CUISINE_FAMILIES.get(str(c).lower(), str(c).lower())
                        for c in (ctx.rules_for(member).get("diet") or {}).get("cuisine_out_exclusions") or []}
                if fam and fam in excl:
                    if fam not in asked:
                        out.append(Failure(o.id, f"{pe.name}: {fam} is excluded for {member} when eating out and was not asked for",
                                           "pick another cuisine", place_id=pick.place_id))
                    elif fam not in lifted:
                        out.append(Failure(o.id, f"{pe.name}: {fam} was asked for; say the standing exclusion is lifted for this ask",
                                           "add it to lifted_exclusions", place_id=pick.place_id, hard=False))
        return out

    @reg.register("P4")
    def seafood_room(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        if "spouse" not in ctx.frame.diners:
            return out
        rule = str((ctx.rules_for("spouse").get("diet") or {}).get("seafood_rooms", "hard_no")).lower()
        for pick in ctx.answer.picks:
            pe = ctx.place(pick.place_id)
            cz = ((pe.place or {}).get("cuisine") or "").lower()
            if pe.room.get("seafood_led") is True and rule != "ok":
                out.append(Failure(o.id, f"{pe.name}: a seafood-led room with the spouse at the table",
                                   "pick a room not led by seafood, or a split order", place_id=pick.place_id))
                continue
            if any(w in cz for w in ("seafood", "sushi", "fish", "oyster", "crab", "lobster", "poke")) and \
                    not any(f.outcome == "P4" for f in pick.flags):
                out.append(Failure(o.id, f"{pe.name}: seafood-led signatures, not flagged", "flag it (P4)",
                                   place_id=pick.place_id, hard=False))
        return out

    @reg.register("P5")
    def named_main_per_diner(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        for pick in ctx.answer.picks:
            lines = {d.member: d for d in pick.diner_lines}
            for member in ctx.diners():
                if not _needs_main(ctx, member):
                    continue
                d = lines.get(member)
                if not d or not (d.dish or "").strip():
                    out.append(Failure(o.id, f"{pick.name}: no named main for {member}",
                                       "name one full main for each diner, inside their rules", place_id=pick.place_id))
            for g in ctx.frame.guests:
                if g.label not in lines and not any(g.label in (ln or "") for ln in ctx.answer.lines):
                    out.append(Failure(o.id, f"{pick.name}: nothing said for the guest '{g.label}'",
                                       "a line for the guest, or say their rules are unknown", place_id=pick.place_id, hard=False))
        return out

    @reg.register("P6")
    def infant_room(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        if "infant" not in ctx.frame.diners:
            return out
        for pick in ctx.answer.picks:
            pe = ctx.place(pick.place_id)
            text = (pe.room.get("text") or "").lower() + " " + " ".join((pe.place or {}).get("room_tags") or []).lower()
            if pe.room.get("bar_led") is True:
                out.append(Failure(o.id, f"{pe.name}: a bar room with the baby", "pick a dining room", place_id=pick.place_id))
                continue
            if re.search(r"no strollers|standing only|counter only|21\+|adults only", text):
                out.append(Failure(o.id, f"{pe.name}: no room for a stroller or an adults-only room", "pick another room",
                                   place_id=pick.place_id))
            when, _ = ctx.arrival(pick)
            if (ctx.rules_for("infant").get("room") or {}).get("earlier_tables", True) and when.hour * 60 + when.minute > 20 * 60 + 30:
                out.append(Failure(o.id, f"{pe.name}: a {when.strftime('%H:%M')} table is late for the baby",
                                   "say so, or an earlier table", place_id=pick.place_id, hard=False))
        return out

    @reg.register("P7")
    def no_bar_led(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        for pick in ctx.answer.picks:
            pe = ctx.place(pick.place_id)
            text = (pe.room.get("text") or "") + " " + " ".join((pe.place or {}).get("room_tags") or []) + " " + ((pe.place or {}).get("cuisine") or "")
            if pe.room.get("bar_led") is True or BAR_LED.search(text):
                out.append(Failure(o.id, f"{pe.name}: a bar-led room", "drop it", place_id=pick.place_id))
            elif BAR_FLAG.search(text) and not any(f.outcome == "P7" for f in pick.flags):
                out.append(Failure(o.id, f"{pe.name}: a bar in the room, not flagged", "flag it (P7)", place_id=pick.place_id, hard=False))
        return out

    @reg.register("P8")
    def price_policy(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        pol = (ctx.profile.get("household") or {}).get("price") or {}
        max_level = str(pol.get("max_level", "$$")).count("$")
        cap = float(pol.get("mains_cap_usd", 20))
        for pick in ctx.answer.picks:
            pe = ctx.place(pick.place_id)
            level = pe.price_level
            if not level:
                out.append(Failure(o.id, f"{pe.name}: price level unknown", "read the menu or say the price is unknown",
                                   place_id=pick.place_id, hard=False))
                continue
            parts = [p.count("$") for p in re.split(r"[–\-/]", level) if p.count("$")]
            if not parts:
                continue
            lo, hi = min(parts), max(parts)
            if lo > max_level:
                out.append(Failure(o.id, f"{pe.name}: {level} is over the household's {pol.get('max_level', '$$')}", "drop it",
                                   place_id=pick.place_id))
                continue
            if hi > max_level:
                prices = [_price(i.get("price_text")) for i in pe.menu]
                mains_ok = [p for p in prices if p is not None and p <= cap]
                if not mains_ok:
                    out.append(Failure(o.id, f"{pe.name}: {level} with no menu main at or under ${cap:.0f} on record",
                                       "read the menu and name a main under the cap, or drop it", place_id=pick.place_id))
                elif not any(f.outcome == "P8" for f in pick.flags):
                    out.append(Failure(o.id, f"{pe.name}: {level}, flag the price", "flag it (P8)", place_id=pick.place_id, hard=False))
        return out

    @reg.register("P9")
    def chains(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        pol = (ctx.profile.get("household") or {}).get("chains") or {}
        exclude_from = int(pol.get("exclude_from_locations", 20))
        for pick in ctx.answer.picks:
            pe = ctx.place(pick.place_id)
            if any(v["kind"] == "chain" for v in pe.verdicts):
                out.append(Failure(o.id, f"{pe.name}: the household called it a chain", "drop it", place_id=pick.place_id))
                continue
            n = pe.location_count
            if n is not None and n >= exclude_from:
                out.append(Failure(o.id, f"{pe.name}: {n} locations", "drop the chain", place_id=pick.place_id))
        return out

    @reg.register("P11")
    def coffee_ritual(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        if (ctx.frame.meal or "").lower() != "coffee" or "primary" not in ctx.frame.diners:
            return out
        coffee = ctx.rules_for("primary").get("coffee") or {}
        rec = ctx.answer.recommended() or (ctx.answer.picks[0] if ctx.answer.picks else None)
        if not rec:
            return out
        pe = ctx.place(rec.place_id)
        line = next((d for d in rec.diner_lines if d.member == "primary"), None)
        if coffee.get("gf_pastry_required", True) and not (line and GF_MARK.search(f"{line.dish} {line.note or ''}")):
            out.append(Failure(o.id, f"{pe.name}: no GF pastry named for the primary member", "name the GF pastry or say none",
                               place_id=rec.place_id, hard=False))
        tags = " ".join((pe.place or {}).get("format_tags") or []).lower() + " " + ((pe.place or {}).get("cuisine") or "").lower()
        if coffee.get("roaster_preferred", True) and not re.search(r"roaster|roastery|coffee|espresso|café|cafe", tags + " " + (rec.why or "").lower()):
            out.append(Failure(o.id, f"{pe.name}: not a roaster or coffee bar on record", "prefer a local roaster", place_id=rec.place_id, hard=False))
        return out

    @reg.register("P12")
    def day_rhythm(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        if ctx.answer.kind != "plan" or not ctx.answer.plan:
            return out
        labels = " ".join(s.label.lower() for s in ctx.answer.plan)
        if "coffee" not in labels:
            out.append(Failure(o.id, "no coffee slot in the day", "add the coffee slots from the profile", hard=False))
        if "lunch" in labels and not re.search(r"dessert|gelato|ice cream|sweet", labels):
            out.append(Failure(o.id, "no dessert after lunch", "add a dessert stop", hard=False))
        if "dinner" in labels and not re.search(r"gelato|ice cream|dessert", labels.split("dinner", 1)[-1]):
            out.append(Failure(o.id, "no gelato after dinner", "add a gelato stop", hard=False))
        return out

    @reg.register("D1")
    def favorites_first(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        """The mechanical half: a saved place that fits the ask is not
        passed over silently. (Favorite cuisines are the judge's half.)"""
        if ctx.answer.kind != "picks" or not ctx.answer.picks:
            return []
        if any(ctx.place(p.place_id).saved for p in ctx.answer.picks):
            return []
        city = (ctx.leg.get("city") or "").lower()
        wanted = {kb.CUISINE_FAMILIES.get(c.lower(), c.lower()) for c in ctx.frame.cuisines}
        excluded = ctx.evidence.excluded()
        left = {kb.norm_name(lo.name) for lo in ctx.answer.left_out}
        for pid in ctx.evidence.saved_ids():
            pe = ctx.place(pid)
            if not pe.exists or pid in excluded or pe.status != "open":
                continue
            if city and ((pe.place or {}).get("city") or "").lower() != city:
                continue
            if wanted and family_of(pe) not in wanted:
                continue
            if kb.norm_name(pe.name) in left:
                continue
            return [Failure(o.id, f"{pe.name} is saved, fits the ask and is neither a pick nor left out with a reason",
                            "lead with the save or say why not", hard=False)]
        return []

    @reg.register("N4")
    def reaction_captured(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        """Verified at the next message by the Record step's reaction
        classifier; nothing to check at answer time."""
        return []

    @reg.register("D2")
    def acclaim_for_top_pick(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        rec = ctx.answer.recommended() or (ctx.answer.picks[0] if ctx.answer.picks else None)
        if not rec:
            return []
        pe = ctx.place(rec.place_id)
        if pe.acclaim or any(r.claim == "acclaim" for r in rec.evidence) or pe.facts.get("acclaim"):
            return []
        wanted = [a for a in ctx.frame.attributes if a in ("legendary", "iconic", "oldest", "family_run", "lots_of_reviews")]
        if wanted and not (pe.place or {}).get("founded"):
            return [Failure(o.id, f"{pe.name}: '{', '.join(wanted)}' asked, no acclaim or founding year on record",
                            "call the acclaim tool or read the about page", place_id=rec.place_id, hard=False)]
        return [Failure(o.id, f"{pe.name}: no named acclaim on record for the top pick", "call the acclaim tool",
                        place_id=rec.place_id, hard=False)]

    @reg.register("D4")
    def format_matches(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        want = {f.lower().replace(" ", "_") for f in ctx.frame.formats}
        if not want:
            return out
        for pick in ctx.answer.picks:
            pe = ctx.place(pick.place_id)
            tags = {t.lower() for t in (pe.place or {}).get("format_tags") or []}
            if not tags:
                out.append(Failure(o.id, f"{pe.name}: format unknown for a '{', '.join(sorted(want))}' ask",
                                   "read the room on its page or say the format is unconfirmed", place_id=pick.place_id, hard=False))
                continue
            if "quick" in want and not (tags & QUICK_TAGS):
                out.append(Failure(o.id, f"{pe.name}: not a quick place on record", "pick a counter or takeout place",
                                   place_id=pick.place_id, hard=False))
            if "hole_in_the_wall" in want and (tags & TABLE_TAGS) and not (tags & HOLE_TAGS):
                out.append(Failure(o.id, f"{pe.name}: a table-service room for a hole-in-the-wall ask", "pick a smaller room",
                                   place_id=pick.place_id, hard=False))
            if want & {"table_service", "sit_down"} and (tags & QUICK_TAGS) and not (tags & TABLE_TAGS):
                out.append(Failure(o.id, f"{pe.name}: a counter place for a sit-down ask", "pick a table-service room",
                                   place_id=pick.place_id, hard=False))
            if "takeout" in want and "takeout" not in tags and "delivery" not in tags:
                out.append(Failure(o.id, f"{pe.name}: takeout not on record", "confirm takeout", place_id=pick.place_id, hard=False))
        return out

    @reg.register("D5")
    def variety(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        days = int((ctx.profile.get("household") or {}).get("variety_days", 14))
        excluded = ctx.evidence.excluded(recent_days=days)
        recent = ctx.evidence.recent_families(days=days)
        named = {kb.norm_name(n) for n in ctx.frame.named_places}
        for pick in ctx.answer.picks:
            pe = ctx.place(pick.place_id)
            if kb.norm_name(pe.name) in named:
                continue
            if excluded.get(pick.place_id) == "recent":
                out.append(Failure(o.id, f"{pe.name}: eaten there inside the last {days} days", "vary, unless asked",
                                   place_id=pick.place_id))
            elif family_of(pe) in recent and not any(matches_cuisine(pe, c) for c in ctx.frame.cuisines):
                out.append(Failure(o.id, f"{pe.name}: {family_of(pe)} again inside the last {days} days", "say so or vary",
                                   place_id=pick.place_id, hard=False))
        return out

    @reg.register("N2")
    def novelty_against_regulars(ctx: VerifyContext, o: ContractOutcome) -> list[Failure]:
        out: list[Failure] = []
        regulars = ctx.evidence.regulars()
        reg_norms = {kb.norm_name(n) for n in regulars.values()}
        for pick in ctx.answer.picks:
            pe = ctx.place(pick.place_id)
            n = kb.norm_name(pe.name)
            if pick.place_id in regulars:
                out.append(Failure(o.id, f"{pe.name} is a regular; an alternative was asked", "offer something new",
                                   place_id=pick.place_id))
                continue
            first = n.split()[0] if n else ""
            if first and any(r.split()[0] == first and len(first) >= 4 for r in reg_norms):
                out.append(Failure(o.id, f"{pe.name} shares its name with a regular", "offer something new, or say it is a sibling",
                                   place_id=pick.place_id))
        return out


def _price(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"\$?\s*(\d+(?:\.\d{1,2})?)", str(text))
    return float(m.group(1)) if m else None
