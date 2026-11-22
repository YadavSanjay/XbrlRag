"""Unit tests for XbrlCalculatorTool (design doc §2.2 / §1.4 "Tool sandbox").

Pure tests, no database. Valid-expression cases mirror the `compute` steps a
plan_generator would emit for scripts/xbrl/analyst_queries.sql scenarios
(YoY growth §2c, segment share §3, margins §7, free cash flow §6).
"""
import math

import pytest

from src.retriever.xbrl.xbrl_calculator_tool import CalculatorError, XbrlCalculatorTool


@pytest.fixture
def calc() -> XbrlCalculatorTool:
    return XbrlCalculatorTool()


# NVIDIA FY2026 figures (from xbrl_db, see test_xbrl_factstore_lookup.py)
NVDA_BINDINGS = {
    "rev26": 215938000000.0,
    "rev25": 130497000000.0,
    "cogs26": 62475000000.0,
    "oi26": 130387000000.0,
    "ocf26": 102718000000.0,
    "capex26": 6042000000.0,
    "dc26": 193737000000.0,
    "gm26": 16042000000.0,
}


class TestReferencedStepIds:
    def test_extracts_identifiers(self):
        assert XbrlCalculatorTool.referenced_step_ids("(dc26 - dc25) / dc25") == ["dc25", "dc26"]

    def test_ignores_literals(self):
        assert XbrlCalculatorTool.referenced_step_ids("rev26 / 1000000") == ["rev26"]

    def test_empty_and_invalid_return_empty(self):
        assert XbrlCalculatorTool.referenced_step_ids("") == []
        assert XbrlCalculatorTool.referenced_step_ids("   ") == []
        assert XbrlCalculatorTool.referenced_step_ids("a +") == []  # syntax error -> []
        assert XbrlCalculatorTool.referenced_step_ids(None) == []  # type: ignore[arg-type]


class TestValidExpressions:
    def test_simple_subtraction(self, calc):
        assert calc.evaluate("rev26 - cogs26", NVDA_BINDINGS) == pytest.approx(153463000000.0)

    def test_simple_division(self, calc):
        assert calc.evaluate("oi26 / rev26", NVDA_BINDINGS) == pytest.approx(0.60381683631412720318)

    def test_gross_margin_with_parentheses(self, calc):
        """analyst_queries.sql 7a: (Revenue - CostOfRevenue) / Revenue."""
        result = calc.evaluate("(rev26 - cogs26) / rev26", NVDA_BINDINGS)
        assert result == pytest.approx(0.71068084357547073697)

    def test_yoy_growth(self, calc):
        """analyst_queries.sql 2c: (curr - prior) / prior."""
        result = calc.evaluate("(rev26 - rev25) / rev25", NVDA_BINDINGS)
        assert result == pytest.approx(0.65473535790094791451)

    def test_free_cash_flow(self, calc):
        """analyst_queries.sql 6a/7d: CashFromOperations - Capex."""
        result = calc.evaluate("ocf26 - capex26", NVDA_BINDINGS)
        assert result == pytest.approx(96676000000.0)

    def test_segment_share_of_total(self, calc):
        """analyst_queries.sql 3a/3d: Data Center share of total revenue."""
        result = calc.evaluate("dc26 / rev26", NVDA_BINDINGS)
        assert result == pytest.approx(193737000000.0 / 215938000000.0)

    def test_two_segment_sum(self, calc):
        result = calc.evaluate("dc26 + gm26", NVDA_BINDINGS)
        assert result == pytest.approx(193737000000.0 + 16042000000.0)

    def test_unary_minus(self, calc):
        assert calc.evaluate("-rev26", NVDA_BINDINGS) == pytest.approx(-215938000000.0)

    def test_unary_plus(self, calc):
        assert calc.evaluate("+rev26", NVDA_BINDINGS) == pytest.approx(215938000000.0)

    def test_numeric_literal_scaling(self, calc):
        """Hypothetical: 'if R&D had been 14% of revenue' style expr (design §2.3)."""
        result = calc.evaluate("rev26 * 0.14", NVDA_BINDINGS)
        assert result == pytest.approx(215938000000.0 * 0.14)

    def test_nested_parentheses(self, calc):
        result = calc.evaluate("((rev26 - rev25)) / (rev25)", NVDA_BINDINGS)
        assert result == pytest.approx(0.65473535790094791451)

    def test_chained_operators(self, calc):
        result = calc.evaluate("rev26 - cogs26 - oi26", NVDA_BINDINGS)
        assert result == pytest.approx(215938000000.0 - 62475000000.0 - 130387000000.0)

    def test_single_identifier(self, calc):
        assert calc.evaluate("rev26", NVDA_BINDINGS) == pytest.approx(215938000000.0)

    def test_single_numeric_literal(self, calc):
        assert calc.evaluate("42", {}) == 42.0

    def test_float_literal(self, calc):
        assert calc.evaluate("3.14", {}) == pytest.approx(3.14)


