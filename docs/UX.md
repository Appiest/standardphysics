# The owner's journey

Read this before you touch a screen an owner sees. It is the spec for the redesign Brendan approved on Sep 26, 2026. The full plan, with the audit of today's app and every source, is the "Standard Physics UX redesign plan" doc.

The first run is one guided path from opening the app to a checklist of fixes. Everything else stays hidden until the owner's first results appear. The iPhone app owns every step that uses the camera. standardphysics.app owns reading, deciding and sharing, through a phone-first owner view that the app loads in an embedded mode.

## The six stages

Every shop is in exactly one stage, and every screen shows the one next step for it. The server derives the stage from what is done, so no screen has to guess.

| Stage | Done when | Where |
| --- | --- | --- |
| Set up | The owner taps "I'm ready" on the checklist | iPhone app |
| Walk | The owner taps Done and the upload starts | iPhone app |
| Fill in the gaps | Every quick answer and photo is answered or skipped | iPhone app, then the web for follow-ups |
| Results | The counter and the customer path are confirmed and the results are showing | Web, in the app |
| Fix | Every checklist item is Done, Not doing or Needs a pro | Web, in the app |
| Tools | Always open once the results first appear | Web, and the iPhone app for adding a room |

Shop tools unlock when the results first appear. That is when the first report counts as done, so a finding can open Plan a layout right away.

## Screens

The copy is draft. It follows section 2 of [`PLAN.md`](PLAN.md) and is there to be rewritten.

| # | Screen | What it says | The one action | Where |
| --- | --- | --- | --- | --- |
| 1 | Welcome | "See what gets in the way of customers in your shop, and how to fix it." A short loop of a room drawing itself, and "See an example shop". | Get started | iPhone app |
| 2 | Before you walk | A checklist: lights on, customer doors open, aisles clear, about 3 minutes. | I'm ready | iPhone app |
| 3 | Camera | "Standard Physics measures your shop with the camera." One button, which opens the system alert. | Continue | iPhone app |
| 4 | The walk | One instruction at a time and one progress cue. A haptic tap when a wall is done, a warning 30 seconds before the limit, and "That's everything we need" when it's enough. The upload starts on its own under a guest account. | Done | iPhone app |
| 5 | Quick answers | "Do customers use a restroom?" and "Do customers go through any doors inside the shop, like a restroom door?" Yes or no for each. | Tap an answer | iPhone app |
| 6 | Quick photos | "While it uploads, a few quick photos." One per screen with a sample picture: the front doorway from the side, the door handle, the floor inside the door, and the restroom from its doorway if customers use one. | Take photo | iPhone app |
| 7 | Door push | Only if there are inside doors: "How hard are the inside doors to push open? A door pressure gauge from a hardware store measures it." A field for pounds, or "Skip for now", which leaves it open in the report. | Save | iPhone app |
| 8 | Measuring | "Measuring your shop. This takes a few minutes." Then "Save your shop while you wait, and we'll let you know when it's ready." Sign in with Apple, email, or "Not now". The next prompt offers notifications either way. | Continue with Apple | iPhone app |
| 9 | Two checks | "Tap where customers pay or check in." Then "Where else do customers go?" with choices like seating or a fitting room, plus the restroom if there is one, and "This is the path customers take. Drag a stop if it's wrong." | Looks right | Web, in the app |
| 10 | Follow-ups | Only when a check needs one. "Measure the front doorway and send us the number" gets a field for inches. Shaky measurements show as "Needs another look", and the next walk of the shop clears them. | Send | Web, in the app |
| 11 | Results | "3 things to fix." Each card shows a picture of the spot, one sentence, the measurement beside the required number, and what to do. Tapping a card flies the 3D model to that spot. "See a layout that fixes this" opens Plan a layout when moving furniture would do it. | Start fixing | Web, in the app |
| 12 | Checklist | Each item is To do, Done, Not doing or Needs a pro, and the header counts "1 of 3 done". An owner who skipped saving is asked again at the first check-off. | Mark done | Web, in the app |
| 13 | Share | "Share your report" as a link or a PDF for a contractor, landlord or inspector. | Share | Web |
| 14 | Shop tools | A one-time card, "You unlocked shop tools." Planning a layout and the wheelchair walk-through open on the web. Adding a room opens a walk in the iPhone app that joins the same shop. Each tool opens with one guided task. | Try it | Web, and the iPhone app for adding a room |

