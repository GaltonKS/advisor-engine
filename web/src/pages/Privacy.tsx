import type { ReactNode } from "react";
import { Brand } from "../components/Layout";

// The GaltonKS Privacy Policy, as approved (web/public/legal/GaltonKS-Privacy-Policy.pdf).
// The wording is legal text: change it only from a new approved version, and update the
// effective date and the PDF together.
export const PRIVACY_EFFECTIVE_DATE = "November 1, 2026";
export const PRIVACY_PDF = "/legal/GaltonKS-Privacy-Policy.pdf";
const PRIVACY_EMAIL = "privacy@galtonks.com";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-8">
      <h2 className="mb-3 text-2xl">{title}</h2>
      <div className="space-y-3 leading-relaxed">{children}</div>
    </section>
  );
}

function Bullets({ items }: { items: [string, ReactNode][] }) {
  return (
    <ul className="list-disc space-y-2 pl-6">
      {items.map(([lead, rest]) => (
        <li key={lead}><b>{lead}</b> {rest}</li>
      ))}
    </ul>
  );
}

const Email = () => <a className="underline" href={`mailto:${PRIVACY_EMAIL}`}>{PRIVACY_EMAIL}</a>;

const RETENTION: [string, string][] = [
  ["Account information", "While your account is active, then 30 days after you close it"],
  ["Billing records", "4 years, for tax and accounting obligations"],
  ["Analytics and cookie data", "1 month"],
  ["Client records and uploaded files", "30 days after the client is deleted or the firm’s account closes"],
  ["Audit records", "1 year from the date of the entry"],
];

