# Attribution

This project builds on and draws ideas from Hermes Agent by Nous Research and jcode,
both licensed under the MIT License. The prior project attribution also references
AVO: Autonomous Variant Optimization (arXiv:2603.24517); this task did not implement
AVO or integrate Team B's work.

Hermes agent behavior, provider/plugin interfaces, and existing memory/review components
remain those of this repository and its upstream contributors. The Rust `memd` engine and
Hermes bridge predate this task; their implementation history and dependencies remain
documented under `memd/`.

The learning workflow is described as Prime-inspired at the user's request. It is an
architectural adaptation built in this repository; no claim is made that it reproduces
Prime Agent's implementation, and no Prime Agent source was copied for this change.
Reused code from Hermes or other sources should retain its existing license and attribution.
