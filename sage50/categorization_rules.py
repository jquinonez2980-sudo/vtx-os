"""
Concetta Enterprises Inc. — Bank Transaction Categorization Ruleset
Generated: 2026-05-09
Purpose: Replace generic Canadian payee ruleset with client-specific rules
Expected Impact: ~85% auto-categorization, 5–10% manual review queue
"""

from decimal import Decimal
from typing import Tuple, Optional

# Known cheque payees populated after live runs confirm payee names.
# Format: (UPPERCASE_KEYWORD_IN_DESCRIPTION, gl_no, account_name)
# Example: ("ROGERS COMMUNICATIONS", 5600, "Telephone & Cellular")
# Leave empty — entries are added as payees are confirmed from cheque OCR.
_CHEQUE_PAYEES: list[tuple[str, int, str]] = []


# Maps our internal 4-digit GL codes to Concetta's actual Sage 50 8-digit lId values.
# Applied only at bridge post time; categorization and BQ use the 4-digit codes.
CONCETTA_ACCOUNT_MAP: dict[str, str] = {
    "1060": "11000000",   # Bank (Sage 50: "Bank" lId=11000000)
    "2100": "21000000",   # Employee Tax Deductions
    "4100": "40500000",   # Revenue → Mortgage Interest (only revenue posting account)
    "5155": "51550000",   # Car Lease
    "5200": "52000000",   # Bank Charges & Interest
    "5400": "54000000",   # Insurance
    "5600": "56000000",   # Telephone & Cellular
    "5700": "57000000",   # Visa
    "5725": "57250000",   # AMEX
    "5750": "57500000",   # Mastercard
    "5800": "58000000",   # Rent
    "5850": "58500000",   # Wages & Benefits
    "5900": "59000000",   # Suspense
}


