import { HourglassMedium } from "@phosphor-icons/react/dist/ssr";
import type { ReactNode } from "react";
import { FloorPlan } from "@/components/FloorPlan";
import { OutcomeMatrix } from "@/components/workspace/OutcomeMatrix";
import { formatInches, groupFindings } from "@/lib/findings";
import { scopedSummary } from "@/lib/outcomes";
import type { Assessment, Finding, Report } from "@/types/contracts";
import { type Fact, FactList } from "./FactList";
import { beingCheckedNames, splitQuestions } from "./reportCounts";
import { longDate } from "./reportDates";
import { WhatWeChecked } from "./WhatWeChecked";
import { Wordmark } from "./Wordmark";

function citation(finding: Finding) {
  const text = `${finding.citation.edition} ${finding.citation.section}`;
  return finding.citation.url ? <a href={finding.citation.url} className="underline decoration-rule underline-offset-2">{text}</a> : text;
}

function ProblemBlock({ finding }: { finding: Finding }) {
  const render = finding.locus?.render_url;
  return (
    <article className={`grid gap-5 break-inside-avoid border-t border-rule py-8 ${render ? "sm:grid-cols-[minmax(0,15rem)_1fr]" : ""}`}>
      {render && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={render} alt={`The spot where ${finding.title.toLowerCase()}`} className="aspect-[3/2] w-full rounded-lg bg-rule/40 object-cover" />
      )}
      <div>
        <div className="flex items-baseline justify-between gap-4">
          <h3 className="text-xl font-semibold leading-snug">{finding.title}</h3>
          {finding.measured_inches !== null && (
            <span className="measurement shrink-0 text-lg">{formatInches(finding.measured_inches)}</span>
          )}
        </div>
        <p className="mt-2 text-ink-muted">{finding.detail}</p>
        {finding.fix && <p className="mt-4 border-l-2 border-accent pl-3 font-medium">{finding.fix}</p>}
        <p className="mt-3 text-sm text-ink-muted">{citation(finding)}</p>
      </div>
    </article>
  );
}

function ProblemsSection({ problems }: { problems: Finding[] }) {
  if (problems.length === 0) return null;
  return (
    <section className="mt-12">
      <h2 className="heading-display text-2xl">What to fix</h2>
      <div className="mt-4">
        {problems.map((finding) => (
          <ProblemBlock key={finding.id} finding={finding} />
        ))}
      </div>
    </section>
  );
}

function StepsToSend({ toSend }: { toSend: Finding[] }) {
  if (toSend.length === 0) return null;
  return (
    <ol className="mt-4 flex list-decimal flex-col gap-4 pl-5">
      {toSend.map((finding) => (
        <li key={finding.id} className="break-inside-avoid">
          <p className="font-semibold">{finding.title}</p>
          <p className="text-ink-muted">{finding.detail}</p>
        </li>
      ))}
    </ol>
  );
}

function PhotosBeingChecked({ names }: { names: string[] }) {
  if (names.length === 0) return null;
  return (
    <div className="mt-8 break-inside-avoid">
      <h3 className="font-semibold">Photos being checked</h3>
      <ul className="mt-2 flex flex-col gap-1.5">
        {names.map((name, index) => (
          <li key={`${index}-${name}`} className="flex items-start gap-2">
            <HourglassMedium size={20} className="mt-0.5 shrink-0 text-attention" aria-hidden />
            {name}
          </li>
        ))}
      </ul>
    </div>
  );
}

function NextStepsSection({ toSend, beingChecked }: { toSend: Finding[]; beingChecked: string[] }) {
  if (toSend.length + beingChecked.length === 0) return null;
  return (
    <section className="mt-12">
      <h2 className="heading-display break-after-avoid text-2xl">Next steps</h2>
      <StepsToSend toSend={toSend} />
      <PhotosBeingChecked names={beingChecked} />
    </section>
  );
}

