# Call Report PDF Download Instructions

The FFIEC CDR (https://cdr.ffiec.gov/public/ManageFacsimiles.aspx) requires ASP.NET form-state for PDF downloads,
so this step is manual. For each bank below, do the following:

1. Open <https://cdr.ffiec.gov/public/ManageFacsimiles.aspx>.
2. Select **Report Type**: `Call Report` (default).
3. **Cycle Date**: select the report date listed below (typically 09/30/2024).
4. **ID Type**: choose `FDIC Certificate Number`. Enter the **CERT** number below.
5. Press **Search**. Click the bank name in the result table.
6. Click the **Call Report PDF** link in the resulting page.
7. Save the PDF into `data/call_reports/` with the exact filename listed.

Verification: re-run this script after downloading; missing files will be re-listed.

| # | Bank | State | CERT | RSSD | Filename | Status |
|---|------|-------|------|------|----------|--------|
| 1 | Legends Bank | Tennessee | 34936 | 2745426 | `2745426_2024-09-30.pdf` | OK |
| 2 | First National Bank North | Minnesota | 5269 | 805755 | `805755_2024-09-30.pdf` | OK |
| 3 | Quail Creek Bank | Oklahoma | 21848 | 507152 | `507152_2024-09-30.pdf` | OK |
| 4 | First Utah Bank | Utah | 22738 | 207872 | `207872_2024-09-30.pdf` | OK |
| 5 | Nebraskaland Bank | Nebraska | 34811 | 2667957 | `2667957_2024-09-30.pdf` | OK |
| 6 | Centennial Bank | Arkansas | 11241 | 456045 | `456045_2024-09-30.pdf` | OK |
| 7 | LendingClub Bank, National Association | Utah | 32551 | 264772 | `264772_2024-09-30.pdf` | OK |
| 8 | Stifel Bank and Trust | Missouri | 57311 | 3076248 | `3076248_2024-09-30.pdf` | OK |
| 9 | ConnectOne Bank | New Jersey | 57919 | 3317932 | `3317932_2024-09-30.pdf` | OK |
| 10 | Stock Yards Bank & Trust Company | Kentucky | 258 | 317342 | `317342_2024-09-30.pdf` | OK |
| 11 | Regions Bank | Alabama | 12368 | 233031 | `233031_2024-09-30.pdf` | OK |
| 12 | Santander Bank, N.A. | Delaware | 29950 | 722777 | `722777_2024-09-30.pdf` | OK |
| 13 | Western Alliance Bank | Arizona | 57512 | 3138146 | `3138146_2024-09-30.pdf` | OK |
| 14 | Bank of America, National Association | North Carolina | 3510 | 480228 | `480228_2024-09-30.pdf` | OK |
| 15 | U.S. Bank National Association | Ohio | 6548 | 504713 | `504713_2024-09-30.pdf` | OK |

**15/15** PDFs present locally.
