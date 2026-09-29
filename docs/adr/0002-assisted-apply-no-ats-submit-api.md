# Assisted apply, not the ATS submit APIs

Greenhouse, Lever and Ashby all expose an application-submit endpoint, but each one requires an API key issued by the hiring company (Greenhouse Job Board API key, Lever Super Admin key, Ashby key with `candidatesWrite`), so a Candidate cannot use them. Their hosted forms also carry CAPTCHAs (reCAPTCHA, and hCaptcha on Lever). Applying is therefore Assisted apply: a visible browser fills the hosted form and the Candidate reviews, solves any CAPTCHA and submits. Verified on 2026-09-29.