class ConcettaRuleset:
    """
    Client-specific categorization rules for Concetta Enterprises Inc.
    
    Rules are applied in priority order:
    0. Revenue (SENTRIX FINANCI deposits) → 4100
    1. Insurance (ECONOMICAL INS) → 5400
    2. Bank Fees → 5200
    3. Telecom (FIDO) → 5600
    4. Credit Card Clearing (TD VISA, SCOTIA VISA, PC MASTRCRD, AMEX CARDS) → 5700/5750
    5. Payroll Tax (RECEIVER GENERAL, CRA) → 2100
    6. Payroll/Wages (CONCETTA BOSH, CHRISTINA BOSH) → 5850
    7. Car Lease (SPL LOAN) → 5155
    8. Rent → 5800
    9. Catch-all Suspense → 5900
    """
    
    def __init__(self):
        """Initialize the ruleset with all categorization rules."""
        self.rules = [
            self._rule_revenue,
            self._rule_cheque_payee,   # payee names extracted from cheque images
            self._rule_insurance,
            self._rule_bank_fees,
            self._rule_telecom,
            self._rule_card_clearing,
            self._rule_payroll_tax,
            self._rule_payroll_wages,
            self._rule_car_lease,
            self._rule_rent,
            self._rule_suspense_fallback,
        ]
    
    def categorize(self, description: str, amount: Decimal) -> Tuple[int, str, Decimal]:
        """
        Apply rules in priority order. First match wins.
        
        Args:
            description: Transaction description from bank statement
            amount: Transaction amount (positive = deposit, negative = withdrawal)
        
        Returns:
            Tuple of (gl_account_number, account_name, confidence_score)
        """
        desc_upper = description.upper()
        
        for rule in self.rules:
            result = rule(desc_upper, amount)
            if result is not None:
                return result
        
        # Fallback (should never reach here due to suspense rule)
        return (5900, "Suspense", Decimal("0"))
    
    # ========== RULE 0: Revenue (client invoice payments) ==========
    def _rule_revenue(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """SENTRIX FINANCI (client invoice deposits) → 4100 Revenue, 95% confidence"""
        if "SENTRIX FINANCI" in desc and amount > 0:
            return (4100, "Revenue", Decimal("95"))
        return None

    # ========== RULE 1: Cheque payees (populated after first live run) ==========
    def _rule_cheque_payee(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """Match CHQ transactions where payee was extracted from cheque image.

        desc format after enrichment: "CHQ#00788 - Rogers Communications Inc."
        Add entries to _CHEQUE_PAYEES as payees are confirmed from live runs.
        """
        for keyword, gl_no, gl_name in _CHEQUE_PAYEES:
            if keyword in desc:
                return (gl_no, gl_name, Decimal("85"))
        return None

    # ========== RULE 2: Insurance ==========
    def _rule_insurance(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """ECONOMICAL INS → 5400 Insurance, 95% confidence"""
        if "ECONOMICAL INS" in desc:
            return (5400, "Insurance", Decimal("95"))
        return None
    
    # ========== RULE 2: Bank Fees ==========
    def _rule_bank_fees(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """
        Bank Charges & Fees → 5200, 90% confidence
        Match: MONTHY PLAN FEE, PAPER STMT FEE, NSF RETURN FEE, CHEQUE CHARGE, BANK CHARGE
        """
        bank_fee_keywords = [
            "MONTHLY PLAN FEE",
            "PAPER STMT FEE",
            "NSF RETURN FEE",
            "CHEQUE CHARGE",
            "BANK CHARGE",
        ]
        for keyword in bank_fee_keywords:
            if keyword in desc:
                return (5200, "Bank Charges & Interest", Decimal("90"))
        return None
    
    # ========== RULE 3: Telecom ==========
    def _rule_telecom(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """FIDO / FIDO SOLUTN → 5600 Telephone & Cellular, 98% confidence"""
        if "FIDO" in desc:
            return (5600, "Telephone & Cellular", Decimal("98"))
        return None
    
    # ========== RULE 4: Credit Card Clearing ==========
    def _rule_card_clearing(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """
        Credit card payment clearing entries → 5700 or 5750, 98% confidence
        Match: TD VISA, SCOTIA VISA → 5700 (Visa); PC MASTRCRD, AMEX CARDS → 5750
        """
        if "TD VISA" in desc or "SCOTIA VISA" in desc:
            return (5700, "Visa", Decimal("98"))
        if "PC MASTRCRD" in desc:
            return (5750, "Mastercard", Decimal("98"))
        if "AMEX CARDS" in desc:
            return (5725, "AMEX", Decimal("98"))
        return None
    
    # ========== RULE 5: Payroll Tax ==========
    def _rule_payroll_tax(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """RECEIVER GENERAL / CRA → 2100 Employee Tax Deductions (LIABILITY), 95% confidence"""
        if "RECEIVER GENERAL" in desc or "CRA" in desc:
            return (2100, "Employee Tax Deductions", Decimal("95"))
        return None
    
    # ========== RULE 6: Payroll/Wages ==========
    def _rule_payroll_wages(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """CONCETTA BOSH / CHRISTINA BOSH → 5850 Wages & Benefits, 85% confidence"""
        if "CONCETTA BOSH" in desc or "CHRISTINA BOSH" in desc:
            return (5850, "Wages & Benefits", Decimal("85"))
        return None
    
    # ========== RULE 7: Car Lease ==========
    def _rule_car_lease(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """SPL LOAN → 5155 Car Lease, 90% confidence"""
        if "SPL LOAN" in desc:
            return (5155, "Car Lease", Decimal("90"))
        return None
    
    # ========== RULE 8: Rent ==========
    def _rule_rent(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """RENT → 5800 Rent, 85% confidence"""
        if "RENT" in desc:
            return (5800, "Rent", Decimal("85"))
        return None
    
    # ========== RULE 9: Catch-all Suspense ==========
    def _rule_suspense_fallback(self, desc: str, amount: Decimal) -> Optional[Tuple[int, str, Decimal]]:
        """Fallback: no rule matched → 5900 Suspense, 0% confidence (manual review)"""
        return (5900, "Suspense", Decimal("0"))


# Module-level function for easy access
def categorize_concetta(description: str, amount: Decimal) -> Tuple[int, str, Decimal]:
    """
    Quick function to categorize a single transaction using Concetta's ruleset.
    
    Usage:
        account_num, account_name, confidence = categorize_concetta("ECONOMICAL INS", Decimal("-182.41"))
    """
    ruleset = ConcettaRuleset()
    return ruleset.categorize(description, amount)