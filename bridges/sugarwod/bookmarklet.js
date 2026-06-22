// SugarWOD → Scientist bookmarklet (readable source).
//
// Runs in your already-logged-in Chrome on app.sugarwod.com calendar pages.
// Extracts the visible week's WODs from #cal-days, POSTs JSON to the bridge.
//
// To install: open bookmarklet_minified.js (the one-line version), copy
// its contents into a new Chrome bookmark's URL field. Then click the
// bookmark while on a SugarWOD weekly calendar page.

(async () => {
  const calDays = document.querySelectorAll('#cal-days .cal-day');
  if (!calDays.length) {
    alert(
      'No SugarWOD calendar found.\n\n' +
      'Make sure you\'re on the weekly view at app.sugarwod.com.'
    );
    return;
  }

  const days = [];
  for (const el of calDays) {
    const header = el.querySelector('.cal-day-header-title')?.innerText.trim() || '';
    const dateInt = el.dataset.dateint || '';
    const workouts = [];
    for (const w of el.querySelectorAll('.cal-workout')) {
      const title = w.querySelector('.cal-workout-title')?.innerText.trim() || '';
      const description =
        w.querySelector('.cal-workout-description')?.innerText.trim() || '';
      if (title || description) workouts.push({ title, description });
    }
    days.push({ date_int: dateInt, header, workouts });
  }

  const url = new URL(location.href);
  const week = url.searchParams.get('week') || days[0]?.date_int || 'unknown';

  const payload = {
    url: location.href,
    week_start: week,
    fetched_at: new Date().toISOString(),
    days,
  };

  try {
    const r = await fetch('http://localhost:8765/sugarwod/week', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    alert(`✓ Sent week ${j.week_start} — ${j.days} days, ${j.workouts} workouts.`);
  } catch (e) {
    if (confirm(
      `POST to localhost:8765 failed: ${e.message}\n\n` +
      `Is the bridge server running?\n\nCopy JSON to clipboard as fallback?`
    )) {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      alert('Copied JSON to clipboard.');
    }
  }
})();
