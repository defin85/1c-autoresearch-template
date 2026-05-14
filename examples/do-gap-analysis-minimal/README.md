# Minimal Example

This example documents the intended command shape for a concrete 1C research repo.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ..\..\scripts\bootstrap\New-1cResearchRepo.ps1 `
  -TargetPath E:\Projects\example_research `
  -ProjectId example `
  -Product "1C Document Management" `
  -BaselineVersion "2.1" `
  -TargetVersion "2.1" `
  -VendorBaseline E:\Projects\vendor `
  -TargetCf E:\Projects\customer\cf `
  -TargetCfe E:\Projects\customer\cfe
```
