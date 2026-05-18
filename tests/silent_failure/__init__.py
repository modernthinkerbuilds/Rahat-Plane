"""Silent-failure guard.

For every registered agent, for every canonical-shape query, assert
that agent.route(msg) returns a Reply where:
  - text is non-empty after .strip()
  - text does NOT match any STUB_PATTERN
  - text does NOT match any of the "I don't know" rabbit-hole shapes
    that look like answers but aren't

This layer catches the worst class of regression: the bot returns
something to Telegram that LOOKS like a reply but is actually a
stub. The user only learns about it from silence or from realizing
"that didn't answer my question."

If a stub leaks here, it leaks to production.
"""