Screens 5 to 7 cover what the server always asks at rule tier 1: four photos and a push-force reading on inside doors. A "no" in screen 5 closes whatever depends on it as not applicable. Screen 10 covers requests a check makes only when it needs one.

After setup, the app's home is one next-step card chosen by the shop's stage, like "Take 2 more photos", "Your results are ready" or "1 of 3 done. Next: move the display case." Once the account is saved, shops come from the account instead of the phone, so a new phone still finds them.

## First-run rules

- At most three screens stand between opening the app and the camera: welcome, the checklist and the camera screen.
- Sign-in never blocks anything. It's offered during the measuring wait, and an owner who skips it is asked again at the first check-off or share.
- The camera is requested on the screen right before the walk, with a single Continue button that opens the system alert.
- Notifications are requested only during the measuring wait, right after the save prompt.
- A skipped tip never comes back on its own, but "How it works" in settings always has it.

Teach by doing. The first 20 seconds of the first walk coach in place ("Point at the wall in front of you"). Each photo request carries one line from copy the server already writes. The first results show a one-time card: "Red means it misses the ADA number. Tap one to see where it is." Each shop tool opens with one guided task, like "Drag the display case toward the wall and watch the finding turn green."

## Owner tools and team tools

Owners get Plan a layout, the wheelchair walk-through and adding another room. Ask, the improvement loop, the simulation, scoped checks, replays and the rebuilt-from-photos view are team-only, gated by account role. The builders' workspace stays for the team.

## The bridge between app and web

The web owner view runs inside the app's web view on a phone and in a browser on a laptop. Inside the app it posts these messages, and each carries ids, never tokens:

| Message | What the app does |
| --- | --- |
| `takePhoto(requestId)` | Opens the camera for one request and uploads the photo against it |
| `addRoom(shopId)` | Starts a walk that joins the same shop. Walking the shop again to clear "Needs another look" uses it too |
| `saveReport()` | Opens the native Sign in with Apple sheet, for owners who skipped saving during the wait |
| `share(url)` | Opens the iOS share sheet for the report link |
| `openLink(url)` | Opens a rule citation in Safari |
| `stageChanged(shopId, stage)` | Updates the app's next-step home card |

## What stays true

- The report keeps "This is not an inspection and it is not legal advice." It is the one deliberate exception to "only describe what is" in section 2 of the plan.
- Never-saved guest shops are deleted 30 days after the scan was last opened. The owner gets a reminder 3 days before, as a push or as a banner in the app.
- A layout the owner plans is saved as a plan. The report keeps describing the shop as scanned.
- Re-scanning a single spot and finding a pro are on the wish list, not planned.

## How it's built

The wire shapes live in `packages/contracts/standardphysics_contracts/owner.py`, and the web's types are generated from them. Every route below answers with those shapes. The phone sends its token as a bearer header and the web view sends the same token as the `sp_session` cookie, as today.

### Accounts

| Route | What it does |
| --- | --- |
| `POST /api/auth/guest` | Makes a guest account and signs it in. The phone calls it on first launch, so the walk can upload with no sign-in. Answers `Session` and sets the cookie. One network can make 20 an hour. |
| `POST /api/auth/save` | `{email, password}`. Turns the signed-in guest into a saved account. 409 when the email already has an account, and then the phone signs in instead. |
| `POST /api/auth/apple` | `{identity_token, full_name?}`. Signs in with Apple. A guest who signs in keeps their shops: they move into the Apple account, or the guest becomes it. |
| `POST /api/auth/sign-in` | As today. When the caller is a guest, the guest's shops move into the account they sign in to. |
| `GET /api/auth/session` | `Session`, now with `guest`, `role` and `deletes_at`. |
| `PUT /api/devices/{apns_token}` | `{environment}`. Registers the phone for notifications. `DELETE` removes it. |

