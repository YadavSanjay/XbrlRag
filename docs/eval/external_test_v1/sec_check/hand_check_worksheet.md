# SEC companyfacts check — hand-check worksheet

18 dimensional and breakdown cases, which companyfacts cannot cover. Check each expected value against the filing itself.

**How to fill in:** for each case, open the filing link. It opens the filing's folder on EDGAR, so click the main 10-K (or 20-F) document, usually the largest `.htm`. Find the segment, product or geography table for that fiscal year and compare the expected value(s). Amounts here are in full units, while filings usually show millions: `269,287,000,000` here is `269,287` in a table "in millions". Put an `x` in one box (`[x] match`). For a mismatch, write what the filing shows in the note. Leave the rest of the file as it is.

## `aq-3a-product-segment-breakdown:fy=2025-ticker=AMZN`

- Question: Break down Amazon's fiscal 2025 revenue by product or service segment.
- Kind: breakdown; ticker AMZN; fiscal year 2025
- Expected row: Online Stores = 269,287,000,000
- Expected row: Third-Party Seller Services = 172,162,000,000
- Expected row: Amazon Web Services = 128,725,000,000
- Expected row: Advertising = 68,635,000,000
- Expected row: Subscription Services = 49,619,000,000
- Expected row: Physical Stores = 22,561,000,000
- Expected row: Other Services = 5,935,000,000
- Cited facts:
  - 9640: `RevenueFromContractWithCustomerExcludingAssessedTax` 269,287,000,000 USD, 2025-01-01 to 2025-12-31; accession 0001018724-26-000004 — https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/
  - 9646: `RevenueFromContractWithCustomerExcludingAssessedTax` 172,162,000,000 USD, 2025-01-01 to 2025-12-31; accession 0001018724-26-000004 — https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/
  - 9655: `RevenueFromContractWithCustomerExcludingAssessedTax` 128,725,000,000 USD, 2025-01-01 to 2025-12-31; accession 0001018724-26-000004 — https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/
  - 9649: `RevenueFromContractWithCustomerExcludingAssessedTax` 68,635,000,000 USD, 2025-01-01 to 2025-12-31; accession 0001018724-26-000004 — https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/
  - 9652: `RevenueFromContractWithCustomerExcludingAssessedTax` 49,619,000,000 USD, 2025-01-01 to 2025-12-31; accession 0001018724-26-000004 — https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/
  - 9643: `RevenueFromContractWithCustomerExcludingAssessedTax` 22,561,000,000 USD, 2025-01-01 to 2025-12-31; accession 0001018724-26-000004 — https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/
  - 9658: `RevenueFromContractWithCustomerExcludingAssessedTax` 5,935,000,000 USD, 2025-01-01 to 2025-12-31; accession 0001018724-26-000004 — https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/


- **Result:** [ x] match   [ ] mismatch   — note: 

## `aq-3a-product-segment-breakdown:fy=2025-ticker=AVGO`

- Question: Break down Broadcom's fiscal 2025 revenue by product or service segment.
- Kind: breakdown; ticker AVGO; fiscal year 2025
- Expected row: Product = 44,847,000,000
- Expected row: Subscriptions and Services = 19,040,000,000
- Cited facts:
  - 26624: `RevenueFromContractWithCustomerExcludingAssessedTax` 44,847,000,000 USD, 2024-11-04 to 2025-11-02; accession 0001730168-25-000121 — https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/
  - 26627: `RevenueFromContractWithCustomerExcludingAssessedTax` 19,040,000,000 USD, 2024-11-04 to 2025-11-02; accession 0001730168-25-000121 — https://www.sec.gov/Archives/edgar/data/1730168/000173016825000121/


- **Result:** [x ] match   [ ] mismatch   — note: 

## `aq-3b-business-segment-breakdown:fy=2024-ticker=AMD`

