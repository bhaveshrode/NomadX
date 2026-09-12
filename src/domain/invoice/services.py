from decimal import Decimal
from typing import List
from .value_objects import Currency, Money, InvoiceTotals, Adjustment, AdjustmentType, TaxLine
from .line_item import InvoiceLineItem


class InvoiceCalculationService:
    """
    Domain Service for calculating invoice totals.
    Enforces deterministic validation of line-level and invoice-level adjustments/taxes.
    """
    @staticmethod
    def calculate_totals(
        line_items: List[InvoiceLineItem],
        adjustments: List[Adjustment],
        tax_lines: List[TaxLine],
        currency: Currency
    ) -> InvoiceTotals:
        """
        Calculates subtotal, discount_total, charge_total, tax_total, and grand_total.
        Verifies currencies of all items match the invoice currency.
        """
        zero_money = Money(Decimal("0.00"), currency)

        subtotal = zero_money
        discount_total = zero_money
        charge_total = zero_money
        tax_total = zero_money

        # 1. Process line items
        for item in line_items:
            if item.unit_price.currency != currency:
                raise ValueError(
                    f"Line item unit price currency ({item.unit_price.currency}) "
                    f"does not match invoice currency ({currency})."
                )
            
            # Subtotal adds net line-item amounts (quantity * unit_price - discount)
            subtotal += item.amount
            
            # Add line item level tax
            tax_total += item.tax

        # 2. Process invoice-level adjustments
        for adj in adjustments:
            if adj.amount.currency != currency:
                raise ValueError(
                    f"Adjustment currency ({adj.amount.currency}) "
                    f"does not match invoice currency ({currency})."
                )
            if adj.type == AdjustmentType.DISCOUNT:
                discount_total += adj.amount
            elif adj.type == AdjustmentType.CHARGE:
                charge_total += adj.amount

        # 3. Process invoice-level taxes
        for tax in tax_lines:
            if tax.amount.currency != currency:
                raise ValueError(
                    f"Tax line currency ({tax.amount.currency}) "
                    f"does not match invoice currency ({currency})."
                )
            tax_total += tax.amount

        # 4. Calculate grand total
        grand_total = subtotal - discount_total + charge_total + tax_total

        return InvoiceTotals(
            subtotal=subtotal,
            discount_total=discount_total,
            charge_total=charge_total,
            tax_total=tax_total,
            grand_total=grand_total
        )
