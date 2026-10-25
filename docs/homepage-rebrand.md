# King Ollies homepage

The homepage remains a Flask/Jinja page served by `index.py`. Authentication, purchasing, and logged-in dashboard redirects are unchanged. The new theme is isolated in `static/css/home.css`; interactions use `static/js/home.js` without a frontend framework or animation dependency.

## Actions

- Get Started / Buy Data: existing signup flow.
- Login / Open Dashboard: existing authentication and customer dashboard.
- MTN AFA: existing authenticated `/customer/afa` page.
- BECE and WASSCE: existing authenticated `/purchase_checker` page.
- NOV/DEC, university vouchers, nursing and teacher licensure: enquiry via the configured `SUPPORT_WHATSAPP`.
- NSS postings/reposting, SHS placement, business registration, passports, birth certificates, affidavits/gazettes, and attestation: enquiry via the same configured WhatsApp contact.

No placeholder or simulated submission is used. Dedicated online processing destinations for enquiry-only services would require confirmation before replacing their WhatsApp actions. No NOV/DEC label was found in the existing project or public homepage; the supplied NOV/DEC spelling is retained.

## Assets and legal text

The supplied logo remains `BRAND_LOGO_URL`. The hero is an original AI-generated illustrative Ghana-focused customer portrait, not a testimonial. Responsive WebP assets are about 73 KB and 33 KB. Existing homepage terms and privacy copy are preserved in `templates/home_legal.html`, presented in keyboard-accessible native dialogs. Existing terms include delivery estimates and AFA talktime conditions; these were not extended to the newly advertised assistance services.

## Validation

- 30 existing pytest tests passed.
- Actual Flask app rendered with a mocked database; homepage and local action routes checked, and authenticated dashboard redirect verified.
- All Jinja templates compiled.
- Headless Chrome screenshots reviewed at 1440px desktop and 390px mobile; overflow checked at 320, 390, 768, and 1440px.
- Mobile navigation, Escape behavior, legal dialogs, image loading, nine service categories, reduced motion, and browser JavaScript errors checked.
- Preview screenshots: `artifacts/homepage/desktop.png` and `artifacts/homepage/mobile.png`.

Changes are local project files. Production deployment was not performed.
