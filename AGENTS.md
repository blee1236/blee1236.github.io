# AGENTS.md — Rules for working on this site

This repository is Bennett Lee's personal website (`blee1236.github.io`), published publicly via GitHub Pages.
Any AI agent or assistant editing this repo must follow these rules.

## 1. Never publish

These must never appear in any committed file, page, metadata, or commit message:

- **Phone number** in any page text or HTML (the downloadable CV PDF is the one approved exception — see below).
- Home address or any street-level location (city/state as listed on the CV is fine).
- Date of birth, ID numbers, or any government/financial identifiers.
- Passwords, API keys, tokens, or any `.env` contents.
- Compensation, salary, or offer details.
- Confidential or non-public employer information: internal fund holdings beyond what is on the CV,
  client names from Barclays, non-public research, model outputs, or PM discussions.
- Anything about other people (classmates, colleagues, references) without their consent.

**Approved exception (2026-11-05):** Bennett explicitly approved publishing the original CV PDF
(`Lee_Bennett_Resume_2026.pdf`), phone number included, as a download on the site. Only that file is
approved. Ask again before publishing any other or updated CV file.

## 2. Ask Bennett first before

- **Any `git push`, deploy, or publishing action** — nothing goes to GitHub without explicit approval.
- Creating commits, branches, or initializing/changing remotes.
- Adding information that is **not** on the CV (new roles, claims, numbers, photos, testimonials).
- Rewording CV facts in a way that changes their meaning (titles, dates, numbers, tickers, rankings).
- Adding third-party scripts, analytics, trackers, fonts from new CDNs, or contact forms.
- Deleting or overwriting existing files.
- Adding a photo, downloadable resume, or any new contact method.

## 3. Style

- **Vibe:** professional but casual — confident, plain-spoken, no buzzword filler.
- **Colors:** Columbia blue palette.
  - Columbia Blue `#B9D9EB` (primary accent / light surfaces)
  - Columbia Navy `#1D4F91` (headings, links, buttons)
  - Deep Navy `#0C2340` (text, nav background)
  - Off-white `#F7FAFC` (page background), white cards
- **Navigation:** streamlined — a single sticky top bar with anchor links to sections; no dropdowns
  or nested menus. Section tiles on the home page for quick jumps.
- **Content order:** must mirror the CV — Education → Experience → Additional Information.
- **Accuracy:** CV content is copied faithfully; do not inflate or invent.
- **Tech:** plain static HTML/CSS (minimal or no JavaScript), no build step, mobile-responsive,
  accessible (semantic HTML, sufficient color contrast, alt text, keyboard-navigable).
- **Links:** LinkedIn `https://www.linkedin.com/in/bennettlee1/`, GitHub `https://github.com/blee1236`,
  email `blee27@gsb.columbia.edu`.