class TestErrorHandling:
    def test_division_by_zero(self, calc):
        with pytest.raises(CalculatorError, match="division by zero"):
            calc.evaluate("rev26 / zero", {**NVDA_BINDINGS, "zero": 0.0})

    def test_division_by_zero_literal(self, calc):
        with pytest.raises(CalculatorError, match="division by zero"):
            calc.evaluate("rev26 / 0", NVDA_BINDINGS)

    def test_undefined_step_id(self, calc):
        with pytest.raises(CalculatorError, match="undefined step id"):
            calc.evaluate("rev26 + does_not_exist", NVDA_BINDINGS)

    def test_step_with_none_value(self, calc):
        """A failed lookup leaves bindings[id]=None; referencing it must error,
        not raise a TypeError from None arithmetic."""
        with pytest.raises(CalculatorError, match="has no value"):
            calc.evaluate("rev26 + missing", {**NVDA_BINDINGS, "missing": None})

    def test_empty_expr(self, calc):
        with pytest.raises(CalculatorError, match="non-empty"):
            calc.evaluate("", NVDA_BINDINGS)

    def test_whitespace_only_expr(self, calc):
        with pytest.raises(CalculatorError, match="non-empty"):
            calc.evaluate("   ", NVDA_BINDINGS)

    def test_non_string_expr(self, calc):
        with pytest.raises(CalculatorError):
            calc.evaluate(None, NVDA_BINDINGS)  # type: ignore[arg-type]

    def test_too_long_expr(self, calc):
        long_expr = " + ".join(["rev26"] * 60)  # > MAX_EXPR_LENGTH
        with pytest.raises(CalculatorError, match="exceeds max length"):
            calc.evaluate(long_expr, NVDA_BINDINGS)

    def test_syntax_error(self, calc):
        with pytest.raises(CalculatorError, match="invalid expression syntax"):
            calc.evaluate("rev26 + + + /", NVDA_BINDINGS)

    def test_unclosed_paren(self, calc):
        with pytest.raises(CalculatorError, match="invalid expression syntax"):
            calc.evaluate("(rev26 - cogs26", NVDA_BINDINGS)


class TestSandboxRejectsUnsafeConstructs:
    """Defense-in-depth: PlanValidator.COMPUTE_EXPR_ALLOWED_RE already restricts
    the character set, but the calculator must independently refuse to execute
    anything beyond `+ - * /`, parens, numeric literals, and identifiers."""

    def test_rejects_function_calls(self, calc):
        with pytest.raises(CalculatorError, match="Call"):
            calc.evaluate("__import__('os').system('echo pwned')", NVDA_BINDINGS)

    def test_rejects_open_file_call(self, calc):
        with pytest.raises(CalculatorError, match="Call"):
            calc.evaluate("open('/etc/passwd').read()", NVDA_BINDINGS)

    def test_rejects_attribute_access(self, calc):
        with pytest.raises(CalculatorError, match="Attribute"):
            calc.evaluate("rev26.__class__", NVDA_BINDINGS)

    def test_rejects_dunder_class_escape(self, calc):
        with pytest.raises(CalculatorError):
            calc.evaluate("().__class__.__bases__[0]", NVDA_BINDINGS)

    def test_rejects_subscript(self, calc):
        with pytest.raises(CalculatorError, match="Subscript"):
            calc.evaluate("rev26[0]", NVDA_BINDINGS)

    def test_rejects_comparisons(self, calc):
        with pytest.raises(CalculatorError, match="Compare"):
            calc.evaluate("rev26 > rev25", NVDA_BINDINGS)

    def test_rejects_boolean_ops(self, calc):
        with pytest.raises(CalculatorError, match="BoolOp"):
            calc.evaluate("rev26 and rev25", NVDA_BINDINGS)

    def test_rejects_conditional_expressions(self, calc):
        with pytest.raises(CalculatorError, match="IfExp"):
            calc.evaluate("rev26 if rev26 > rev25 else rev25", NVDA_BINDINGS)

    def test_rejects_list_literals(self, calc):
        with pytest.raises(CalculatorError):
            calc.evaluate("[rev26, rev25]", NVDA_BINDINGS)

    def test_rejects_string_literals(self, calc):
        with pytest.raises(CalculatorError, match="non-numeric literal"):
            calc.evaluate("'rev26'", NVDA_BINDINGS)

    def test_rejects_string_concatenation(self, calc):
        with pytest.raises(CalculatorError, match="non-numeric literal"):
            calc.evaluate("'a' + 'b'", NVDA_BINDINGS)

    def test_rejects_fstrings(self, calc):
        with pytest.raises(CalculatorError):
            calc.evaluate("f'{rev26}'", NVDA_BINDINGS)

    def test_rejects_comprehensions(self, calc):
        with pytest.raises(CalculatorError):
            calc.evaluate("[x for x in (1,2,3)]", NVDA_BINDINGS)

    def test_rejects_lambda(self, calc):
        with pytest.raises(CalculatorError):
            calc.evaluate("(lambda: rev26)()", NVDA_BINDINGS)

    def test_rejects_power_operator(self, calc):
        with pytest.raises(CalculatorError, match="Pow"):
            calc.evaluate("2 ** 999999", NVDA_BINDINGS)

    def test_rejects_modulo_operator(self, calc):
        with pytest.raises(CalculatorError, match="Mod"):
            calc.evaluate("rev26 % rev25", NVDA_BINDINGS)

    def test_rejects_floor_division(self, calc):
        with pytest.raises(CalculatorError, match="FloorDiv"):
            calc.evaluate("rev26 // rev25", NVDA_BINDINGS)

    def test_rejects_bitwise_ops(self, calc):
        with pytest.raises(CalculatorError):
            calc.evaluate("rev26 | rev25", NVDA_BINDINGS)

    def test_rejects_walrus_assignment(self, calc):
        with pytest.raises(CalculatorError):
            calc.evaluate("(x := rev26)", NVDA_BINDINGS)

    def test_rejects_boolean_literal(self, calc):
        with pytest.raises(CalculatorError, match="non-numeric literal"):
            calc.evaluate("True", NVDA_BINDINGS)

    def test_rejects_none_literal(self, calc):
        with pytest.raises(CalculatorError, match="non-numeric literal"):
            calc.evaluate("None", NVDA_BINDINGS)

    def test_multiline_injection_is_syntax_error(self, calc):
        with pytest.raises(CalculatorError, match="invalid expression syntax"):
            calc.evaluate("rev26\nimport os", NVDA_BINDINGS)