export default function Privacy() {
  return (
    <div className="min-h-screen">
      <header className="navy-band">
        <div className="mx-auto max-w-[1240px] px-4 py-3"><Brand /></div>
      </header>
      <main className="mx-auto max-w-[760px] px-4 py-10">
        <div className="eyebrow mb-3">Legal</div>
        <h1 className="text-4xl leading-tight">GaltonKS Privacy Policy</h1>
        <p className="mt-2 text-sm text-muted">
          Effective date: {PRIVACY_EFFECTIVE_DATE} ·{" "}
          <a className="underline" href={PRIVACY_PDF} download>Download PDF</a>
        </p>
        <div className="my-6 h-px w-14 bg-gold" />

        <Section title="Introduction and Scope">
          <p>
            This policy explains what personal information GaltonKS collects from people in the United States,
            how we use it, and the choices you have.
          </p>
          <p>
            GaltonKS is operated by GaltonKS LLC (“GaltonKS,” “we,” or “us”). This policy covers the GaltonKS web
            application and the pages that link to this policy (the “Service”). The Service is intended for use in
            the United States. By using it, you agree to the practices described here.
          </p>
        </Section>

        <Section title="Information We Collect">
          <p>We collect the following information.</p>
          <Bullets items={[
            ["Account information.", "When you create an account, we collect your name and email address, plus anything you add to your profile."],
            ["Payment information.", "When you pay for the Service, our payment processor collects your payment details. We receive limited billing information back, such as card type, last four digits, and payment status."],
            ["Usage data.", "When you use the Service, we and our service providers automatically collect technical information such as your IP address, browser and device type, pages viewed, features used, referring page, and timestamps."],
          ]} />
          <p>
            Advisers may also upload CSV files containing their clients’ portfolio holdings. We process these files
            on behalf of the adviser’s firm to provide the Service. We also keep records of activity in the Service,
            including who took each action, for which firm, client, and portfolio, and the inputs and results.
          </p>
        </Section>

        <Section title="Client Data">
          <p>
            The portfolio information that advisers upload about their clients is held on behalf of the adviser’s
            firm. The firm decides what to upload and is responsible for having the right to share it with us.
          </p>
          <p>
            We use client information to provide the Service to that firm, and we do not sell it. Each firm can see
            only its own data. Clients of an adviser who want to access, correct, or delete their information should
            contact the adviser’s firm directly. Client files and records are deleted on the schedule in Data
            Retention below.
          </p>
        </Section>

        <Section title="Galton AI">
          <p>
            Galton AI is the assistant available to signed-in advisers. When an adviser asks it a question, we send
            the question, along with the portfolio information from the adviser’s own firm that is needed to answer
            it, to Anthropic, which generates the response on our behalf. Anthropic acts as our service provider for
            this purpose. This information is not used to train AI models.
          </p>
          <p>
            Galton AI can read only files that belong to the adviser’s own firm, and it cannot read the audit
            records. The questions, responses, and draft approvals from each Galton AI session are saved in those
            audit records.
          </p>
        </Section>

        <Section title="How We Use Your Information">
          <p>We use the information we collect only for the purposes below.</p>
          <Bullets items={[
            ["Provide the Service:", "create and manage your account, authenticate you, and deliver the features you use."],
            ["Billing:", "process payments, issue receipts, and manage your subscription."],
            ["Communicate with you:", "send service notices, security alerts, and replies to your support requests."],
            ["Improve the Service:", "understand how people use GaltonKS so we can fix problems and build better features."],
            ["Protect the Service:", "detect fraud, abuse, and security incidents."],
            ["Meet legal obligations:", "comply with applicable law and respond to lawful requests."],
          ]} />
        </Section>

        <Section title="Payment Information">
          <p>
            Payments are handled by Stripe, a third-party processor. Your full card number and security code go
            directly to the processor and are not stored on GaltonKS servers.
          </p>
          <p>
            We keep only the billing details needed to manage your account, such as the last four digits of your
            card, card type, billing status, and transaction history. The processor’s use of your information is
            governed by its own privacy policy:{" "}
            <a className="underline" href="https://stripe.com/privacy" target="_blank" rel="noreferrer">https://stripe.com/privacy</a>.
          </p>
        </Section>

        <Section title="Cookies and Analytics">
          <p>We use cookies and similar technologies to keep you signed in and remember your settings.</p>
          <Bullets items={[
            ["Essential cookies", "are required for sign-in and security. The Service does not work without them."],
            ["Analytics.", "We use Plausible Analytics to understand how the Service is used. Plausible does not use cookies. It records page views, referring pages, browser and device type, and approximate location, and does not store your IP address."],
          ]} />
          <p>
            You can block or delete cookies in your browser settings, though blocking essential cookies may break
            parts of the Service. We do not currently offer a separate opt-out control for analytics.
          </p>
        </Section>

        <Section title="How We Share Information">
          <p>
            GaltonKS does not sell your personal information or share it for cross-context behavioral advertising.
          </p>
          <p>We share information only in these situations:</p>
          <Bullets items={[
            ["Service providers.", "Companies that help us run the Service: Supabase (sign-in, database, and file storage), Render (hosting), Anthropic (the AI behind Galton AI), Stripe (payments), and Plausible (analytics). They may use your information only to perform services for us."],
            ["Legal reasons.", "When required by law, subpoena, or other legal process, or to protect the rights, safety, or property of GaltonKS, our users, or others."],
            ["Business transfers.", "If GaltonKS is involved in a merger, acquisition, or sale of assets, your information may be transferred as part of that transaction. We will notify you of any change in how it is handled."],
            ["With your consent.", "In any other case, when you tell us to."],
          ]} />
        </Section>

        <Section title="Data Retention">
          <p>
            We keep personal information only as long as needed for the purposes in this policy, or as the law
            requires.
          </p>
          <table className="w-full border border-rule bg-card text-sm">
            <thead>
              <tr className="border-b border-rule text-left">
                <th className="px-3 py-2 font-semibold">Data</th>
                <th className="px-3 py-2 font-semibold">How long we keep it</th>
              </tr>
            </thead>
            <tbody>
              {RETENTION.map(([data, period]) => (
                <tr key={data} className="border-b border-rule last:border-b-0">
                  <td className="px-3 py-2 align-top">{data}</td>
                  <td className="px-3 py-2 align-top">{period}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>When the retention period ends, we delete the data or remove anything that identifies you.</p>
        </Section>

        <Section title="Data Security">
          <p>
            We use reasonable administrative, technical, and physical safeguards to protect your information,
            including:
          </p>
          <Bullets items={[
            ["Verified access.", "Every request to the Service is checked against a verified sign-in. Galton AI is available only to signed-in advisers and is subject to a daily usage limit."],
            ["Separation between firms.", "Each firm’s data is isolated at the database level, so an adviser can reach only their own firm’s records."],
            ["Protected client files.", "Uploaded CSV files are kept in private storage, checked for integrity each time they are read, and limited in size and type before they are saved."],
            ["Audit records.", "We keep an append-only record of activity in the Service that advisers cannot edit or delete."],
            ["Protected credentials.", "Private keys and other secrets are stored on our servers and are never sent to your browser."],
          ]} />
          <p>No system is completely secure, so we cannot guarantee absolute security.</p>
          <p>
            If a security incident affects your personal information, we will notify you and the relevant
            authorities as required by applicable law.
          </p>
        </Section>

        <Section title="Your Choices and US State Privacy Rights">
          <p>
            You can review and update your name and email address in your account settings. You can also unsubscribe
            from marketing emails using the link in any message. Service and security notices will continue.
          </p>
          <p>
            Depending on where you live, state privacy laws, such as those in California, Colorado, Connecticut,
            Virginia, and others, may give you these rights:
          </p>
          <Bullets items={[
            ["Access:", "know what personal information we hold about you and get a copy."],
            ["Correction:", "ask us to fix inaccurate information."],
            ["Deletion:", "ask us to delete your personal information, subject to legal exceptions."],
            ["Opt-out:", "we do not sell or share your personal information, including for targeted advertising, so there is nothing to opt out of."],
            ["Non-discrimination:", "we will not treat you differently for exercising these rights."],
          ]} />
          <p>
            To make a request, email <Email />. We may need to verify your identity first, and we will respond within
            the time the applicable law requires, generally 45 days. If we deny a request, you may be able to appeal
            by replying to our decision.
          </p>
          <p>
            If you are a client of an adviser who uses GaltonKS, we hold your information on behalf of that adviser’s
            firm. Please contact the firm directly with any request to access, correct, or delete it.
          </p>
        </Section>

        <Section title="Children’s Privacy">
          <p>
            The Service is not directed to children under 13, and we do not knowingly collect personal information
            from them. If you believe a child under 13 has given us personal information, email us at <Email /> and
            we will delete it.
          </p>
        </Section>

        <Section title="Changes to This Policy">
          <p>
            We may update this policy from time to time. When we do, we will change the effective date at the top and
            post the new version in the Service. For material changes, we will also notify you by email or an in-app
            notice before they take effect. Continued use of the Service after a change means you accept the updated
            policy.
          </p>
        </Section>

        <Section title="Contact Us">
          <p>Questions about this policy or your information? Contact us at:</p>
          <p>GaltonKS LLC | <Email /></p>
        </Section>
      </main>
    </div>
  );
}
