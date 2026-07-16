# Migration Requirement Contour

Validate the customization registry, run `migration-requirement bootstrap`
without `--apply`, inspect counts and conflicts, then publish atomically with
`--apply`. Use bounded `show`, `context`, and `neighbors` commands. Modify graph
relations through `link` and `unlink`, validate after each block, and activate
the contour only after comparison evidence is accepted. `deactivate` restores
the previous subject-card and functional-gap ownership route.
