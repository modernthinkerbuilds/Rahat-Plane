# Deploying your GitHub Profile

Two files to publish, two destinations. ~10 minutes total.

---

## 1. Profile README (`README-PROFILE.md`)

This shows up on `github.com/modernthinkerbuilds` as the page header.

GitHub uses a special "magic" repo: a public repo with the **same name as your username**, containing a `README.md` at the root.

### Steps

```bash
# Create the magic repo
cd ~/developer
mkdir modernthinkerbuilds && cd modernthinkerbuilds
git init

# Copy the profile readme in
cp ~/developer/agency/rahat/profile/README-PROFILE.md ./README.md

# First commit
git add README.md
git commit -m "feat: initialize profile readme"

# Create the repo on GitHub (use gh CLI or do it in browser)
gh repo create modernthinkerbuilds/modernthinkerbuilds --public --source=. --push

# OR if you don't have gh installed:
# 1. Go to github.com/new
# 2. Repo name: modernthinkerbuilds (must match your username exactly)
# 3. Public, no README (you've already got one)
# 4. Then locally:
#    git remote add origin https://github.com/modernthinkerbuilds/modernthinkerbuilds.git
#    git branch -M main
#    git push -u origin main
```

Refresh `github.com/modernthinkerbuilds` — the README will now render at the top of your profile.

No placeholders to replace — the file is ready to push as-is.

---

## 2. Rahat-Plane Repo README (`README-RAHAT-REPO.md`)

This replaces the current OpenClaw README on your [`Rahat-Plane`](https://github.com/modernthinkerbuilds/Rahat-Plane) repo (currently a fork of OpenClaw's README — not your story).

```bash
# In your existing Rahat-Plane clone on disk
cd ~/developer/agency/rahat/staging   # or wherever the local clone lives

# Back up the OpenClaw README (you may want it for reference)
mv README.md README-OPENCLAW.md

# Copy the new Rahat README in
cp ~/developer/agency/rahat/profile/README-RAHAT-REPO.md ./README.md

# Commit
git add README.md README-OPENCLAW.md
git commit -m "docs: replace upstream readme with Rahat positioning"
git push origin main
```

No placeholders to replace — file is ready to push as-is.

---

## 3. Pin the right repos on your profile

Per Aakash's guidance: **two strong pinned repos > a wall of half-finished ones.**

Recommended pins for `github.com/modernthinkerbuilds`:

1. **`Rahat-Plane`** — flagship
2. **One specs/writing repo** — if you start a public PRDs/essays repo, that's pin #2
3. *(optional)* Anything else that demonstrates range — but don't pad

To pin: profile page → "Customize your pins" → check the 1-2 repos that matter.

---

## 4. Profile polish (5-min wins)

These compound. Do them once, they pay off forever.

- [ ] **Profile photo:** Clean, professional, recent. Not a wedding photo, not a vacation selfie. The screenshot Aakash showed has Shubham at a clean white background — that's the standard.
- [ ] **Name field:** Your real name, no clever handles in the display name
- [ ] **Bio (160 chars):** Try: *"PM by day, agent builder by night. Building Rahat — a sovereign runtime for personal AI agents."*
- [ ] **Location:** Bay Area, CA
- [ ] **Website:** Link to the `Rahat-Plane` repo (or skip — leave blank)
- [ ] **Pronouns:** Optional but adds humanity
- [ ] **Achievement badges:** These show up automatically as you commit, open PRs, etc. Don't fake them.

---

## 5. The first 30 days of activity (the "green squares")

Aakash's point: **regular > frequent**, **meaningful > regular**.

You don't need 365 green days. You need:

- A clear streak of meaningful commits (build journal entries, PRD revisions, agent persona iterations)
- One pull request per week (even on your own repos — practice the discipline of PRs over direct-to-main)
- A few discussions/issues on your own repo as you talk through architecture trade-offs publicly

The goal isn't to game the chart. It's to make the chart honest evidence that you ship.

---

## 6. What goes in `/specs/PRD.md` next

The Rahat repo README references `/specs/PRD.md`. You already have most of this from the v2.0/v2.1 PRD work — paste the latest version into `staging/specs/PRD.md` and commit. That gives every visitor a place to dig deeper.

```bash
mkdir -p staging/specs
# Paste your latest PRD content into staging/specs/PRD.md
git add staging/specs/PRD.md
git commit -m "docs: add Rahat v2.1 PRD"
git push
```

---

## What to do *after* this is live

1. **Open the first GitHub Discussion** on the `Rahat-Plane` repo — title it "The Architecture: why a control plane, why local-first." That's your Month 1 anchor post, in the right venue.
2. **Start the 90-day series in-repo** — Month 1 is *Architecture*. Pick one of the three pillars (Sovereignty / State Continuity / Extensibility) and write the first deep-dive as a Discussion or a `/specs/` essay.
3. **Stay honest** — when something fails (the 429 quota wall, the Keychron pairing loop, the Apple Watch CSV that had to be manual for 3 weeks), commit it. The messy middle is what makes the brand stick. Use commit messages and Discussions as your build journal.
