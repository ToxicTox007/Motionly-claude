# Music starter-set sign-off checklist (D3)

The import tool refuses any track without ALL of the checks below. This
checklist mirrors those checks so you can verify each candidate against
its own source page before signing off. **Import no real track until it
is signed off here.** Until then, code and tests use generated test
tracks only.

Gather at least **30** candidate tracks.

## Per-track checks (repeat for every candidate)

- [ ] `provider_track_id` is stable and unique
- [ ] `title` matches the source
- [ ] `audio_path` points at the uploaded private-bucket object
- [ ] at least one `mood_tag` from the fixed vocabulary
- [ ] `source_page_url` is the track's own http(s) page
- [ ] `license_id` + `license_version` are on the allow-list (CC0-1.0 or PD only)
- [ ] archived the licence page; `license_archive_sha256` is its SHA-256
- [ ] `rights_holder` recorded
- [ ] `composition_rights_status` = cleared (verified independently)
- [ ] `recording_rights_status` = cleared (verified independently)
- [ ] `verified_by` + `verified_at` recorded

## Mood coverage (every planner mood must have a fitting track)

Tick each mood once at least one signed-off candidate carries it:

- [ ] `ambient`
- [ ] `calm`
- [ ] `cinematic`
- [ ] `clean`
- [ ] `confident`
- [ ] `conversational`
- [ ] `dark`
- [ ] `dramatic`
- [ ] `energetic`
- [ ] `happy`
- [ ] `inspiring`
- [ ] `luxury`
- [ ] `minimal`
- [ ] `neutral`
- [ ] `playful`
- [ ] `podcast`
- [ ] `subtle`
- [ ] `uplifting`

## Final sign-off

- [ ] Every row above is checked
- [ ] Mood coverage is complete
- [ ] Every track in this set is individually signed off; only these tracks may be imported (the licence policy itself was approved at D3 on 2026-10-20)
