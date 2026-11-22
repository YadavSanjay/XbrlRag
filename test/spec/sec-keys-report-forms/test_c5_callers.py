"""C5: the callers of the key builders inherit the report-form rule.

Oracle: requirements 2-3 of docs/specs/sec-keys-report-forms.md, values read off the conftest
payload by hand. Fakes only at the seams (SecSource, FactStoreSource, HttpTransport,
FilingStore).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import fiscal_key_from_source
from src.eval.keys.sec_live import LiveSecSource, fiscal_key_with_payload
from src.eval.suites.intents import Intent
from src.eval.suites.v2_suite import IntentDroppedError, key_intent

NI = "us-gaap:NetIncomeLoss"


def _keyed(kit, intent, doc=None):
    doc = kit.companyfacts() if doc is None else doc
    return key_intent(intent, kit.inventory(), {}, kit.FakeSecSource(doc), kit.NoFactStore())


@pytest.mark.criterion("C5")
def test_fiscal_key_from_source_reads_the_10k(kit):
    # oracle: payload — FY2024 net income: A24's 7_091, not proxy P26's 7_130
    source = kit.FakeSecSource(kit.companyfacts())
    key = fiscal_key_from_source(source, kit.CIK, NI, "USD", 2024)
    assert key["value"] == 7_091 * kit.MN
    assert key["accession"] == kit.A24
    assert source.calls == [("company_facts", kit.CIK)]


@pytest.mark.criterion("C5")
def test_fiscal_key_with_payload_reads_the_10k(kit):
    # oracle: payload — as above, through LiveSecSource over the fake transport; the record
    # names the companyfacts URL the key was built from
    transport = kit.FakeTransport({kit.CF_URL: kit.cf_bytes(kit.companyfacts())})
    clock = [0.0]
    source = LiveSecSource(kit.MemoryStore(), transport=transport,
                           identity="Spec Tester spec@example.com",
                           clock=lambda: clock[0], sleep=lambda s: None,
                           wall_clock=lambda: kit.WALL)
    sourced = fiscal_key_with_payload(source, kit.CIK, NI, "USD", 2024)
    assert sourced["key"]["value"] == 7_091 * kit.MN
    assert sourced["key"]["accession"] == kit.A24
    assert sourced["key"]["form"] == "10-K"
    assert sourced["payload"]["url"] == kit.CF_URL


@pytest.mark.criterion("C5")
def test_v2_lookup_and_drop(kit):
    # oracle: payload — FY2023 net income is A24's recast 14_974 (not P26's 14_999);
    # FY2023 ProfitLoss has no report value (nil in A23, only proxy P25) -> dropped "no_key"
    keyed = _keyed(kit, Intent("lookup", tickers=[kit.TICKER], fiscal_years=[2023],
                               metrics=["NetIncome"]))
    assert keyed["value"] == 14_974 * kit.MN
    (k,) = keyed["inputs"]
    assert k["accession"] == kit.A24 and k["form"] == "10-K"
    with pytest.raises(IntentDroppedError) as dropped:
        _keyed(kit, Intent("lookup", tickers=[kit.TICKER], fiscal_years=[2023],
                           metrics=["ProfitLoss"]))
    assert dropped.value.args[0] == "no_key"


@pytest.mark.criterion("C5")
def test_v2_growth_uses_one_report_not_a_proxy(kit):
    # oracle: payload — the latest report reporting FY2023 and FY2024 net income is A24
    # (14_974 and 7_091); growth = (7_091 - 14_974) / 14_974, both inputs from A24
    keyed = _keyed(kit, Intent("compute", tickers=[kit.TICKER], fiscal_years=[2023, 2024],
                               metrics=["NetIncome"], operation="yoy_growth"))
    assert keyed["same_filing"] is True
    assert [k["accession"] for k in keyed["inputs"]] == [kit.A24, kit.A24]
    assert [k["value"] for k in keyed["inputs"]] == [14_974 * kit.MN, 7_091 * kit.MN]
    # tolerance: one float division of exact integers; 1e-12 is far below any real difference
    assert keyed["value"] == pytest.approx((7_091 - 14_974) / 14_974, rel=1e-12)
    assert keyed["not_meaningful"] is False


@pytest.mark.criterion("C5")
@given(data=st.data(), year=st.sampled_from([2023, 2024]),
       metric=st.sampled_from(["NetIncome", "Revenue"]))
def test_adding_non_report_entries_changes_no_keyed_lookup(kit, data, year, metric):
    # oracle: metamorphic, requirement 2 — the v2 keyer inherits the rule, so a lookup's
    # keyed result (or its drop reason) is the same with extra non-report entries
    noise = data.draw(kit.noise_entries(
        slots=(("us-gaap", "NetIncomeLoss", "USD"), ("us-gaap", "Revenues", "USD")),
        periods=(kit.FY23, kit.FY24)))
    intent = Intent("lookup", tickers=[kit.TICKER], fiscal_years=[year], metrics=[metric])

    def run(doc):
        try:
            return ("keyed", _keyed(kit, intent, doc))
        except IntentDroppedError as exc:
            return ("dropped", exc.args[0])

    assert run(kit.add_noise(kit.companyfacts(), noise)) == run(kit.companyfacts())