- Question: Break down AMD's fiscal 2024 revenue by reportable business segment.
- Kind: breakdown; ticker AMD; fiscal year 2024
- Expected row: Datacenter = 12,579,000,000
- Expected row: Client = 7,054,000,000
- Expected row: Embedded = 3,557,000,000
- Expected row: Gaming = 2,595,000,000
- Cited facts:
  - 19541: `RevenueFromContractWithCustomerExcludingAssessedTax` 12,579,000,000 USD, 2023-12-31 to 2024-12-28; accession 0000002488-25-000012 — https://www.sec.gov/Archives/edgar/data/2488/000000248825000012/
  - 19544: `RevenueFromContractWithCustomerExcludingAssessedTax` 7,054,000,000 USD, 2023-12-31 to 2024-12-28; accession 0000002488-25-000012 — https://www.sec.gov/Archives/edgar/data/2488/000000248825000012/
  - 19550: `RevenueFromContractWithCustomerExcludingAssessedTax` 3,557,000,000 USD, 2023-12-31 to 2024-12-28; accession 0000002488-25-000012 — https://www.sec.gov/Archives/edgar/data/2488/000000248825000012/
  - 19547: `RevenueFromContractWithCustomerExcludingAssessedTax` 2,595,000,000 USD, 2023-12-31 to 2024-12-28; accession 0000002488-25-000012 — https://www.sec.gov/Archives/edgar/data/2488/000000248825000012/


- **Result:** [ x] match   [ ] mismatch   — note: 

## `aq-3b-business-segment-breakdown:fy=2026-ticker=MSFT`

- Question: Break down Microsoft's fiscal 2026 revenue by reportable business segment.
- Kind: breakdown; ticker MSFT; fiscal year 2026
- Expected row: Productivity And Business Processes = 139,996,000,000
- Expected row: Intelligent Cloud = 137,791,000,000
- Expected row: More Personal Computing = 54,052,000,000
- Cited facts:
  - 105706: `RevenueFromContractWithCustomerExcludingAssessedTax` 139,996,000,000 USD, 2025-07-01 to 2026-06-30; accession 0001193125-26-323660 — https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/
  - 105718: `RevenueFromContractWithCustomerExcludingAssessedTax` 137,791,000,000 USD, 2025-07-01 to 2026-06-30; accession 0001193125-26-323660 — https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/
  - 105730: `RevenueFromContractWithCustomerExcludingAssessedTax` 54,052,000,000 USD, 2025-07-01 to 2026-06-30; accession 0001193125-26-323660 — https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/


- **Result:** [x ] match   [ ] mismatch   — note: 

## `aq-3d-segment-member-lookup:fy=2025-segment=DRAM_Products-ticker=MU`

- Question: How much revenue did Micron Technology report for DRAM Products in fiscal 2025?
- Kind: dimensional; ticker MU; fiscal year 2025
- Expected: 28,578,000,000 USD
- Cited facts:
  - 58370: `RevenueFromContractWithCustomerExcludingAssessedTax` 28,578,000,000 USD, 2024-08-30 to 2025-08-28; accession 0000723125-25-000028 — https://www.sec.gov/Archives/edgar/data/723125/000072312525000028/


- **Result:** [x ] match   [ ] mismatch   — note: 

## `aq-3d-segment-member-lookup:fy=2025-segment=Gaming-ticker=AMD`

- Question: How much revenue did AMD report for Gaming in fiscal 2025?
- Kind: dimensional; ticker AMD; fiscal year 2025
- Expected: 3,910,000,000 USD
- Cited facts:
  - 115012: `RevenueFromContractWithCustomerExcludingAssessedTax` 3,910,000,000 USD, 2024-12-29 to 2025-12-27; accession 0000002488-26-000018 — https://www.sec.gov/Archives/edgar/data/2488/000000248826000018/


- **Result:** [x ] match   [ ] mismatch   — note: 

## `aq-3d-segment-member-lookup:fy=2025-segment=Google_Search_Other-ticker=GOOGL`

- Question: How much revenue did Alphabet report for Google Search & Other in fiscal 2025?
- Kind: dimensional; ticker GOOGL; fiscal year 2025
- Expected: 224,532,000,000 USD
- Cited facts:
  - 34667: `RevenueFromContractWithCustomerExcludingAssessedTax` 224,532,000,000 USD, 2025-01-01 to 2025-12-31; accession 0001652044-26-000018 — https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/


- **Result:** [ x] match   [ ] mismatch   — note: 

