import { CheckCircle } from "@phosphor-icons/react/dist/ssr";
import { formatInches } from "@/lib/findings";
import type { Finding, ReviewedRule, Scenario } from "@/types/contracts";
import { FactList, type Fact } from "./FactList";
import { longDate } from "./reportDates";
import { sharedReview, thresholdText, type SharedReview } from "./rulesReview";

function legs(scenario: Scenario | null): string[] {
  if (!scenario) return [];
  return scenario.stops.slice(1).map((stop, index) => `${scenario.stops[index].name} to ${stop.name}`);
}

function ReviewerCell({ rule }: { rule: ReviewedRule }) {
  return (
    <td className="py-2">
      {rule.verified_by}, {longDate.format(new Date(rule.verified_at))}
      {rule.second_check_by && <span className="block text-ink-muted">Second check: {rule.second_check_by}</span>}
    </td>
  );
}

function RulesTable({ rules, reviewerPerRow }: { rules: ReviewedRule[]; reviewerPerRow: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-ink-muted">
          <tr>
            <th className="py-2 pr-4 font-semibold">Section</th>
            <th className="py-2 pr-4 font-semibold">Check</th>
            <th className="py-2 pr-4 font-semibold">Standard</th>
            {reviewerPerRow && <th className="py-2 font-semibold">Reviewed by</th>}
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => (
            <tr key={rule.check.id} className="break-inside-avoid border-t border-rule align-top">
              <td className="measurement whitespace-nowrap py-2 pr-4">{rule.check.citation.section}</td>
              <td className="py-2 pr-4">{rule.check.title}</td>
              <td className="measurement whitespace-nowrap py-2 pr-4">{thresholdText(rule.check.threshold, rule.check.unit, formatInches)}</td>
              {reviewerPerRow && <ReviewerCell rule={rule} />}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function reviewFacts(review: SharedReview): Fact[] {
  const facts: Fact[] = [{ label: "Reviewed by", value: `${review.reviewedBy}, ${longDate.format(new Date(review.reviewedOn))}` }];
  return review.secondCheckBy ? [...facts, { label: "Second check", value: review.secondCheckBy }] : facts;
}

function PathsMeasured({ scenario }: { scenario: Scenario | null }) {
  const measured = legs(scenario);
  if (measured.length === 0) return null;
  return (
    <div className="break-inside-avoid">
      <h3 className="mt-6 font-semibold">Paths measured</h3>
      <ul className="mt-2 flex flex-wrap gap-2">
        {measured.map((leg) => (
          <li key={leg} className="rounded-md bg-rule/50 px-2.5 py-1 text-sm">{leg}</li>
        ))}
      </ul>
    </div>
  );
}

function WhatPasses({ passes }: { passes: Finding[] }) {
  if (passes.length === 0) return null;
  return (
    <div className="break-inside-avoid">
      <h3 className="mt-6 font-semibold">What passes</h3>
      <ul className="mt-2 flex flex-col gap-1.5">
        {passes.map((finding) => (
          <li key={finding.id} className="flex items-start gap-2">
            <CheckCircle size={20} weight="fill" className="mt-0.5 shrink-0 text-pass" aria-hidden />
            {finding.title}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * In a preview nobody has reviewed the rules yet, and the notice at the top
 * already says so, so the placeholder reviewer the API carries stays out.
 */
function RulesReviewed({ rules, preview }: { rules: ReviewedRule[]; preview: boolean }) {
  if (rules.length === 0) return null;
  const review = preview ? null : sharedReview(rules);
  return (
    <>
      <h3 className="mt-6 break-after-avoid font-semibold">{preview ? "Rules checked" : "Rules and who reviewed them"}</h3>
      {review && <FactList className="mt-2" facts={reviewFacts(review)} />}
      <div className="mt-2">
        <RulesTable rules={rules} reviewerPerRow={!preview && review === null} />
      </div>
    </>
  );
}

interface WhatWeCheckedProps {
  scenario: Scenario | null;
  passes: Finding[];
  rules: ReviewedRule[];
  preview: boolean;
}

export function WhatWeChecked({ scenario, passes, rules, preview }: WhatWeCheckedProps) {
  return (
    <section className="mt-12">
      <h2 className="heading-display break-after-avoid text-2xl">What we checked</h2>
      <PathsMeasured scenario={scenario} />
      <WhatPasses passes={passes} />
      <RulesReviewed rules={rules} preview={preview} />
    </section>
  );
}
