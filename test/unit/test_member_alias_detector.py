"""Unit tests for the WS-1 / A2 member-alias detector (pure logic, no DB)."""
from src.ingest.sec_edgar.xbrl.member_alias_detector import (
    AliasReport,
    MemberAlias,
    MemberAliasDetector,
    _local,
)


class TestConfidence:
    def test_confidence_scales_with_support_and_saturates(self):
        conf = MemberAliasDetector._confidence
        assert conf(0) == 0.0
        assert conf(1) == 0.2
        assert conf(2) == 0.4
        assert conf(5) == 1.0
        assert conf(10) == 1.0          # saturates at 1.0


class TestLocalHelper:
    def test_local_strips_namespace(self):
        assert _local("http://www.oracle.com/20250531:FixedRateSeniorNotesMember") \
            == "FixedRateSeniorNotesMember"
        assert _local("us-gaap:TreasuryStockCommonMember") == "TreasuryStockCommonMember"
        assert _local(None) is None


class TestMemberAliasShape:
    def test_to_dict_uses_local_names(self):
        a = MemberAlias(
            cik="0001341439",
            axis_qname="us-gaap:DebtInstrumentAxis",
            old_member_qname="http://www.oracle.com/20240531:SeniorNotesDueMay2030Member",
            new_member_qname="http://www.oracle.com/20250531:FixedRateSeniorNotesDueMay2030Member",
            detected_in_fy=2025, support_concepts=2, support_facts=4,
            confidence=MemberAliasDetector._confidence(2),
        )
        d = a.to_dict()
        assert d["axis"] == "DebtInstrumentAxis"
        assert d["old_member"] == "SeniorNotesDueMay2030Member"
        assert d["new_member"] == "FixedRateSeniorNotesDueMay2030Member"
        assert d["support_concepts"] == 2
        assert d["confidence"] == 0.4

    def test_report_to_dict(self):
        a = MemberAlias("c", "ax", "old", "new", 2025, 3, 5, 0.6)
        report = AliasReport(detected=1, aliases=[a])
        d = report.to_dict()
        assert d["detected"] == 1
        assert d["aliases"][0]["new_member"] == "new"
