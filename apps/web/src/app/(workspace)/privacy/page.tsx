import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Privacy at Standard Physics",
  description: "What a scan of your shop contains, where it goes, and how to delete it.",
};

const UPDATED = "20 September 2026";
const CONTACT = "privacy@standardphysics.app";

function Section({ heading, children }: { heading: string; children: React.ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="heading-display text-2xl">{heading}</h2>
      <div className="mt-3 flex flex-col gap-3 text-ink-muted">{children}</div>
    </section>
  );
}

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-3xl px-5 py-10">
      <h1 className="heading-display text-4xl">Privacy at Standard Physics</h1>
      <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-ink-muted">
        <dt>Last updated</dt>
        <dd className="text-ink">{UPDATED}</dd>
      </dl>

      <Section heading="What a scan contains">
        <p>
          Walking your shop with the app records the shape of the room and the things in it. That
          means a floor plan and a room model, a LiDAR mesh of the surfaces, still photographs taken
          along the walk, a video of the walk itself, and the position the camera was in for each
          frame. Photographs of an interior show whatever was in the room at the time, including
          anyone who happened to be standing in it.
        </p>
        <p>
          Your account holds the email address you signed up with, the name you gave your shop, and
          your password, which is stored as a scrypt hash and cannot be read back.
        </p>
      </Section>

      <Section heading="Where it goes">
        <p>
          Scans upload to the Standard Physics server and stay there. They belong to the account
          that uploaded them, and the workspace only ever lists your own shops.
        </p>
        <p>
          To work out what is in a room, the server sends photographs from the scan to a model
          provider that identifies and counts objects in them. Nothing else about you is sent with
          them, and the provider is set to retain nothing.
        </p>
      </Section>

      <Section heading="What we do not do">
        <p>
          Your scans are not sold, rented or shared with anyone else. There is no advertising in
          Standard Physics, no tracking across other apps or websites, and no profile built about
          you. Nobody outside the account sees your shop.
        </p>
      </Section>

      <Section heading="Deleting it">
        <p>
          A single scan goes from the list in the app or the workspace, and takes its photographs,
          its video and its measurements with it.
        </p>
        <p>
          Deleting your whole account removes the account, every shop in it and every file stored
          for those shops. The button is on the home screen of the app and at the top of the
          workspace. It happens immediately, there is no grace period, and it cannot be undone.
        </p>
      </Section>

      <Section heading="Asking us">
        <p>
          Write to <a className="underline decoration-rule underline-offset-2" href={`mailto:${CONTACT}`}>{CONTACT}</a> for
          a copy of what is held about you, a correction, or anything else about this page.
        </p>
      </Section>

      <p className="mt-12 text-ink-muted">
        <Link className="underline decoration-rule underline-offset-2" href="/">Back to your shops</Link>
      </p>
    </main>
  );
}
