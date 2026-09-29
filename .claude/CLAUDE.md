Shared UI and design guidance for everyone working on this repo. Claude Code picks this up automatically.

When making code changes, don't write summary markdown files or create copies of files (like .backup or .pre-features); I have Git and don't need them.

Commit to git whenever a feature is finished and verified (typecheck + tests pass), without waiting to be asked. One commit per feature, message describing the change. Use the default git user. Branch first if on the default branch.

master is the only shared branch. Pull master before you start, branch from it for your own work, and merge that branch back into master when it's done. Don't build on someone else's branch, and don't start a new long-lived integration branch; if two branches need combining, merge each into master instead.

Always use tools to understand problems better; avoid guessing or manufacturing data/statistics.

When building UIs and apps:
- default to React or Next (where applicable) with TypeScript and TailwindCSS. Do not use vanilla CSS unless absolutely necessary.
- use Phosphor Icons (npm: @phosphor-icons/react) and avoid emojis.
- make professional, well-designed UIs (no blurple). They should look good while staying easy to understand and skim.
- write copy for the website in approachable human terms. Sound agreeable and conversational.
- design the user experience to be frictionless, and avoid overwhelming the user.
- never use gradient text (bg-clip-text + text-transparent or similar). It reads as AI slop.
- never combine rounded corners with a visible-color border on the same element. The border anti-aliases unevenly at the corners and looks cut off. Use shadow, ring, or a fill instead — keep the visible border for sharp-cornered elements only. Subtle inputs (form fields) are the lone exception where a thin neutral border on a rounded input is acceptable.

I value code that explains itself through clear class, method, and variable names. Comments may be used when necessary to explain some tricky logic, but should otherwise be avoided.

Passing the project's cyclomatic complexity lint is a hard requirement, not a nice-to-have — treat a complexity failure exactly like a type error or a failing test. Where the project has no such rule, hold to the same bar anyway: pull branchy logic into named functions, replace nested conditionals with early returns or a lookup, and split a function that has grown more than one job. This applies to all code, not just UI.

## Typography

- Never use Lora, or anything Lora-adjacent, for display or headings. That whole bracketed-serif, moderate-contrast, blog-default lane reads as a template.
- Never use Instrument Serif, or anything like it, anywhere: the high-contrast italic display serif used as a "lyrical accent" (Instrument Serif, Playfair Display, DM Serif Display, Cormorant, Fraunces italic, Libre Caslon Display, Bodoni Moda, Italiana) is AI-signature slop and hard to read. Assistant or quoted text gets the same family as body copy, distinguished by weight, surface, or layout, never by an italic serif.
- Research Google Fonts before picking type for an app. Look at what's actually there for the direction at hand instead of reaching for the same four families; check weights, optical sizes, and whether a variable version exists.
- NEVER set text in all-caps unless the content is genuinely uppercase: airport codes, 2FA codes, ticker symbols, acronyms. Not for labels, buttons, nav, eyebrows, or "emphasis."
- Don't touch letter-spacing. If type looks wrong at a size, the size or the family is wrong.
- The letter-spaced uppercase micro-label is banned outright, including when a skill, a design system, or a reference recommends it. Small labels stay sentence case at a size people can actually read.
- Monospace is for content that is genuinely code, data, or figures that align in a column: a command, a hash, a file path, a table of numbers. Never as a costume — section numbers, bylines, credits, nav, captions, and headings set in mono to make a page feel technical read as AI slop. If a label needs to feel secondary, use the body family smaller or lighter.
- Use the scale. `text-sm`, not `text-[9px]`. Arbitrary values are a last resort with a reason, not a default.
- Text styles are defined once, in the stylesheet or theme layer. See **Define it once**.

## Define it once

Anything that appears more than once in an interface gets a single definition and is referenced from there — never restated at each call site. That covers:

- **Type** — `.body {}`, a heading style, a token. Not per-element font declarations.
- **Color** — semantic tokens (`--surface`, `--text-muted`, `--accent`) defined in one place. Not raw hex or a Tailwind color scattered through components.
- **Elements** — a button is a `Button` component with variants, not markup copy-pasted with slightly different padding each time. Same for inputs, cards, badges, dialogs, empty states.
- **Spacing, radii, shadows, motion** — scales and easing curves live in the theme.

When a design changes, it should change in one file. If a change means touching many files, the abstraction is missing. Read the project's existing stylesheet, theme, or component library first and extend it rather than starting a parallel one.

## Layout and UI

