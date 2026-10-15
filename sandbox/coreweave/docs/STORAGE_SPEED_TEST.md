# Bounded synthetic transfer diagnostic

`artifact_speed_test.py` defaults to a local cost plan. A real diagnostic needs
explicit output directory, private project, prepared image and user-verified
rates; `--run` authorizes synthetic upload and a ten-minute sandbox. Payload
size is bounded and configurable. It never reads research inputs or `.env`.

The diagnostic hashes a generated payload, commits a private artifact, then
compares sequential and SDK bulk downloads into separate scratch directories.
Each result records bytes, elapsed time and SHA256 verification with cache
bypass. Do not interpret the bulk-download label as proof of a particular
parallel worker count, or one small transfer as sustained global throughput.
The output includes the artifact identity for deliberate later retention review;
there is no automatic artifact or local-file deletion.

A real run costs money and transfers data. It is not a routine code validation
step. Offline tests use recording fake SDKs and tiny synthetic bytes. Record
settled provider lifecycle/billing separately; stopwatch estimates cannot prove
exact billable runtime. Input staging before allocation avoids paying sandbox
compute while a workstation uploads, but storage charges and permissions still
need independent verification.