## `aq-3d-segment-member-lookup:fy=2025-segment=Networking-ticker=NVDA`

- Question: How much revenue did NVIDIA report for Networking in fiscal 2025?
- Kind: dimensional; ticker NVDA; fiscal year 2025
- Expected: 12,990,000,000 USD
- Cited facts:
  - 65578: `Revenues` 12,990,000,000 USD, 2024-01-29 to 2025-01-26; accession 0001045810-25-000023 — https://www.sec.gov/Archives/edgar/data/1045810/000104581025000023/


- **Result:** [x ] match   [ ] mismatch   — note: 

## `aq-3d-segment-member-lookup:fy=2025-segment=Service_Other-ticker=META`

- Question: How much revenue did Meta Platforms report for Service, Other in fiscal 2025?
- Kind: dimensional; ticker META; fiscal year 2025
- Expected: 2,584,000,000 USD
- Cited facts:
  - 41640: `RevenueFromContractWithCustomerExcludingAssessedTax` 2,584,000,000 USD, 2025-01-01 to 2025-12-31; accession 0001628280-26-003942 — https://www.sec.gov/Archives/edgar/data/1326801/000162828026003942/


- **Result:** [ x] match   [ ] mismatch   — note: 

## `aq-3d-segment-member-lookup:fy=2026-segment=Microsoft_Three_Six_Five_Commercial_-08b561d2`

- Question: How much revenue did Microsoft report for Microsoft Three Six Five Commercial Products And Cloud Services in fiscal 2026?
- Kind: dimensional; ticker MSFT; fiscal year 2026
- Expected: 101,997,000,000 USD
- Cited facts:
  - 105769: `RevenueFromContractWithCustomerExcludingAssessedTax` 101,997,000,000 USD, 2025-07-01 to 2026-06-30; accession 0001193125-26-323660 — https://www.sec.gov/Archives/edgar/data/789019/000119312526323660/


- **Result:** [x ] match   [ ] mismatch   — note: 

## `aq-4a-geographic-breakdown:fy=2024-ticker=AAPL`

- Question: Break down Apple's fiscal 2024 revenue by geography.
- Kind: breakdown; ticker AAPL; fiscal year 2024
- Expected row: Other Countries = 181,887,000,000
- Expected row: UNITED STATES = 142,196,000,000
- Expected row: CHINA = 66,952,000,000
- Cited facts:
  - 14259: `RevenueFromContractWithCustomerExcludingAssessedTax` 181,887,000,000 USD, 2023-10-01 to 2024-09-28; accession 0000320193-24-000123 — https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/
  - 14253: `RevenueFromContractWithCustomerExcludingAssessedTax` 142,196,000,000 USD, 2023-10-01 to 2024-09-28; accession 0000320193-24-000123 — https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/
  - 14256: `RevenueFromContractWithCustomerExcludingAssessedTax` 66,952,000,000 USD, 2023-10-01 to 2024-09-28; accession 0000320193-24-000123 — https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/


- **Result:** [x ] match   [ ] mismatch   — note: 

## `aq-4a-geographic-breakdown:fy=2026-ticker=ORCL`

- Question: Break down Oracle's fiscal 2026 revenue by geography.
- Kind: breakdown; ticker ORCL; fiscal year 2026
- Expected row: UNITED STATES = 39,835,000,000
- Expected row: Other Countries = 20,843,000,000
- Expected row: UNITED KINGDOM = 2,816,000,000
- Expected row: GERMANY = 1,993,000,000
- Expected row: JAPAN = 1,870,000,000
- Cited facts:
  - 109075: `Revenues` 39,835,000,000 USD, 2025-06-01 to 2026-05-31; accession 0001193125-26-277521 — https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/
  - 109099: `Revenues` 20,843,000,000 USD, 2025-06-01 to 2026-05-31; accession 0001193125-26-277521 — https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/
  - 109081: `Revenues` 2,816,000,000 USD, 2025-06-01 to 2026-05-31; accession 0001193125-26-277521 — https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/
  - 109087: `Revenues` 1,993,000,000 USD, 2025-06-01 to 2026-05-31; accession 0001193125-26-277521 — https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/
  - 109093: `Revenues` 1,870,000,000 USD, 2025-06-01 to 2026-05-31; accession 0001193125-26-277521 — https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/


