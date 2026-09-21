import { ArrowLeft } from "@phosphor-icons/react/dist/ssr";
import Link from "next/link";
import { notFound } from "next/navigation";
import { FloorPlan } from "@/components/FloorPlan";
import { PrintButton } from "@/components/PrintButton";
import { getReport } from "@/lib/api";
import { formatInches, groupFindings } from "@/lib/findings";
import type { Finding, ReviewedRule, Scenario } from "@/types/contracts";
import { requireSession } from "@/lib/session";

export const dynamic = "force-dynamic";

const longDate = new Intl.DateTimeFormat("en-US", { month: "long", day: "numeric", year: "numeric" });

function citation(finding: Finding) {
  const text = `${finding.citation.edition} ${finding.citation.section}`;
  return finding.citation.url ? <a href={finding.citation.url} className="underline decoration-rule underline-offset-2">{text}</a> : text;
}

function ProblemBlock({ finding }: { finding: Finding }) {
  const render = finding.locus?.render_url;
  return (
    <article className="grid gap-5 break-inside-avoid border-t border-rule py-8 sm:grid-cols-[minmax(0,15rem)_1fr]">
      {render ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={render} alt={`The spot where ${finding.title.toLowerCase()}`} className="aspect-[3/2] w-full rounded-lg bg-rule/40 object-cover" />
      ) : (
        <div className="hidden sm:block" />
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
        <p className="mt-3 text-sm text-ink-faint">{citation(finding)}</p>
      </div>
    </article>
  );
}

function legs(scenario: Scenario | null): string[] {
  if (!scenario) return [];
  return scenario.stops.slice(1).map((stop, index) => `${scenario.stops[index].name} to ${stop.name}`);
}

function threshold(rule: ReviewedRule): string {
  const { threshold: value, unit } = rule.check;
  return unit === "in" ? formatInches(value) : `${value} ${unit}`;
}

function RulesTable({ rules }: { rules: ReviewedRule[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-ink-muted">
          <tr>
            <th className="py-2 pr-4 font-semibold">Section</th>
            <th className="py-2 pr-4 font-semibold">Check</th>
            <th className="py-2 pr-4 font-semibold">Standard</th>
            <th className="py-2 font-semibold">Reviewed by</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => (
            <tr key={rule.check.id} className="border-t border-rule align-top">
              <td className="measurement whitespace-nowrap py-2 pr-4">{rule.check.citation.section}</td>
              <td className="py-2 pr-4">{rule.check.title}</td>
              <td className="measurement whitespace-nowrap py-2 pr-4">{threshold(rule)}</td>
              <td className="py-2">
                {rule.verified_by}, {longDate.format(new Date(rule.verified_at))}
                {rule.second_check_by && <span className="block text-ink-muted">Second check: {rule.second_check_by}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WhatWeChecked({ scenario, passes, rules }: { scenario: Scenario | null; passes: Finding[]; rules: ReviewedRule[] }) {
  const measured = legs(scenario);
  return (
    <section className="mt-12 break-inside-avoid">
      <h2 className="heading-display text-2xl">What we checked</h2>
      {measured.length > 0 && (
        <>
          <h3 className="mt-6 font-semibold">Paths measured</h3>
          <ul className="mt-2 flex flex-wrap gap-2">
            {measured.map((leg) => (
              <li key={leg} className="rounded-md bg-rule/50 px-2.5 py-1 text-sm">{leg}</li>
            ))}
          </ul>
        </>
      )}
      {passes.length > 0 && (
        <>
          <h3 className="mt-6 font-semibold">What passes</h3>
          <ul className="mt-2 flex flex-col gap-1">
            {passes.map((finding) => (
              <li key={finding.id}>{finding.title}</li>
            ))}
          </ul>
        </>
      )}
      {rules.length > 0 && (
        <>
          <h3 className="mt-6 font-semibold">Rules and who reviewed them</h3>
          <div className="mt-2">
            <RulesTable rules={rules} />
          </div>
        </>
      )}
    </section>
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

function NextStepsSection({ questions }: { questions: Finding[] }) {
  if (questions.length === 0) return null;
  return (
    <section className="mt-12 break-inside-avoid">
      <h2 className="heading-display text-2xl">Next steps</h2>
      <ol className="mt-4 flex list-decimal flex-col gap-4 pl-5">
        {questions.map((finding) => (
          <li key={finding.id}>
            <p className="font-semibold">{finding.title}</p>
            <p className="text-ink-muted">{finding.detail}</p>
          </li>
        ))}
      </ol>
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
          Standards for Accessible Design. It does not measure what the scan could not see, and the
          coverage above says how much of the shop that was.
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
    <p className="mb-8 rounded-lg bg-ink px-4 py-3 font-semibold text-paper">
      Preview report. The rules in it are waiting for a person to review them.
    </p>
  );
}

export default async function ReportPage({ params }: PageProps<"/scans/[scanId]/report">) {
  await requireSession();
  const { scanId } = await params;
  const report = await getReport(scanId);
  if (!report) notFound();
  const { scan, scene, scenario, assessment, rules } = report;
  const groups = groupFindings(assessment?.findings ?? []);
  const checkedOn = new Date(assessment?.created_at ?? scan.created_at);

  return (
    <main className="mx-auto max-w-3xl px-5 py-10 print:max-w-none print:p-0">
      <div className="mb-10 flex items-center justify-between print:hidden">
        <Link href={`/scans/${scanId}`} className="flex items-center gap-2 rounded-lg p-2 text-ink-muted hover:bg-ink/5 hover:text-ink">
          <ArrowLeft size={18} weight="bold" aria-hidden />
          Back to the shop
        </Link>
        <PrintButton />
      </div>

      <PreviewNotice preview={report.preview} />
      <header className="grid items-end gap-6 sm:grid-cols-[1fr_9rem]">
        <div>
          <h1 className="heading-display text-4xl">{scan.name}</h1>
          <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-ink-muted">
            <dt>Checked</dt>
            <dd className="text-ink">{longDate.format(checkedOn)}</dd>
            <dt>To fix</dt>
            <dd className="text-ink">{groups.problems.length}</dd>
            <dt>Photos to send</dt>
            <dd className="text-ink">{groups.questions.length}</dd>
          </dl>
        </div>
        {scene && <FloorPlan scene={scene} className="hidden aspect-square w-full sm:block" />}
      </header>

      <ProblemsSection problems={groups.problems} />
      <NextStepsSection questions={groups.questions} />

      <WhatWeChecked scenario={scenario} passes={groups.passes} rules={rules} />
      <WhatThisIsNot />
    </main>
  );
}
