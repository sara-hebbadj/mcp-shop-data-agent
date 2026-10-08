"""Execution-accuracy scoring rules."""

from evals.scoring import results_match


def test_exact_and_extra_columns():
    gold = [["Dubai", 1638]]
    assert results_match(gold, [["Dubai", 1638]], ordered=False)
    assert results_match(gold, [[1638, "Dubai", "UAE"]], ordered=False)  # order/extra columns ok
    assert not results_match(gold, [["Dubai", 1637]], ordered=False)


def test_number_tolerance_and_percentages():
    assert results_match([[73.7]], [[73.68]], ordered=False)
    assert results_match([[73.7]], [[0.737]], ordered=False)  # fraction vs percentage
    assert not results_match([[73.7]], [[74.5]], ordered=False)


def test_unordered_rows_must_still_line_up():
    gold = [["Dubai", -0.17], ["Al Ain", 0.52]]
    assert results_match(gold, [["Al Ain", 0.52], ["Dubai", -0.17]], ordered=False)
    assert not results_match(gold, [["Dubai", 0.52], ["Al Ain", -0.17]], ordered=False)


def test_ordered_questions_check_the_order():
    gold = [["A", 3], ["B", 2]]
    assert results_match(gold, [["A", 3], ["B", 2]], ordered=True)
    assert not results_match(gold, [["B", 2], ["A", 3]], ordered=True)


def test_row_count_must_match():
    assert not results_match([[1], [2]], [[1]], ordered=False)
    assert results_match([], [], ordered=False)