A new walk of a shop, to add a room or to walk it again, is `POST /api/scans` with `replaces` set to the shop's scan. The new scan starts with the owner's in-shop answers and photos, and the old one leaves the shop list once the new one is ready. Each walk is its own scan because each capture has its own coordinates.

A guest's shops are deleted 30 days after any of them was last opened. The reminder goes 3 days before, as a push, and `Session.deletes_at` lets the app and the web show it as a banner.

### A shop's journey

| Route | What it does |
| --- | --- |
| `GET /api/journeys` | Every shop the account has, each with its stage and next step. The app's home card reads this. |
| `GET /api/scans/{id}/journey` | One shop's `Journey`. |
| `GET /api/scans/{id}/requests` | Every request for the shop, in the order to ask them. The in-shop ones exist as soon as the scan does. |
| `PUT /api/scans/{id}/requests/{request_id}/answer` | `{yes}` or `{number}`. A "no" to the restroom or inside-door question closes the requests that depend on it as not applicable. |
| `PUT /api/scans/{id}/requests/{request_id}/photo` | A JPEG or PNG body, at most 15 MB. The photo waits for a person on the team to check it. |
| `POST /api/scans/{id}/requests/{request_id}/skip` | Stops asking. The request stays open in the report. |
| `GET /api/scans/{id}/requests/{request_id}/photo` | The photo that was sent. |
| `GET /api/scans/{id}/checklist` | One item per thing to fix, with its status. |
| `PUT /api/scans/{id}/checklist/{finding_id}` | `{status}`: `to_do`, `done`, `not_doing` or `needs_pro`. |
| `GET /api/scans/{id}/scenario/suggestion?destinations=seating,restroom` | A customer path for any business: in the front door, to the counter, to each place the owner picked, and out. |
| `POST /api/scans/{id}/shares` | Makes a read-only report link that expires in 30 days. |
| `GET /api/shared/{token}` | The report behind a link, with no sign-in. |
| `POST /api/scans/{id}/plans` | Saves a planned layout and checks it. The shop as scanned doesn't change. `GET` lists the plans and `DELETE /plans/{plan_id}` removes one. |

Answers change findings as soon as they arrive. A photo that's been sent and not yet checked stays a question with `asks: "review"`. A doorway width or a door's push force is checked against the rule straight away. A photo becomes a result when a person on the team marks it passes or problem through `GET /api/team/reviews` and `PUT /api/team/reviews/{scan_id}/{request_id}`, which only team accounts can reach.

### On the web

| Route | Who | What it is |
| --- | --- | --- |
| `/shops/{id}` | Owners | The owner view: one step at a time, then the results and checklist as one list, sharing and the shop tools |
| `/example` | Anyone | The sample shop in the owner view, read-only |
| `/r/{token}` | Anyone with the link | A shared report, printable to PDF |
| `/` | Everyone signed in | The shops, each with its next step. Owners go to `/shops/{id}`, the team to the workspace |
| `/scans/{id}` | The team | The builders' workspace |
| `/team/reviews` | The team | Photos waiting to be checked |
| `/team/funnel` | The team | How many shops reach each step, from the walk to the first fix |

The team is whoever `SP_TEAM_EMAILS` names. Once it names anyone, the ask box, the improvement loop, simulations, rebuilds and combining rooms answer only to the team.

### The bridge

The app adds one message handler, `standardPhysics`, and keeps `nativeCapture` for the old home page. Each message is a JSON object with a `type`:

| `type` | Other fields | What the app does |
| --- | --- | --- |
| `takePhoto` | `requestId` | Opens the camera for that request, uploads the photo, then calls `window.standardPhysics.photoSent(requestId)` |
| `addRoom` | `scanId` | Starts a walk that joins the same shop |
| `saveReport` | none | Opens Sign in with Apple, then reloads the page |
| `share` | `url`, `title` | Opens the share sheet for the link, with a PDF of the page beside it |
| `openLink` | `url` | Opens the link in Safari |
| `stageChanged` | `scanId`, `stage` | Refreshes the home card |

The app's web view adds `StandardPhysicsApp/<build>` to its user agent. The web reads it to render the owner view without the site's own chrome, so the page never flashes the laptop layout first.