function WhatThisIsNot() {
  return (
    <section className="mt-12 break-inside-avoid border-t border-rule pt-8">
      <h2 className="heading-display text-2xl">What this report is not</h2>
      <div className="mt-3 flex flex-col gap-3 text-ink-muted">
        <p>
          Standard Physics measures what the scan could see and compares it against the 2010 ADA
          Standards for Accessible Design.
        </p>
        <p>
          This is not an inspection and it is not legal advice. Only a Certified Access Specialist
          can inspect your shop in person, and only their report carries legal weight. Fixing what
          is listed here first makes that inspection shorter and cheaper.
        </p>
      </div>
    </section>
  );
}

function PreviewNotice({ preview }: { preview: boolean }) {
  if (!preview) return null;
  return (
    <p className="mb-8 flex items-start gap-3 border-l-4 border-attention bg-attention/10 px-4 py-3">
      <HourglassMedium size={20} className="mt-0.5 shrink-0 text-attention" aria-hidden />
      This is a preview report. The rules in it are waiting for a person to review them.
    </p>
  );
}

function ScopedSection({ assessment }: { assessment: Assessment | null }) {
  const scope = assessment?.scope ?? null;
  if (scope === null) return null;
  const summary = scopedSummary(scope);
  return (
    <section className="mt-12 break-inside-avoid" aria-label="Scoped check outcomes">
      <h2 className="heading-display text-2xl">Scoped check outcomes</h2>
      {summary && <p className="mt-2 text-ink-muted">{summary}</p>}
      <div className="mt-4 border-t border-rule">
        <OutcomeMatrix scope={scope} findings={assessment?.findings ?? []} />
      </div>
    </section>
  );
}

function reportFacts(checkedOn: Date, problems: number, toSend: number, beingChecked: number): Fact[] {
  const facts: Fact[] = [
    { label: "Checked", value: longDate.format(checkedOn) },
    { label: "To fix", value: problems },
    { label: "To send", value: toSend },
  ];
  return beingChecked > 0 ? [...facts, { label: "Photos being checked", value: beingChecked }] : facts;
}

interface ReportDocumentProps {
  report: Report;
  /** The scoped-check matrix answers questions only the team asks. */
  showScope: boolean;
  /** Controls above the report, left out when it is printed. */
  toolbar: ReactNode;
}

/** The report an owner reads, prints and shares, the same wherever it opens. */
export function ReportDocument({ report, showScope, toolbar }: ReportDocumentProps) {
  const { scan, scene, scenario, assessment, rules } = report;
  const groups = groupFindings(assessment?.findings ?? []);
  const { toSend, beingChecked } = splitQuestions(groups.questions);
  const checkedOn = new Date(assessment?.created_at ?? scan.created_at);

  return (
    <main className="mx-auto max-w-3xl px-5 py-10 print:max-w-none print:p-0">
      <div className="mb-10 flex flex-wrap items-center justify-between gap-x-2 gap-y-3 print:hidden">{toolbar}</div>
      <Wordmark className="mb-6 hidden print:block" />

      <PreviewNotice preview={report.preview} />
      <header className="grid items-end gap-6 sm:grid-cols-[1fr_9rem]">
        <div>
          <h1 className="heading-display text-4xl">{scan.name}</h1>
          <FactList className="mt-4" facts={reportFacts(checkedOn, groups.problems.length, toSend.length, beingChecked.length)} />
        </div>
        {scene && <FloorPlan scene={scene} className="hidden aspect-square w-full sm:block" />}
      </header>

      <ProblemsSection problems={groups.problems} />
      {showScope && <ScopedSection assessment={assessment} />}
      <NextStepsSection toSend={toSend} beingChecked={beingCheckedNames(beingChecked, rules)} />
      <WhatWeChecked scenario={scenario} passes={groups.passes} rules={rules} preview={report.preview} />
      <WhatThisIsNot />
    </main>
  );
}
