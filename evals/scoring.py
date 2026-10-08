"""Execution accuracy: does the agent's query result match the gold result?

We compare RESULTS, not SQL text, because many different queries are correct.
Rules (kept simple so they are easy to explain):
 - same number of rows;
 - every gold column must appear somewhere in the prediction (extra columns,
   such as an id next to a name, are fine; column names do not matter);
 - whole numbers (counts) must match exactly;
 - decimals match within 0.1% (or 0.01 absolute), so 73.7 and 73.68 both pass;
   a rate given as a fraction (0.737) also matches a percentage (73.7);
 - text matches case-insensitively;
 - row order matters only when the question asks for a ranking or a sequence.
"""


def _normalize(value):
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value if isinstance(value, float) else int(value)
    text = str(value).strip()
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return text.lower()
    return int(number) if number.is_integer() else number


def _values_match(gold, predicted) -> bool:
    numbers = (int, float)
    if isinstance(gold, float) and isinstance(predicted, numbers):
        tolerance = max(0.01, 0.001 * abs(gold))
        candidates = (predicted, predicted * 100, predicted / 100)  # fraction vs percentage
        return any(abs(gold - candidate) <= tolerance for candidate in candidates)
    return gold == predicted


def _sort_key(value):
    # None first, then numbers, then text; works for mixed columns
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, (int, float)):
        return (1, value, "")
    return (2, 0.0, str(value))


def _columns(rows: list[list]) -> list[list]:
    if not rows:
        return []
    return [[_normalize(row[i]) for row in rows] for i in range(len(rows[0]))]


def _column_match(gold_column: list, predicted_column: list, ordered: bool) -> bool:
    if not ordered:
        gold_column = sorted(gold_column, key=_sort_key)
        predicted_column = sorted(predicted_column, key=_sort_key)
    return all(_values_match(g, p) for g, p in zip(gold_column, predicted_column, strict=True))


def results_match(gold_rows: list[list], predicted_rows: list[list], ordered: bool) -> bool:
    """True if the predicted result contains the gold result (see module docstring)."""
    if len(gold_rows) != len(predicted_rows):
        return False
    if not gold_rows:
        return True
    gold_columns, predicted_columns = _columns(gold_rows), _columns(predicted_rows)

    # Step 1: find, for each gold column, a predicted column with the same values.
    mapping = []
    for gold_column in gold_columns:
        found = next(
            (j for j, column in enumerate(predicted_columns) if _column_match(gold_column, column, ordered)), None
        )
        if found is None:
            return False
        mapping.append(found)
    if ordered:
        return True  # columns matched position by position, so the rows line up too

    # Step 2 (unordered): check whole rows, so "Dubai -0.17" cannot match "Dubai 0.52".
    def row_key(row):
        return tuple(_sort_key(value) for value in row)

    gold_table = sorted(zip(*gold_columns, strict=True), key=row_key)
    predicted_table = sorted(zip(*(predicted_columns[j] for j in mapping), strict=True), key=row_key)
    return all(
        _values_match(g, p)
        for gold_row, predicted_row in zip(gold_table, predicted_table, strict=True)
        for g, p in zip(gold_row, predicted_row, strict=True)
    )