- **Result:** [x ] match   [ ] mismatch   — note: 

## `aq-8b-multidim-lookup:combo=LongTermDebt_Senior_Notes_Member_and_Senior_Un-33ba4138`

- Question: For Amazon in fiscal 2025, what was long term debt for Senior Notes and Senior Unsecured Notes 2020?
- Kind: dimensional; ticker AMZN; fiscal year 2025
- Expected: 7,750,000,000 USD
- Cited facts:
  - 9080: `LongTermDebt` 7,750,000,000 USD, at 2025-12-31; accession 0001018724-26-000004 — https://www.sec.gov/Archives/edgar/data/1018724/000101872426000004/


- **Result:** [x ] match   [ ] mismatch   — note: 

## `aq-8b-multidim-lookup:combo=OperatingIncomeLoss_Compute_And_Networking_Seg-35c15313`

- Question: For NVIDIA in fiscal 2025, what was operating income loss for Compute And Networking Segment and Operating Segments?
- Kind: dimensional; ticker NVDA; fiscal year 2025
- Expected: 82,875,000,000 USD
- Cited facts:
  - 65495: `OperatingIncomeLoss` 82,875,000,000 USD, 2024-01-29 to 2025-01-26; accession 0001045810-25-000023 — https://www.sec.gov/Archives/edgar/data/1045810/000104581025000023/


- **Result:** [ x] match   [ ] mismatch   — note: 

## `aq-8b-multidim-lookup:combo=OperatingIncomeLoss_Datacenter_Member_and_Oper-6ede653c`

- Question: For AMD in fiscal 2024, what was operating income loss for Datacenter and Operating Segments?
- Kind: dimensional; ticker AMD; fiscal year 2024
- Expected: 3,482,000,000 USD
- Cited facts:
  - 19574: `OperatingIncomeLoss` 3,482,000,000 USD, 2023-12-31 to 2024-12-28; accession 0000002488-25-000012 — https://www.sec.gov/Archives/edgar/data/2488/000000248825000012/


- **Result:** [x ] match   [ ] mismatch   — note: 

## `aq-8b-multidim-lookup:combo=OperatingIncomeLoss_Graphics_Segment_Member_an-3fb979be`

- Question: For NVIDIA in fiscal 2026, what was operating income loss for Graphics Segment and Operating Segments?
- Kind: dimensional; ticker NVDA; fiscal year 2026
- Expected: 9,156,000,000 USD
- Cited facts:
  - 1127: `OperatingIncomeLoss` 9,156,000,000 USD, 2025-01-27 to 2026-01-25; accession 0001045810-26-000021 — https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/


- **Result:** [x ] match   [ ] mismatch   — note: 

## `aq-8b-multidim-lookup:combo=SellingAndMarketingExpense_Cloud_and_Software_-16c4074f`

- Question: For Oracle in fiscal 2026, what was selling and marketing expense for Cloud and Software Business and Operating Segments?
- Kind: dimensional; ticker ORCL; fiscal year 2026
- Expected: 7,212,000,000 USD
- Cited facts:
  - 108976: `SellingAndMarketingExpense` 7,212,000,000 USD, 2025-06-01 to 2026-05-31; accession 0001193125-26-277521 — https://www.sec.gov/Archives/edgar/data/1341439/000119312526277521/


- **Result:** [x ] match   [ ] mismatch   — note: 

## `aq-8b-multidim-lookup:combo=SellingAndMarketingExpense_Hardware_Business_M-0069e593`

- Question: For Oracle in fiscal 2025, what was selling and marketing expense for Hardware Business and Operating Segments?
- Kind: dimensional; ticker ORCL; fiscal year 2025
- Expected: 276,000,000 USD
- Cited facts:
  - 73559: `SellingAndMarketingExpense` 276,000,000 USD, 2024-06-01 to 2025-05-31; accession 0000950170-25-087926 — https://www.sec.gov/Archives/edgar/data/1341439/000095017025087926/

- **Result:** [x ] match   [ ] mismatch   — note: 