- No eyebrows. The small label floating above a heading adds a line and says nothing.
- No short decorative horizontal rules — the little 40px accent line under a heading, the divider used as ornament.
- No decorative section numbers. `01` in an accent color parked beside a heading is an eyebrow wearing a number. Number sections only when the reader needs the ordinal to find or cite the thing, and then set the number in the heading's own family as part of the heading, not floating in a margin.
- No middot metadata strips. `Thing · Category   Person · Place · Year` crammed into one small line is a pile of facts impersonating a sentence. Facts that matter get a key/value block or a real sentence; the rest get cut. The same goes for any run of unrelated items chained with `·`, `—`, or `/` to look like a caption.
- No UI tricolons: the reflexive row of three feature cards, three stats, three steps. If there are four things, show four; if there are two, show two.
- Avoid colored icons sitting on colored rounded squares as a layout device — the pastel-tile-grid look. Fine on actual controls (a delete icon on a button); not fine as the visual system of a page.
- No mystery controls. A button, chip, or link must say what it does before it is tapped ("Enroll your voice", not "Your voice"). If the label needs the tap to explain it, the control is poorly designed.
- A hot, saturated accent (orange, red, acid) is light, not paint: use it for glow, motion, and small marks, never as the fill of primary buttons or chips. Controls stay in the neutral ramp with one calm accent.
- Chat and transcript views need real hierarchy: a speaker and their words read as one unit, and an assistant or bot turn sits on its own distinct surface (Slackbot-style), never styled as if a human said it.
- Text is the last resort, not the first. Before writing a label, a caption, or a sentence of status copy, ask whether size, weight, color, position, a container, an icon, or a small diagram could carry the same fact. If it can, build that instead. A label that names a region ("Album artwork", "Playback controls", "Text formatting controls") is almost always the model describing the UI rather than making it.
- One heading per section, naming the thing: "Artemis II crew", not "Four explorers. One shared horizon." followed by a paragraph restating it. Drop the supporting paragraph under a heading unless it carries a fact the heading doesn't. Drop image captions that repeat the alt context. A section rail of numbers and all-caps meta strings ("04 — THE PEOPLE") is an eyebrow by another name.
- A heading names one thing, so it needs no comma. "aTokens, and the interest stream you can give away" and "The stable rate, and why it cannot be a fixed rate" are two headings wearing one hat: either the section covers one subject and the tail is padding, or it covers two and the second deserves its own heading. The same goes for a comma-separated list of everything the section mentions ("Reserves, total liquidity, and the utilization rate"), which describes the section's contents instead of naming its subject.
- Prefer a diagram, a table, or a marked-up state over prose that says the same thing. A trajectory drawn is better than a trajectory described; `Launched · 16 Nov 2022` as a key/value row is better than a sentence containing the date. Detail that genuinely doesn't fit goes behind a disclosure — an info affordance, an accordion — not into body copy.
- Hierarchy: make the most important element visually dominant through size, color, and contrast, and actively demote everything else. If every element sits in an identically sized box at the same weight, nothing is the subject and the eye has no order to follow.
- Gestalt: show grouping with proximity, a shared container, alignment, and similarity rather than announcing it with a heading. Two toolbars separated by a gap need no "Text formatting" / "Alignment" labels above them. Separate unrelated controls the same way, so relationships are visible before anything is read.
- State: every meaningful state gets a visible change — shape, color, position, motion, or contrast. Selected is a ring and a check, locked is a hatch and a padlock, disabled is real contrast loss. "Status: Not selected" as text is a bug.

This is about the reflex, not an absolute. Documentation, dense data tools, and anything that must be searchable or screen-read will legitimately carry more text — and accessible names, alt text, and labels for assistive tech are never the thing being cut. The rule is that visible prose should be doing work no visual could do.

## Ambition

Every UI should look like a screenshot from a design textbook, a video game, or a designer's portfolio. Be bold and take on the big version of the task.

- Use real imagery. Generate it (Codex image generation), or model it — open Blender or Inkscape and build the thing you need. On my site we modeled a whale from published data, shaded it, and made it a design element; that is the bar.
- Use animation and interactivity. Verify animation frame by frame, not by assuming the CSS is right.
- Detail is not optional and never gets skimmed: grain, texture, edge treatment, weight.

References worth stealing from:
- `~/.claude/references/hermeus-hero.png` — Hermeus. Grey and bright orange, nothing else. Headline sits *behind* the engine and takes an animated heat distortion from the exhaust. Framing does the work.
- sixmorevodka — animation, boldness, and detail carried all the way through.
- Fortiche — a symbol or motif recurring across the whole site, the way strong writers and photographers repeat an image.
- The mad-whales piece on my site — data-driven model turned into a design element.

