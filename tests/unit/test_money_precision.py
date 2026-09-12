import pytest
from decimal import Decimal
from nomadx.src.domain.invoice.value_objects import Currency, CurrencyType, Money


def test_currency_creation():
    usd = Currency("USD", CurrencyType.FIAT)
    assert usd.code == "USD"
    assert usd.type == CurrencyType.FIAT
    
    # Try invalid code
    with pytest.raises(ValueError):
        Currency("U", CurrencyType.FIAT)
        
    with pytest.raises(ValueError):
        Currency("USDUSDUSD", CurrencyType.FIAT)


def test_money_creation_success():
    usd = Currency("USD", CurrencyType.FIAT)
    m = Money(Decimal("100.50"), usd)
    assert m.amount == Decimal("100.50")
    assert m.currency == usd


def test_money_creation_rejects_float():
    usd = Currency("USD", CurrencyType.FIAT)
    
    # Ensure float amounts are blocked to prevent rounding errors
    with pytest.raises(TypeError):
        Money(100.50, usd)


def test_money_addition_same_currency():
    usd = Currency("USD", CurrencyType.FIAT)
    m1 = Money("100.50", usd)
    m2 = Money("50.25", usd)
    
    result = m1 + m2
    assert result.amount == Decimal("150.75")
    assert result.currency == usd


def test_money_addition_different_currencies_raise_error():
    usd = Currency("USD", CurrencyType.FIAT)
    eur = Currency("EUR", CurrencyType.FIAT)
    
    m1 = Money("100.00", usd)
    m2 = Money("50.00", eur)
    
    with pytest.raises(ValueError):
        _ = m1 + m2


def test_money_subtraction_same_currency():
    usd = Currency("USD", CurrencyType.FIAT)
    m1 = Money("100.50", usd)
    m2 = Money("50.25", usd)
    
    result = m1 - m2
    assert result.amount == Decimal("50.25")


def test_money_subtraction_different_currencies_raise_error():
    usd = Currency("USD", CurrencyType.FIAT)
    eur = Currency("EUR", CurrencyType.FIAT)
    
    m1 = Money("100.00", usd)
    m2 = Money("50.00", eur)
    
    with pytest.raises(ValueError):
        _ = m1 - m2


def test_money_multiplication_by_int_and_decimal():
    usd = Currency("USD", CurrencyType.FIAT)
    m = Money("10.50", usd)
    
    # Integer mult
    r1 = m * 2
    assert r1.amount == Decimal("21.00")
    
    # Rmul
    r2 = 3 * m
    assert r2.amount == Decimal("31.50")
    
    # Decimal mult
    r3 = m * Decimal("1.5")
    assert r3.amount == Decimal("15.75")


def test_money_multiplication_rejects_float():
    usd = Currency("USD", CurrencyType.FIAT)
    m = Money("10.50", usd)
    
    with pytest.raises(TypeError):
        _ = m * 1.5


def test_money_comparisons():
    usd = Currency("USD", CurrencyType.FIAT)
    m1 = Money("10.00", usd)
    m2 = Money("20.00", usd)
    m3 = Money("10.00", usd)
    
    assert m1 < m2
    assert m1 <= m2
    assert m2 > m1
    assert m2 >= m1
    assert m1 == m3
    assert m1 != m2
    assert m1 <= m3
    assert m1 >= m3


def test_money_comparison_different_currencies_raise_error():
    usd = Currency("USD", CurrencyType.FIAT)
    eur = Currency("EUR", CurrencyType.FIAT)
    
    m1 = Money("10.00", usd)
    m2 = Money("10.00", eur)
    
    with pytest.raises(ValueError):
        _ = m1 < m2
