# app/modules/finance/helpers.py
"""
Helper functions for the Finance module.
"""

from decimal import Decimal, ROUND_HALF_UP
import math


def calculate_loan_payment(principal, annual_interest_rate, total_months, grace_period=0):
    """
    Calculate monthly loan payment using the amortization formula.
    
    Args:
        principal: Total loan amount
        annual_interest_rate: Annual interest rate (e.g., 18.0 for 18%)
        total_months: Total number of months
        grace_period: Months before first payment (default: 0)
    
    Returns:
        dict: Monthly payment, total interest, total repayment
    """
    if total_months <= 0:
        return {
            'monthly_payment': 0,
            'total_interest': 0,
            'total_repayment': 0,
            'schedule': []
        }
    
    monthly_rate = annual_interest_rate / 100 / 12
    
    if monthly_rate == 0:
        monthly_payment = principal / total_months
        total_interest = 0
    else:
        # Payment = P * r * (1+r)^n / ((1+r)^n - 1)
        factor = (1 + monthly_rate) ** total_months
        monthly_payment = principal * monthly_rate * factor / (factor - 1)
        total_repayment = monthly_payment * total_months
        total_interest = total_repayment - principal
    
    # Generate schedule
    schedule = []
    remaining_balance = principal
    
    for month in range(1, total_months + 1):
        if month <= grace_period:
            # Grace period: no payment, interest accrues
            interest_payment = remaining_balance * monthly_rate
            principal_payment = 0
            total_payment = 0
            remaining_balance += interest_payment
        else:
            interest_payment = remaining_balance * monthly_rate
            principal_payment = monthly_payment - interest_payment
            total_payment = monthly_payment
            remaining_balance -= principal_payment
        
        # Ensure we don't go negative due to rounding
        if remaining_balance < 0:
            principal_payment += remaining_balance
            remaining_balance = 0
        
        schedule.append({
            'month': month,
            'principal_payment': principal_payment,
            'interest_payment': interest_payment,
            'total_payment': total_payment,
            'remaining_balance': remaining_balance if remaining_balance > 0 else 0
        })
    
    return {
        'monthly_payment': monthly_payment,
        'total_interest': total_interest,
        'total_repayment': total_repayment,
        'schedule': schedule
    }


def validate_payment_terms(cash_pct, days30_pct, days60_pct, days90_pct, days120_pct, days150_pct):
    """
    Validate that payment terms sum to 100%.
    
    Returns:
        tuple: (is_valid, error_message)
    """
    total = (cash_pct or 0) + (days30_pct or 0) + (days60_pct or 0) + \
            (days90_pct or 0) + (days120_pct or 0) + (days150_pct or 0)
    
    if abs(total - 100) > 0.01:
        return False, f"Payment terms must sum to 100% (current: {total}%)"
    
    return True, ""


def format_currency(amount, currency_code='IRR'):
    """
    Format currency with appropriate symbol and decimal places.
    """
    if amount is None:
        amount = 0
    
    # Convert to integer if it's a whole number
    if amount == int(amount):
        formatted = f"{int(amount):,}"
    else:
        formatted = f"{amount:,.2f}"
    
    symbols = {
        'IRR': 'ریال',
        'USD': '$',
        'EUR': '€',
        'GBP': '£'
    }
    
    symbol = symbols.get(currency_code, '')
    
    if currency_code == 'IRR':
        return f"{formatted} {symbol}"
    else:
        return f"{symbol}{formatted}"


def calculate_monthly_totals(sales_lines):
    """
    Calculate totals for sales forecast lines.
    
    Returns:
        dict: Totals for each month
    """
    totals = {
        'month1_quantity': 0,
        'month1_amount': 0,
        'month2_quantity': 0,
        'month2_amount': 0,
        'month3_quantity': 0,
        'month3_amount': 0,
        'total_quantity': 0,
        'total_amount': 0
    }
    
    for line in sales_lines:
        totals['month1_quantity'] += line.get('Month1Quantity', 0)
        totals['month1_amount'] += line.get('Month1Amount', 0)
        totals['month2_quantity'] += line.get('Month2Quantity', 0)
        totals['month2_amount'] += line.get('Month2Amount', 0)
        totals['month3_quantity'] += line.get('Month3Quantity', 0)
        totals['month3_amount'] += line.get('Month3Amount', 0)
        totals['total_quantity'] += line.get('TotalQuantity', 0)
        totals['total_amount'] += line.get('TotalAmount', 0)
    
    return totals


def get_persian_seasons():
    """Get list of Persian seasons with their month mappings."""
    return [
        {'code': 1, 'name': 'بهار', 'name_en': 'Spring', 'months': ['فروردین', 'اردیبهشت', 'خرداد']},
        {'code': 2, 'name': 'تابستان', 'name_en': 'Summer', 'months': ['تیر', 'مرداد', 'شهریور']},
        {'code': 3, 'name': 'پاییز', 'name_en': 'Autumn', 'months': ['مهر', 'آبان', 'آذر']},
        {'code': 4, 'name': 'زمستان', 'name_en': 'Winter', 'months': ['دی', 'بهمن', 'اسفند']}
    ]


def get_persian_months():
    """Get list of Persian months."""
    return ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
            'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']