When the design direction is genuinely unclear, quiz me before building. Ask about direction and references; don't ask permission to be ambitious.

## Writing

Copy stays approachable, human, and conversational (see above). Beyond that:

- No paraprosdokians — the sentence that turns on its second half for a wink.
- No rising tricolons, and no three-beat rhythms generally.
- No parcellation, meaning a fragment punctuated as if it were a sentence so that it lands as a dramatic beat. Every sentence needs a subject and a verb. It shows up in two shapes:
  - A verbless phrase standing alone. "Embedded ML, close to the metal." has no verb; write "We build embedded ML that runs close to the metal."
  - A real sentence followed by a fragment or short clause that restates it with more flourish. "A curve stops being the graph of something. It becomes a trip." says one thing twice; write the one sentence that states the fact: "A parametric curve gives x and y as separate functions of t, so it can cross itself."
- Explain, don't evoke. A metaphor may sit next to a definition but never replace one. If a reader can finish the paragraph without learning what the thing is, what it does, or what changed, it isn't written yet.
- Length is not the enemy; vagueness is. Don't compress an explanation into an elegant summary that only makes sense to someone who already understands it. Write for a reader who is capable, has the stated prerequisites, and takes in information slowly: name each new term where it first appears, show a concrete instance rather than only the general case, and give the reason a thing works the way it does, not just the statement that it does. Cut words that carry no information, never the ones carrying the explanation.
- Explanations are carried by pictures and text together, and a wall of either one is a failure. Before writing a third paragraph, ask what a diagram, a labelled screenshot, a worked example, a before/after pair, or a small interactive control would say better, and build that instead. When both appear, they should divide the work rather than repeat it: the visual shows the structure or the change, the prose says what it means and why it matters.
- No bolted-on apposition, which is a trailing phrase that renames or glosses what came before it instead of saying something new. It attaches with an em dash ("…under real hardware constraints — FPGA acceleration for high-throughput, low-latency workloads."), a colon, or a comma ("Where the arc length formula comes from: one small step, measured by Pythagoras."), and the tell is that the tail has no verb of its own. Either fold it into the sentence or give it a subject and a verb and let it be a sentence.
- No evaluative tags. A trailing clause that comments on the sentence rather than adding to it is filler even when it is grammatically complete: "…and that is deliberate", "…which is important", "…and that's the point", "…which is what makes it work". It tells the reader how to feel about a fact instead of giving them another one. Either say what the tag is gesturing at (why it is deliberate, what it is for) or end the sentence at the fact.
- Don't hang the point of a sentence off its end. If what the reader most needs sits after a comma in a trailing modifier — an appositive, an "-ing" or "-ed" phrase, or a "which" clause — it arrives as an afterthought and is easy to skim past. Promote it to a main clause, or split into two sentences that each have a subject and a verb. Test every tail: if deleting it loses nothing, cut it; if deleting it loses the point, it deserved a sentence of its own. Watch especially for a sentence carrying three comma-separated chunks, where the reader cannot tell whether the third one continues a list or starts a new thought.
- A caption, tooltip, helper line, empty state, or alt-adjacent label has to carry a fact the reader cannot get from the thing it sits beside. Naming what they are already looking at is not information: "Where the arc length formula comes from", "Playback controls", "Your team's activity". Say the thing itself instead — what the picture shows, what the number means, what to do next, what changed. The test is to cover the image or the control: if the words still tell you something specific, keep them; if not, they were decoration and should be cut or rewritten.

## Interface skills (always on)

Whenever you write or change UI — components, pages, layouts, styles, product copy, icons, or motion — load the relevant `better-*` skills before you write the code, not after. Do this automatically; I should never have to ask.

- `better-ui` — visual polish, concentric radii, optical alignment, surfaces, icons, animation, hit areas
- `better-typography` — type scale, spacing, wrapping, truncation, variable fonts, OpenType
- `better-colors` — palettes, semantic tokens, contrast
- `better-layout` — grouping, alignment, reading order, progressive disclosure
- `better-accessibility` — semantics, focus, keyboard, screen readers, forms
- `better-writing` — product copy, labels, empty states, errors
- `better-interface` — load this one instead when the work spans several of the above, or when reviewing a whole screen or flow

Load the two or three that actually apply to the change; load `better-interface` for anything screen-sized or larger. Their rules sit under the design preferences above — where a skill conflicts with something I've stated here, mine wins, and say so rather than silently picking one. This covers every skill, not just the `better-*` ones: `artifact-design`, `frontend-design`, `dataviz` and anything else that arrives with the session are subordinate to this file.

`interface-review`, `break`, and `variant` are mine to invoke; don't run them on your own.
