# MARXIV approval evidence

This directory is reserved for reviewed, non-secret, durable MARXIV evidence packs.

Allowed production evidence may include:

- public human-authority registry;
- fresh approval challenge;
- `marxiv.human-approval.v2` signed artifact;
- approval verification result;
- canonical evidence-pack hash;
- references to the exact Scientific Object and package hash.

Never commit:

- Ed25519 private keys;
- `MARXIV_APPROVAL_SECRET`;
- arXiv passwords or session material;
- browser cookies;
- recovery codes;
- other authentication secrets.

A GitHub Actions artifact is temporary transport evidence, not durable canon. Production evidence should enter this directory only through reviewed change control and must remain explicitly separate from arXiv submission authority.
