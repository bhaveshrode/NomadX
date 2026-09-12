import pytest
from nomadx.src.domain.shared.result.result import Result, ResultError

def test_result_success():
    res = Result.ok("hello")
    assert res.is_success
    assert not res.is_failure
    assert res.value == "hello"
    
    with pytest.raises(ValueError):
        _ = res.error

def test_result_failure():
    err = ResultError.validation("Invalid age", {"field": "age"})
    res = Result.fail(err)
    
    assert res.is_failure
    assert not res.is_success
    assert res.error == err
    assert err.code == "VALIDATION_ERROR"
    assert err.message == "Invalid age"
    assert err.metadata == {"field": "age"}

    with pytest.raises(ValueError):
        _ = res.value

def test_result_combine():
    r1 = Result.ok("ok")
    r2 = Result.ok(123)
    err = ResultError.conflict("Constraint violated")
    r3 = Result.fail(err)

    # All success
    res_comb_ok = Result.combine([r1, r2])
    assert res_comb_ok.is_success

    # One failure
    res_comb_fail = Result.combine([r1, r3, r2])
    assert res_comb_fail.is_failure
    assert res_comb_fail.error == err
