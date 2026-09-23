import pytest

from sched.cronexpr import CronError, parse


def test_star_and_plain_values():
    spec = parse("* * * * *")
    assert spec.minutes == frozenset(range(60))
    assert spec.dom_any and spec.dow_any
    spec = parse("5 4 3 2 1")
    assert spec.minutes == {5}
    assert spec.hours == {4}
    assert spec.dom == {3}
    assert spec.months == {2}
    assert spec.dow == {1}


def test_lists_ranges_steps():
    spec = parse("1,2,3 0-6 */10 1-12/3 *")
    assert spec.minutes == {1, 2, 3}
    assert spec.hours == set(range(0, 7))
    assert spec.dom == set(range(1, 32, 10))
    assert spec.months == {1, 4, 7, 10}


def test_range_with_step_and_single_with_step():
    assert parse("0 9-17/2 * * *").hours == {9, 11, 13, 15, 17}
    assert parse("5/15 * * * *").minutes == {5, 20, 35, 50}


def test_names_case_insensitive():
    spec = parse("0 0 * JAN,MAR mon-FRI")
    assert spec.months == {1, 3}
    assert spec.dow == {1, 2, 3, 4, 5}
    assert parse("0 0 * * sun").dow == {0}


def test_dow_seven_is_sunday():
    assert parse("0 0 * * 7").dow == {0}
    assert parse("0 0 * * 0,7").dow == {0}


@pytest.mark.parametrize("alias,expected", [
    ("@hourly", ("0 * * * *",)),
    ("@daily", ("0 0 * * *",)),
    ("@weekly", ("0 0 * * 0",)),
    ("@monthly", ("0 0 1 * *",)),
    ("@yearly", ("0 0 1 1 *",)),
])
def test_aliases(alias, expected):
    got = parse(alias)
    want = parse(expected[0])
    assert got.minutes == want.minutes and got.hours == want.hours
    assert got.dom == want.dom and got.months == want.months
    assert got.dow == want.dow


@pytest.mark.parametrize("expr,field_index,token", [
    ("70 * * * *", 0, "70"),
    ("* 25 * * *", 1, "25"),
    ("* * 0 * *", 2, "0"),
    ("* * * 13 *", 3, "13"),
    ("* * * * 8", 4, "8"),
    ("* * * foo *", 3, "foo"),
    ("* * * * funday", 4, "funday"),
    ("*/0 * * * *", 0, "*/0"),
    ("5-2 * * * *", 0, "5-2"),
    ("1-2-3 * * * *", 0, "1-2-3"),
    ("* * * * * *", -1, None),
    ("* * *", -1, None),
    ("@sometimes", -1, "@sometimes"),
    ("", -1, None),
])
def test_validation_errors_pinpoint_field_token_reason(expr, field_index, token):
    with pytest.raises(CronError) as excinfo:
        parse(expr)
    err = excinfo.value
    assert err.field_index == field_index
    if token is not None:
        assert err.token == token
        assert token in str(err)
    assert err.reason
    if field_index >= 0:
        assert f"field {field_index + 1}" in str(err)


def test_no_raw_exceptions_leak():
    for bad in ["*", "@", "a b c d e", "1/2/3 * * * *", ",,,", "- * * * *"]:
        with pytest.raises(CronError):
            parse(bad)
