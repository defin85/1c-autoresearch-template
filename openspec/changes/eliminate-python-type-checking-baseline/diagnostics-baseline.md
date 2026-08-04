# BasedPyright Migration Baseline

Measured on 2026-08-04 in the verified `sppr-research-ver2` target with BasedPyright 1.39.9, Python 3.11, no baseline and only `src/one_c_autoresearch` included.

## Summary

- Files analyzed: 35
- Files with diagnostics: 34
- Errors: 318
- Warnings: 6,708
- Notes: 0
- Total diagnostics: 7,026

## Diagnostics By Rule

| Count | Rule |
| ---: | --- |
| 3,441 | `reportAny` |
| 972 | `reportExplicitAny` |
| 489 | `reportUnknownMemberType` |
| 475 | `reportUnknownArgumentType` |
| 430 | `reportUnusedCallResult` |
| 394 | `reportUnknownVariableType` |
| 119 | `reportArgumentType` |
| 90 | `reportImplicitStringConcatenation` |
| 79 | `reportUnknownParameterType` |
| 73 | `reportUnannotatedClassAttribute` |
| 60 | `reportUnusedFunction` |
| 39 | `reportPossiblyUnboundVariable` |
| 36 | `reportGeneralTypeIssues` |
| 36 | `reportOptionalMemberAccess` |
| 35 | `reportCallInDefaultInitializer` |
| 32 | `reportMissingParameterType` |
| 27 | `reportUnknownLambdaType` |
| 25 | `reportImportCycles` |
| 25 | `reportUnusedImport` |
| 24 | `reportDeprecated` |
| 19 | `reportUnnecessaryIsInstance` |
| 14 | `reportPrivateUsage` |
| 10 | `reportTypedDictNotRequiredAccess` |
| 9 | `reportImplicitOverride` |
| 9 | `reportMissingTypeArgument` |
| 9 | `reportReturnType` |
| 8 | `reportCallIssue` |
| 7 | `reportIndexIssue` |
| 6 | `reportUnusedVariable` |
| 5 | `reportAttributeAccessIssue` |
| 5 | `reportOptionalSubscript` |
| 5 | `reportPrivateLocalImportUsage` |
| 4 | `reportMissingTypeStubs` |
| 4 | `reportUninitializedInstanceVariable` |
| 3 | `reportUnusedParameter` |
| 2 | `reportOperatorIssue` |
| 2 | `reportOptionalIterable` |
| 1 | `reportAssignmentType` |
| 1 | `reportUnboundVariable` |
| 1 | `reportUnnecessaryComparison` |
| 1 | `reportUnreachable` |

## Highest-Count Modules

| Count | Module |
| ---: | --- |
| 964 | `workspace_api.py` |
| 574 | `sqlite_state.py` |
| 562 | `indexes.py` |
| 530 | `workflow.py` |
| 490 | `sources.py` |
| 385 | `pipeline_graphs.py` |
| 364 | `dispatcher.py` |
| 317 | `service.py` |
| 232 | `mrq.py` |
| 205 | `stage_recompute.py` |

This file records aggregate migration evidence only. The temporary machine-readable baseline is not an accepted final artifact.
